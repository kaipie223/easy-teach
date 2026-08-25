"""Knowledge-base import, evidence persistence and Chroma indexing."""

from __future__ import annotations

import mimetypes
import uuid
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
    detect_file_type,
    parse_material,
)
from backend.services.rag import reset_retriever


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


def detect_knowledge_file_type(filename: str, content: bytes) -> tuple[str, str]:
    """Validate supported knowledge-base files, including plain text."""
    extension = Path(filename).suffix.lower()
    if extension in TEXT_EXTENSIONS:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeValidationError(
                "文本文件必须使用 UTF-8 编码",
                code="KNOWLEDGE_TEXT_ENCODING_INVALID",
            ) from exc
        return "text", mimetypes.guess_type(filename)[0] or "text/plain"

    try:
        return detect_file_type(filename, content)
    except MaterialValidationError as exc:
        raise KnowledgeValidationError(str(exc), code=exc.code, details=exc.details) from exc


def _parse_text(content: bytes) -> ParsedMaterial:
    text = content.decode("utf-8").strip()
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


def _parse_knowledge(file_type: str, path: Path, content: bytes) -> ParsedMaterial:
    if file_type == "text":
        return _parse_text(content)
    return parse_material(file_type, path)


def _next_version(db: DBSession, collection_id: str, title: str) -> int:
    latest = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.collection_id == collection_id,
            KnowledgeDocument.title == title,
        )
        .order_by(KnowledgeDocument.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def _relative_source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(settings.knowledge_base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def import_document(
    db: DBSession,
    *,
    owner_id: str,
    collection_id: str,
    title: str,
    filename: str,
    content: bytes,
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
    file_type, mime_type = detect_knowledge_file_type(safe_name, content)
    checksum = checksum_sha256(content)
    duplicate = (
        db.query(KnowledgeDocument)
        .filter(
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

    document_id = f"kb_{uuid.uuid4().hex[:8]}"
    stored_path = settings.knowledge_base_dir / ".managed" / document_id / safe_name
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(content)

    now = _now()
    document = KnowledgeDocument(
        document_id=document_id,
        owner_id=owner_id,
        collection_id=collection_id,
        title=normalized_title,
        source_path=_relative_source_path(stored_path),
        file_type=file_type,
        version=_next_version(db, collection_id, normalized_title),
        checksum_sha256=checksum,
        enabled=enabled,
        index_status="pending",
        metadata_json={"mime_type": mime_type},
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()

    try:
        parsed = _parse_knowledge(file_type, stored_path, content)
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
        "chunk_count": len(parsed.chunks),
    }
    for index, chunk in enumerate(parsed.chunks):
        db.add(
            EvidenceChunk(
                evidence_id=f"evidence_{uuid.uuid4().hex[:8]}",
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


def rebuild_index(db: DBSession) -> dict[str, Any]:
    """Rebuild the single Chroma collection from enabled DB documents."""
    documents = (
        db.query(KnowledgeDocument)
        .filter(
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
        with _INDEX_REBUILD_LOCK:
            # Drop the process cache before replacing the persisted collection.
            reset_retriever()
            build_index(chunks, settings.chroma_persist_dir)
    except Exception as exc:
        reset_retriever()
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
        document.index_namespace = "knowledge_base"
        document.indexed_at = indexed_at
        document.metadata_json = {
            **(document.metadata_json or {}),
            "chunk_count": chunks_by_document[document.document_id],
        }
        document.updated_at = indexed_at
    db.commit()
    reset_retriever()
    return {
        "status": "ready",
        "indexed_document_ids": document_ids,
        "chunk_count": len(chunks),
    }
