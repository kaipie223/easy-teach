"""Administrator knowledge-base management and teacher retrieval APIs."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.security import get_current_user, require_admin
from backend.db.database import get_db
from backend.models.knowledge import KnowledgeDocument
from backend.models.user import User
from backend.schemas import (
    KnowledgeDocumentInfo,
    KnowledgeDocumentUpdate,
    KnowledgeIndexResponse,
    KnowledgeSearchRequest,
    RAGDocument,
)
from backend.services.knowledge import (
    KnowledgeIndexError,
    KnowledgeValidationError,
    import_document,
    invalidate_document_evidence,
    rebuild_index,
)
from backend.services.rag import search as rag_search

router = APIRouter()


def _document_info(document: KnowledgeDocument) -> KnowledgeDocumentInfo:
    payload = KnowledgeDocumentInfo.model_validate(document).model_dump()
    payload["chunk_count"] = int((document.metadata_json or {}).get("chunk_count", 0))
    return KnowledgeDocumentInfo.model_validate(payload)


def _get_document(db: DBSession, document_id: str) -> KnowledgeDocument:
    document = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.document_id == document_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if document is None:
        raise ApiError("知识库文档不存在", code="KNOWLEDGE_DOCUMENT_NOT_FOUND", status_code=404)
    return document


@router.get("/documents", response_model=list[KnowledgeDocumentInfo])
def list_documents(
    collection_id: str | None = Query(default=None, max_length=128),
    include_deleted: bool = Query(default=False),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(KnowledgeDocument)
    if collection_id:
        query = query.filter(KnowledgeDocument.collection_id == collection_id)
    if user.role != "admin":
        query = query.filter(
            KnowledgeDocument.enabled.is_(True),
            KnowledgeDocument.index_status == "ready",
            KnowledgeDocument.deleted_at.is_(None),
        )
    elif not include_deleted:
        query = query.filter(KnowledgeDocument.deleted_at.is_(None))
    documents = query.order_by(KnowledgeDocument.updated_at.desc()).all()
    return [_document_info(document) for document in documents]


@router.post("/documents", response_model=KnowledgeDocumentInfo, status_code=201)
async def create_document(
    file: UploadFile = File(...),
    collection_id: str = Form(default="default"),
    title: str = Form(default=""),
    enabled: bool = Form(default=False),
    db: DBSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not file.filename:
        raise ApiError("文件名为空", code="INVALID_KNOWLEDGE_NAME", status_code=400)
    content = await file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise ApiError(
            f"文件超过 {settings.max_upload_size_mb} MB 限制",
            code="KNOWLEDGE_TOO_LARGE",
            status_code=413,
        )
    try:
        document = import_document(
            db,
            owner_id=admin.user_id,
            collection_id=collection_id,
            title=title,
            filename=file.filename,
            content=content,
            enabled=enabled,
        )
    except KnowledgeValidationError as exc:
        status_code = 409 if exc.code == "KNOWLEDGE_DOCUMENT_EXISTS" else 422
        raise ApiError(
            str(exc),
            code=exc.code,
            status_code=status_code,
            details=exc.details,
        ) from exc
    return _document_info(document)


@router.patch("/documents/{document_id}", response_model=KnowledgeDocumentInfo)
def update_document(
    document_id: str,
    request: KnowledgeDocumentUpdate,
    db: DBSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    document = _get_document(db, document_id)
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError("没有需要更新的字段", code="NO_KNOWLEDGE_CHANGES", status_code=422)
    if request.title is not None:
        normalized_title = request.title.strip()
        if not normalized_title:
            raise ApiError("知识库文档标题不能为空", code="INVALID_KNOWLEDGE_TITLE", status_code=422)
        document.title = normalized_title
    if request.enabled is not None and request.enabled != document.enabled:
        document.enabled = request.enabled
        document.index_status = "pending"
        document.indexed_at = None
        document.index_namespace = None
    document.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(document)
    return _document_info(document)


@router.delete("/documents/{document_id}", response_model=KnowledgeDocumentInfo)
def delete_document(
    document_id: str,
    db: DBSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    document = _get_document(db, document_id)
    now = datetime.now(timezone.utc)
    document.deleted_at = now
    document.enabled = False
    document.index_status = "pending"
    document.indexed_at = None
    document.updated_at = now
    invalidate_document_evidence(db, document.document_id, reason="document_deleted")
    db.commit()
    db.refresh(document)
    return _document_info(document)


@router.post("/index", response_model=KnowledgeIndexResponse)
def index_documents(
    db: DBSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        return KnowledgeIndexResponse.model_validate(rebuild_index(db))
    except KnowledgeIndexError as exc:
        raise ApiError(
            str(exc),
            code="KNOWLEDGE_INDEX_FAILED",
            status_code=503,
            details=exc.details,
            suggested_action="检查向量模型和网络配置后重试索引",
        ) from exc


@router.post("/documents/{document_id}/index", response_model=KnowledgeIndexResponse)
def index_document(
    document_id: str,
    db: DBSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    document = _get_document(db, document_id)
    if not document.enabled:
        raise ApiError(
            "请先启用该知识库文档",
            code="KNOWLEDGE_DOCUMENT_DISABLED",
            status_code=409,
        )
    try:
        return KnowledgeIndexResponse.model_validate(rebuild_index(db))
    except KnowledgeIndexError as exc:
        raise ApiError(
            str(exc),
            code="KNOWLEDGE_INDEX_FAILED",
            status_code=503,
            details={**exc.details, "document_id": document_id},
            suggested_action="检查向量模型和网络配置后重试索引",
        ) from exc


@router.post("/search", response_model=list[RAGDocument])
async def search_knowledge(
    request: KnowledgeSearchRequest,
    db: DBSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    results = await rag_search(request.query.strip(), request.top_k)
    ready_document_ids = {
        document_id
        for (document_id,) in db.query(KnowledgeDocument.document_id)
        .filter(
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.enabled.is_(True),
            KnowledgeDocument.index_status == "ready",
        )
        .all()
    }
    # Legacy Chroma entries without a managed document ID remain searchable;
    # managed entries must still pass the current database state filter.
    return [
        result
        for result in results
        if result.document_id is None or result.document_id in ready_document_ids
    ]
