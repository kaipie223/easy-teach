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
    KnowledgeIndexJobInfo,
    KnowledgeIndexProgress,
    KnowledgeIndexStatus,
    KnowledgeSearchRequest,
    RAGDocument,
)
from backend.services.knowledge import (
    KnowledgeValidationError,
    import_document,
    index_progress,
    invalidate_document_evidence,
    start_rebuild,
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
    metadata = document.metadata_json or {}
    payload["chunk_count"] = int(metadata.get("chunk_count", 0) or 0)
    payload["page_count"] = int(metadata.get("page_count", 0) or 0)
    payload["text_pages"] = int(metadata.get("text_pages", 0) or 0)
    pages = payload["page_count"]
    payload["text_coverage"] = round(payload["text_pages"] / pages, 4) if pages else 0.0
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
        if normalized_title != document.title:
            document.title = normalized_title
            # 集合格子里存的 source 是旧标题。不把这一本标回待索引，检索结果会一直
            # 显示改名前的名称。只在 ready 时才重置：pending/failed 的文档下次索引
            # 本来就会重做，没必要多写一次库。
            if document.index_status == "ready":
                document.index_status = "pending"
                document.indexed_at = None
                document.index_namespace = None
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


@router.get("/index/progress", response_model=KnowledgeIndexProgress)
def get_index_progress(
    db: DBSession = Depends(get_db),
    teacher: User = Depends(require_teacher),
):
    """当前索引进度：正在处理的文件、块进度与整体进度。

    重建跑在后台线程里（启动接口只负责启动），所以进度必须能单独查询：
    界面上"正在处理哪一本、写到第几块"就是靠轮询这个接口拿到的。状态从文档字段推导，
    所以刷新页面甚至重启进程后读到的仍是同一条进度。
    """
    return KnowledgeIndexProgress.model_validate(
        index_progress(db, owner_id=teacher.user_id)
    )


@router.post("/index", response_model=KnowledgeIndexJobInfo, status_code=202)
def index_documents(
    force: bool = Query(default=False),
    db: DBSession = Depends(get_db),
    teacher: User = Depends(require_teacher),
):
    """启动索引，立刻返回；进度与结果都看 ``/knowledge/index/progress``。

    默认是**增量**：只处理未就绪 / 不在当前集合里的文档，顺带清掉停用、删除文档遗留的
    块；全部就绪时是空操作，响应里 ``work_pending=false``。``force=true`` 退回全量重建
    （staged collection + 原子切指针），供脚本与运维使用——换嵌入模型、怀疑索引损坏时
    才需要。

    索引发分钟级，过去这个接口会一直等它跑完，于是请求总是先被客户端掐断（见
    KnowledgeIndexJobInfo）。现在请求只负责启动后台线程，失败会落成文档的
    ``index_status=failed`` 与 ``error_message``，界面据此显示"失败，需重试"。
    """
    started, work_pending = start_rebuild(db, owner_id=teacher.user_id, force=force)
    return KnowledgeIndexJobInfo(
        status=KnowledgeIndexStatus.INDEXING,
        started=started,
        work_pending=work_pending,
    )


@router.post(
    "/documents/{document_id}/index",
    response_model=KnowledgeIndexJobInfo,
    status_code=202,
)
def index_document(
    document_id: str,
    db: DBSession = Depends(get_db),
    teacher: User = Depends(require_teacher),
):
    """单本文档的"重新索引"：只重做这一本，其它已建好的索引不受影响。"""
    document = _get_document(db, document_id, teacher.user_id)
    if not document.enabled:
        raise ApiError(
            "请先启用该知识库文档",
            code="KNOWLEDGE_DOCUMENT_DISABLED",
            status_code=409,
        )
    if document.error_code == "KNOWLEDGE_PARSE_FAILED":
        # 解析失败的文档没有证据块，重索引只会空转然后再次失败：直接把原因说清楚，
        # 而不是让教师盯着一个秒完成的"成功"发懵。
        raise ApiError(
            "这份文档解析失败（常见于纯图片扫描件），无法建立索引，请先转成可提取文字的版本再重新上传",
            code="KNOWLEDGE_DOCUMENT_UNPARSABLE",
            status_code=409,
            details={"error_message": document.error_message or ""},
        )
    started, work_pending = start_rebuild(
        db,
        owner_id=teacher.user_id,
        document_ids=[document.document_id],
    )
    return KnowledgeIndexJobInfo(
        status=KnowledgeIndexStatus.INDEXING,
        started=started,
        work_pending=work_pending,
    )


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
