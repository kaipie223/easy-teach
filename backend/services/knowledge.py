"""Knowledge-base import, evidence persistence and Chroma indexing."""

from __future__ import annotations

import mimetypes
import codecs
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from ai.rag.embedder import build_index
from backend.config import settings
from backend.models.knowledge import KnowledgeDocument
from backend.models.material import EvidenceChunk
from backend.services.materials import (
    MAX_CHUNK_CHARS,
    ParsedChunk,
    ParsedMaterial,
    MaterialValidationError,
    checksum_sha256,
    detect_file_type_from_path,
    parse_material,
)
from backend.services.knowledge_scope import (
    activate_knowledge_collection,
    knowledge_storage_dir,
    staged_knowledge_collection_name,
)
from backend.services.rag import reset_retriever


logger = logging.getLogger(__name__)


TEXT_EXTENSIONS = {".txt", ".md"}
_INDEX_REBUILD_LOCK = Lock()


class KnowledgeValidationError(ValueError):
    """Raised when a knowledge-base import is invalid."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class KnowledgeIndexError(RuntimeError):
    """Raised when rebuilding the vector index fails."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


@contextmanager
def _knowledge_index_lock(owner_id: str):
    """Use Redis in production and a process lock in development/tests."""
    if settings.environment.lower() not in {"production", "prod"}:
        with _INDEX_REBUILD_LOCK:
            yield
        return

    import redis

    client = redis.Redis.from_url(settings.redis_url)
    lock = client.lock(
        f"easy-teach:knowledge-index:{owner_id}",
        timeout=max(settings.task_time_limit_seconds, 60),
        blocking_timeout=5,
    )
    acquired = lock.acquire(blocking=True)
    if not acquired:
        raise KnowledgeIndexError(
            "该知识库正在重建索引",
            details={"owner_id": owner_id},
        )
    try:
        yield
    finally:
        lock.release()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(filename: str) -> str:
    safe_name = Path(filename).name.strip()
    if not safe_name or safe_name in {".", ".."}:
        raise KnowledgeValidationError(
            "文件名为空",
            code="INVALID_KNOWLEDGE_NAME",
        )
    return safe_name


def detect_knowledge_file_type(filename: str, path: Path) -> tuple[str, str]:
    """Validate supported knowledge-base files, including plain text."""
    extension = Path(filename).suffix.lower()
    if extension in TEXT_EXTENSIONS:
        try:
            decoder = codecs.getincrementaldecoder("utf-8")()
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    decoder.decode(chunk)
                decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise KnowledgeValidationError(
                "文本文件必须使用 UTF-8 编码",
                code="KNOWLEDGE_TEXT_ENCODING_INVALID",
            ) from exc
        return "text", mimetypes.guess_type(filename)[0] or "text/plain"

    try:
        return detect_file_type_from_path(filename, path)
    except MaterialValidationError as exc:
        raise KnowledgeValidationError(str(exc), code=exc.code, details=exc.details) from exc


def _parse_text(path: Path) -> ParsedMaterial:
    text = path.read_text(encoding="utf-8").strip()
    chunks: list[ParsedChunk] = []
    for offset in range(0, len(text), MAX_CHUNK_CHARS):
        part = text[offset : offset + MAX_CHUNK_CHARS].strip()
        if not part:
            continue
        chunks.append(
            ParsedChunk(
                text=part,
                locator={"offset": offset},
                metadata={"format": "text"},
            )
        )
    return ParsedMaterial(
        text_content=text,
        chunks=chunks,
        result_json={"format": "text", "chunk_count": len(chunks)},
    )


def _parse_knowledge(file_type: str, path: Path) -> ParsedMaterial:
    if file_type == "text":
        return _parse_text(path)
    return parse_material(file_type, path)


def _next_version(db: DBSession, owner_id: str, collection_id: str, title: str) -> int:
    latest = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.owner_id == owner_id,
            KnowledgeDocument.collection_id == collection_id,
            KnowledgeDocument.title == title,
        )
        .order_by(KnowledgeDocument.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def _relative_source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(settings.data_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def import_document(
    db: DBSession,
    *,
    owner_id: str,
    collection_id: str,
    title: str,
    filename: str,
    staged_path: Path,
    size_bytes: int,
    checksum: str,
    enabled: bool = False,
) -> KnowledgeDocument:
    """Persist a managed document and its immutable evidence chunks."""
    collection_id = collection_id.strip()
    if not collection_id or len(collection_id) > 128:
        raise KnowledgeValidationError(
            "知识库集合名称无效",
            code="INVALID_KNOWLEDGE_COLLECTION",
        )

    safe_name = _safe_filename(filename)
    normalized_title = title.strip() or Path(safe_name).stem
    file_type, mime_type = detect_knowledge_file_type(safe_name, staged_path)
    duplicate = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.owner_id == owner_id,
            KnowledgeDocument.collection_id == collection_id,
            KnowledgeDocument.checksum_sha256 == checksum,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if duplicate is not None:
        raise KnowledgeValidationError(
            "相同文件已经导入到该知识库",
            code="KNOWLEDGE_DOCUMENT_EXISTS",
            details={"document_id": duplicate.document_id},
        )

    document_id = f"kb_{uuid.uuid4().hex[:24]}"
    stored_path = knowledge_storage_dir(owner_id) / document_id / safe_name
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.replace(stored_path)

    now = _now()
    document = KnowledgeDocument(
        document_id=document_id,
        owner_id=owner_id,
        collection_id=collection_id,
        title=normalized_title,
        source_path=_relative_source_path(stored_path),
        file_type=file_type,
        version=_next_version(db, owner_id, collection_id, normalized_title),
        checksum_sha256=checksum,
        enabled=enabled,
        index_status="pending",
        metadata_json={"mime_type": mime_type, "size_bytes": size_bytes},
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()

    try:
        parsed = _parse_knowledge(file_type, stored_path)
    except Exception as exc:
        document.index_status = "failed"
        document.error_code = "KNOWLEDGE_PARSE_FAILED"
        document.error_message = str(exc)
        document.updated_at = _now()
        db.commit()
        db.refresh(document)
        return document

    document.metadata_json = {
        **(parsed.result_json or {}),
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "chunk_count": len(parsed.chunks),
    }
    for index, chunk in enumerate(parsed.chunks):
        db.add(
            EvidenceChunk(
                evidence_id=f"evidence_{uuid.uuid4().hex[:24]}",
                knowledge_document_id=document.document_id,
                source_type=f"knowledge_{file_type}",
                chunk_index=index,
                locator_json=chunk.locator,
                text=chunk.text,
                metadata_json={**chunk.metadata, "source_path": _relative_source_path(stored_path)},
                usage_tags=[],
                content_hash=checksum_sha256(chunk.text.encode("utf-8")),
                is_valid=True,
                created_at=now,
            )
        )
    document.updated_at = now
    db.commit()
    db.refresh(document)
    return document


def invalidate_document_evidence(
    db: DBSession,
    document_id: str,
    *,
    reason: str,
) -> None:
    now = _now()
    (
        db.query(EvidenceChunk)
        .filter(
            EvidenceChunk.knowledge_document_id == document_id,
            EvidenceChunk.is_valid.is_(True),
        )
        .update(
            {
                "is_valid": False,
                "invalidated_at": now,
                "invalidation_reason": reason,
            },
            synchronize_session=False,
        )
    )


def rebuild_index(db: DBSession, *, owner_id: str) -> dict[str, Any]:
    """Rebuild one teacher's private Chroma collection."""
    collection_name = staged_knowledge_collection_name(owner_id)
    documents = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.owner_id == owner_id,
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.enabled.is_(True),
            or_(
                KnowledgeDocument.index_status != "failed",
                KnowledgeDocument.error_code == "KNOWLEDGE_INDEX_FAILED",
            ),
        )
        .order_by(KnowledgeDocument.created_at.asc())
        .all()
    )
    document_ids = [document.document_id for document in documents]
    now = _now()
    for document in documents:
        document.index_status = "indexing"
        document.error_code = None
        document.error_message = None
        document.updated_at = now
    db.commit()

    evidence_rows = []
    if document_ids:
        evidence_rows = (
            db.query(EvidenceChunk)
            .filter(
                EvidenceChunk.knowledge_document_id.in_(document_ids),
                EvidenceChunk.is_valid.is_(True),
            )
            .order_by(EvidenceChunk.created_at.asc(), EvidenceChunk.chunk_index.asc())
            .all()
        )
    title_by_id = {document.document_id: document.title for document in documents}
    chunks = []
    chunks_by_document: dict[str, int] = {document_id: 0 for document_id in document_ids}
    for evidence in evidence_rows:
        document_id = evidence.knowledge_document_id
        if document_id not in title_by_id:
            continue
        locator = evidence.locator_json or {}
        metadata: dict[str, Any] = {
            "document_id": document_id,
            "evidence_id": evidence.evidence_id,
            "source_path": evidence.metadata_json.get("source_path", "")
            if evidence.metadata_json
            else "",
        }
        for key, value in locator.items():
            if value is not None and isinstance(value, (str, int, float, bool)):
                metadata[f"locator_{key}"] = value
        chunks.append(
            {
                "content": evidence.text,
                "source": title_by_id[document_id],
                "chunk_index": evidence.chunk_index,
                "metadata": metadata,
            }
        )
        chunks_by_document[document_id] += 1

    try:
        with _knowledge_index_lock(owner_id):
            build_index(
                chunks,
                settings.chroma_persist_dir,
                collection_name=collection_name,
            )
            activate_knowledge_collection(owner_id, collection_name)
            reset_retriever(owner_id)
    except Exception as exc:
        logger.exception("Knowledge index rebuild failed for owner %s", owner_id)
        reset_retriever(owner_id)
        failed_at = _now()
        for document in documents:
            document.index_status = "failed"
            document.error_code = "KNOWLEDGE_INDEX_FAILED"
            document.error_message = str(exc)
            document.updated_at = failed_at
        db.commit()
        raise KnowledgeIndexError(
            "知识库向量索引失败",
            details={"document_ids": document_ids},
        ) from exc

    indexed_at = _now()
    for document in documents:
        document.index_status = "ready"
        document.index_namespace = collection_name
        document.indexed_at = indexed_at
        document.metadata_json = {
            **(document.metadata_json or {}),
            "chunk_count": chunks_by_document[document.document_id],
        }
        document.updated_at = indexed_at
    db.commit()
    reset_retriever(owner_id)
    return {
        "status": "ready",
        "indexed_document_ids": document_ids,
        "chunk_count": len(chunks),
    }
