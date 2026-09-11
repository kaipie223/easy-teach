"""Teacher-private knowledge-base management and retrieval APIs."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.security import require_teacher
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
from backend.services.limits import remaining_storage_bytes
from backend.services.uploads import (
    UploadSizeExceeded,
    remove_managed_file,
    remove_staged_upload,
    stream_upload_to_path,
)

router = APIRouter()


def _document_info(document: KnowledgeDocument) -> KnowledgeDocumentInfo:
    payload = KnowledgeDocumentInfo.model_validate(document).model_dump()
    payload["chunk_count"] = int((document.metadata_json or {}).get("chunk_count", 0))
    return KnowledgeDocumentInfo.model_validate(payload)


def _get_document(db: DBSession, document_id: str, owner_id: str) -> KnowledgeDocument:
    document = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.document_id == document_id,
            KnowledgeDocument.owner_id == owner_id,
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
    teacher: User = Depends(require_teacher),
):
    query = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.owner_id == teacher.user_id,
    )
    if collection_id:
        query = query.filter(KnowledgeDocument.collection_id == collection_id)
    if not include_deleted:
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
    teacher: User = Depends(require_teacher),
):
    if not file.filename:
        raise ApiError("文件名为空", code="INVALID_KNOWLEDGE_NAME", status_code=400)
    safe_name = Path(file.filename).name
    staged_path = settings.data_dir / ".upload_tmp" / f"kb_{uuid.uuid4().hex}" / safe_name
    max_file_bytes = settings.max_upload_size_mb * 1024 * 1024
    remaining_bytes = remaining_storage_bytes(db, teacher.user_id)
    if remaining_bytes <= 0:
        raise ApiError(
            "个人存储空间不足",
            code="STORAGE_QUOTA_EXCEEDED",
            status_code=413,
            suggested_action="请删除不再需要的知识库文档后重试",
        )
    try:
        stored = await stream_upload_to_path(
            file,
            staged_path,
            max_bytes=min(max_file_bytes, remaining_bytes),
        )
    except UploadSizeExceeded as exc:
        quota_limited = remaining_bytes < max_file_bytes
        raise ApiError(
            "个人存储空间不足" if quota_limited else f"文件超过 {settings.max_upload_size_mb} MB 限制",
            code="STORAGE_QUOTA_EXCEEDED" if quota_limited else "KNOWLEDGE_TOO_LARGE",
            status_code=413,
            details={"max_bytes": exc.max_bytes, "actual_bytes": exc.actual_bytes},
        ) from exc
    try:
        document = import_document(
            db,
            owner_id=teacher.user_id,
            collection_id=collection_id,
            title=title,
            filename=safe_name,
            staged_path=staged_path,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum_sha256,
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
    finally:
        remove_staged_upload(staged_path)
    return _document_info(document)


@router.patch("/documents/{document_id}", response_model=KnowledgeDocumentInfo)
def update_document(
    document_id: str,
    request: KnowledgeDocumentUpdate,
    db: DBSession = Depends(get_db),
    teacher: User = Depends(require_teacher),
):
    document = _get_document(db, document_id, teacher.user_id)
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
    teacher: User = Depends(require_teacher),
):
    document = _get_document(db, document_id, teacher.user_id)
    source_path = Path(document.source_path)
    stored_path = source_path if source_path.is_absolute() else settings.data_dir / source_path
    try:
        remove_managed_file(stored_path, root=settings.data_dir / "knowledge")
    except ValueError as exc:
        raise ApiError(
            "知识库文件存储路径异常，拒绝删除",
            code="KNOWLEDGE_STORAGE_PATH_INVALID",
            status_code=500,
        ) from exc
    now = datetime.now(timezone.utc)
    document.deleted_at = now
    document.enabled = False
    document.index_status = "pending"
    document.indexed_at = None
    document.updated_at = now
    document.metadata_json = {**(document.metadata_json or {}), "size_bytes": 0}
    invalidate_document_evidence(db, document.document_id, reason="document_deleted")
    db.commit()
    db.refresh(document)
    return _document_info(document)


@router.post("/index", response_model=KnowledgeIndexResponse)
def index_documents(
    db: DBSession = Depends(get_db),
    teacher: User = Depends(require_teacher),
):
    try:
        return KnowledgeIndexResponse.model_validate(
            rebuild_index(db, owner_id=teacher.user_id)
        )
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
    teacher: User = Depends(require_teacher),
):
    document = _get_document(db, document_id, teacher.user_id)
    if not document.enabled:
        raise ApiError(
            "请先启用该知识库文档",
            code="KNOWLEDGE_DOCUMENT_DISABLED",
            status_code=409,
        )
    try:
        return KnowledgeIndexResponse.model_validate(
            rebuild_index(db, owner_id=teacher.user_id)
        )
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
    teacher: User = Depends(require_teacher),
):
    results = await rag_search(
        request.query.strip(),
        request.top_k,
        owner_id=teacher.user_id,
    )
    ready_document_ids = {
        document_id
        for (document_id,) in db.query(KnowledgeDocument.document_id)
        .filter(
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.owner_id == teacher.user_id,
            KnowledgeDocument.enabled.is_(True),
            KnowledgeDocument.index_status == "ready",
        )
        .all()
    }
    return [
        result
        for result in results
        if result.document_id in ready_document_ids
    ]
