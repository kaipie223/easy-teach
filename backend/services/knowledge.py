"""Knowledge-base import, evidence persistence and Chroma indexing."""

from __future__ import annotations

import mimetypes
import codecs
import logging
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from ai.rag.embedder import (
    add_index,
    build_index,
    collection_stats,
    remove_document_chunks,
)
from backend.config import settings
from backend.models.knowledge import KnowledgeDocument
from backend.models.material import EvidenceChunk
from backend.services.materials import (
    ParsedChunk,
    ParsedMaterial,
    MaterialValidationError,
    checksum_sha256,
    detect_file_type_from_path,
    parse_material,
)
from backend.services.knowledge_scope import (
    activate_knowledge_collection,
    active_knowledge_collection_name,
    knowledge_storage_dir,
    staged_knowledge_collection_name,
)
from backend.services.rag import reset_retriever, warm_retriever


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


_PAGE_MARKER = re.compile(
    r"^[ \t]*\[\[page[ \t]+(\d+)\]\][ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _split_pages(text: str) -> list[tuple[int | None, str]]:
    """把带 ``[[page N]]`` 标记的文本拆成 (页码, 正文)。

    没有标记时返回 ``[(None, 全文)]``，调用方沿用按字符偏移切块的老行为 —— 扫描件
    OCR 出来的文本才带标记（见 scripts/ocr_textbooks.py），目的是让引用同样能显示
    "第 N 页"，而不是一个对老师没意义的字符偏移。
    """
    markers = list(_PAGE_MARKER.finditer(text))
    if not markers:
        return [(None, text)]
    pages: list[tuple[int | None, str]] = []
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        pages.append((int(marker.group(1)), text[start:end]))
    return pages


def _parse_text(path: Path, *, max_chars: int) -> ParsedMaterial:
    text = path.read_text(encoding="utf-8").strip()
    chunks: list[ParsedChunk] = []
    page_total = 0
    text_pages = 0
    for page_number, page_text in _split_pages(text):
        normalized = page_text.strip()
        if page_number is None:
            for offset in range(0, len(normalized), max_chars):
                part = normalized[offset : offset + max_chars].strip()
                if not part:
                    continue
                chunks.append(
                    ParsedChunk(
                        text=part,
                        locator={"offset": offset},
                        metadata={"format": "text"},
                    )
                )
            continue
        page_total = max(page_total, page_number)
        if not normalized:
            continue
        text_pages += 1
        parts = [normalized[index : index + max_chars] for index in range(0, len(normalized), max_chars)]
        for index, part in enumerate(parts):
            locator: dict[str, Any] = {"page": page_number}
            if len(parts) > 1:
                locator["part"] = index + 1
            chunks.append(
                ParsedChunk(
                    text=part.strip(),
                    locator=locator,
                    metadata={"format": "text"},
                )
            )

    result_json: dict[str, Any] = {"format": "text", "chunk_count": len(chunks)}
    if page_total:
        # 带页码标记的文本也报页数与抽字率：界面（库概览、每本体检）对 OCR 版
        # 教材就和 PDF 一个口径，不用另做一套。
        result_json.update({"page_count": page_total, "text_pages": text_pages})
    return ParsedMaterial(text_content=text, chunks=chunks, result_json=result_json)


def _parse_knowledge(file_type: str, path: Path) -> ParsedMaterial:
    """知识库用自己的分块上限：这些块要被嵌入，必须落在向量模型的输入窗口内。

    资料中心仍用 materials.MAX_CHUNK_CHARS（1200）—— 那边的块只作为文本进提示词，
    不进向量库，块大一些反而少丢上下文。
    """
    max_chars = settings.knowledge_chunk_chars
    if file_type == "text":
        return _parse_text(path, max_chars=max_chars)
    return parse_material(file_type, path, max_chars=max_chars)


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


def _owner_documents(db: DBSession, *, owner_id: str) -> list[KnowledgeDocument]:
    """该账号的全部文档（含已停用、已删除），按导入顺序。"""
    return (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.owner_id == owner_id)
        .order_by(KnowledgeDocument.created_at.asc())
        .all()
    )


def _is_index_candidate(document: KnowledgeDocument) -> bool:
    """这本文档能不能进索引：启用、未删除，且不是"永久失败"。

    索引、进度查询、增量判定共用这一份筛选。几处各写一遍必然漂移，然后界面上就会
    出现"总数 12 本、进度跑到 10 本就说完成了"这种对不上的数字。
    """
    if document.deleted_at is not None or not document.enabled:
        return False
    # 解析失败在导入时就定性了，重索引多少次也不会变；可重试的嵌入失败
    # （KNOWLEDGE_INDEX_FAILED）保留在候选里。
    return document.index_status != "failed" or document.error_code == "KNOWLEDGE_INDEX_FAILED"


def _rebuild_candidates(db: DBSession, *, owner_id: str) -> list[KnowledgeDocument]:
    return [
        document
        for document in _owner_documents(db, owner_id=owner_id)
        if _is_index_candidate(document)
    ]


def _has_indexed_chunks(document: KnowledgeDocument) -> bool:
    """这份文档是否写出过块（含已停用/已删除的）：增量运行要把这些块清出集合。"""
    return bool((document.metadata_json or {}).get("chunk_count"))


def _chunk_metadata(document_id: str, evidence: EvidenceChunk) -> dict[str, Any]:
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
    return metadata


@dataclass(frozen=True)
class _IndexPlan:
    """一次索引任务的计划：写哪几本、清哪几本、写到哪个集合。

    ``_queue_rebuild``（请求线程里立刻让进度可见）与 ``rebuild_index``（后台线程真正
    干活）共用同一份计划。两处各判一次必然漂移，界面上就会出现"进度说要处理 3 本、
    实际只嵌了 1 本"。
    """

    mode: str  # "staged" | "incremental" | "noop"
    collection_name: str
    to_index_ids: list[str]
    to_remove_ids: list[str]


def _resolve_index_target(
    db: DBSession,
    *,
    owner_id: str,
    document_ids: Sequence[str] | None = None,
    force: bool = False,
) -> _IndexPlan:
    """决定这次索引任务做什么。

    - 首次建库、active 集合被人删空、以及显式 ``force`` → 全量重建（staged 模式）：
      这些场景没有可复用的旧块，而且全量路径天然免疫"换过嵌入模型、维度对不上"。
    - 其余情况走增量：只处理未就绪、或不在当前 active 集合里的文档；顺带把停用/删除
      文档遗留的块清出去。已经就绪且就在当前集合里的文档一个字节都不动——这正是
      "重复点击几乎不花时间"的来源。
    - 给了 ``document_ids``（单本"重新索引"）时只处理这几本，即使它已经就绪；但集合
      不可复用时只能退回全量（没有旧集合可以承接增量）。
    """
    documents = _owner_documents(db, owner_id=owner_id)
    candidates = [document for document in documents if _is_index_candidate(document)]
    active_name = active_knowledge_collection_name(owner_id)
    collection_exists, existing_chunks = collection_stats(
        settings.chroma_persist_dir, active_name
    )

    if force or not collection_exists or existing_chunks == 0:
        if document_ids:
            logger.info(
                "集合 %s 不存在或为空（或显式 force），本次退回全量重建", active_name
            )
        return _IndexPlan(
            mode="staged",
            collection_name=staged_knowledge_collection_name(owner_id),
            to_index_ids=[document.document_id for document in candidates],
            to_remove_ids=[],
        )

    if document_ids:
        wanted = set(document_ids)
        to_index = [document for document in candidates if document.document_id in wanted]
    else:
        to_index = [
            document
            for document in candidates
            if document.index_status != "ready" or document.index_namespace != active_name
        ]

    to_remove_ids = [
        document.document_id
        for document in documents
        if not _is_index_candidate(document) and _has_indexed_chunks(document)
    ]

    if not to_index and not to_remove_ids:
        return _IndexPlan(
            mode="noop",
            collection_name=active_name,
            to_index_ids=[],
            to_remove_ids=[],
        )

    return _IndexPlan(
        mode="incremental",
        collection_name=active_name,
        to_index_ids=[document.document_id for document in to_index],
        to_remove_ids=to_remove_ids,
    )


def rebuild_index(
    db: DBSession,
    *,
    owner_id: str,
    plan: _IndexPlan | None = None,
    document_ids: Sequence[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """执行一次索引任务（计划见 ``_resolve_index_target``），返回执行结果。

    - ``staged``：全量重建，写入新的暂存 collection 后原子切指针，正在服务的旧集合
      全程不受影响。
    - ``incremental``：增量，只重写 ``plan.to_index_ids`` 这几本的块，并把停用/删除
      文档遗留的块从正在服务的集合里清出去——其余文档一个字节都不动。
    - ``noop``：没有需要处理的文档，立即返回。

    返回值里的 ``changed`` 表示集合内容是否真的变了：空操作点击不该触发检索器重置与
    预热（预热会把整库读进内存，白等几秒）。

    ``plan`` 通常由 ``start_rebuild`` 在请求线程里解析好（进度要立刻可见，必须先落库）；
    脚本/测试直接调用时可以不传，只给 ``force`` / ``document_ids``，这里按同一份规则
    现场解析——判定只有一处，不存在两条路径各判一次然后漂移。
    """
    if plan is None:
        plan = _resolve_index_target(
            db, owner_id=owner_id, document_ids=document_ids, force=force
        )

    if plan.mode == "noop":
        return {
            "status": "ready",
            "indexed_document_ids": [],
            "chunk_count": 0,
            "changed": False,
        }

    collection_name = plan.collection_name
    documents = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.document_id.in_(plan.to_index_ids))
        .order_by(KnowledgeDocument.created_at.asc())
        .all()
        if plan.to_index_ids
        else []
    )
    document_ids = [document.document_id for document in documents]

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

    # 按文档分组，并保持文档自身的处理顺序。进度要能回答"现在在处理哪一本"，
    # 就必须能由"已写入第 N 块"反推文档边界；以前块是全库混排的，做不到这一点。
    title_by_id = {document.document_id: document.title for document in documents}
    chunks_by_document: dict[str, list[dict]] = {
        document_id: [] for document_id in document_ids
    }
    for evidence in evidence_rows:
        document_id = evidence.knowledge_document_id
        if document_id not in title_by_id:
            continue
        chunks_by_document[document_id].append(
            {
                "content": evidence.text,
                "source": title_by_id[document_id],
                "chunk_index": evidence.chunk_index,
                "metadata": _chunk_metadata(document_id, evidence),
            }
        )

    chunks: list[dict] = []
    boundaries: list[tuple[str, int, int]] = []
    for document_id in document_ids:
        start = len(chunks)
        chunks.extend(chunks_by_document[document_id])
        boundaries.append((document_id, start, len(chunks)))

    now = _now()
    for document in documents:
        document.index_status = "indexing"
        document.error_code = None
        document.error_message = None
        document.metadata_json = {
            **(document.metadata_json or {}),
            # 总块数在嵌入开始前就已经确定，先落库，界面才能显示"已写入 x / y 块"。
            # chunk_count 只在真正写完时才写，否则一次失败的重建会留下"看起来有内容"
            # 的假象。
            "chunk_total": len(chunks_by_document[document.document_id]),
            "chunks_written": 0,
        }
        document.updated_at = now
    db.commit()

    documents_by_id = {document.document_id: document for document in documents}

    def persist_progress(written: int, _total: int) -> None:
        """把"已写入第几块"换算成"哪一本、这一本写了多少"并落库。

        回调签名必须与 ``build_index`` 一致（已写入块数, 总块数）。总块数这里用不到
        ——文档边界已由 boundaries 给出——但**必须收下**：少一个参数会抛 TypeError，
        而嵌入器会把回调异常吞掉，于是生产上表现为"进度永远停在准备中"却毫无报错。

        每 100 块回调一次，一次索引任务的数据库写入次数约等于本次总块数 / 100，代价
        可以忽略；换来的是刷新页面、甚至换个进程都能接着看到进度。
        """
        touched_at = _now()
        dirty = False
        for document_id, start, end in boundaries:
            document = documents_by_id[document_id]
            if written >= end:
                if document.index_status == "ready":
                    continue
                document.index_status = "ready"
                document.index_namespace = collection_name
                document.indexed_at = touched_at
                document.metadata_json = {
                    **(document.metadata_json or {}),
                    "chunk_count": end - start,
                    "chunks_written": end - start,
                }
            elif start < written < end:
                written_for_document = written - start
                metadata = document.metadata_json or {}
                if metadata.get("chunks_written") == written_for_document:
                    continue
                document.metadata_json = {
                    **metadata,
                    "chunks_written": written_for_document,
                }
            else:
                # 还没轮到这一本：保持 indexing，等它自己那一轮再落库
                continue
            document.updated_at = touched_at
            dirty = True
        if dirty:
            db.commit()

    try:
        with _knowledge_index_lock(owner_id):
            if plan.mode == "staged":
                build_index(
                    chunks,
                    settings.chroma_persist_dir,
                    collection_name=collection_name,
                    on_batch=persist_progress,
                )
                activate_knowledge_collection(owner_id, collection_name)
            else:
                if plan.to_remove_ids:
                    # 停用/删除的文档已不是索引来源，它们的块留在集合里只占空间
                    # （检索结果会被 ready 过滤挡掉，但磁盘不会）。
                    remove_document_chunks(
                        settings.chroma_persist_dir,
                        collection_name,
                        plan.to_remove_ids,
                    )
                add_index(
                    chunks,
                    settings.chroma_persist_dir,
                    collection_name=collection_name,
                    on_batch=persist_progress,
                    replace_document_ids=document_ids,
                )
            reset_retriever(owner_id)
    except Exception as exc:
        logger.exception("Knowledge index rebuild failed for owner %s", owner_id)
        reset_retriever(owner_id)
        failed_at = _now()
        if plan.mode == "staged":
            # 暂存集合已经作废（build_index 失败时会删掉它），正在服务的旧集合完好；
            # 这里把候选统一标失败并留下可重试的错误信息，界面显示"失败，需重试"，
            # 教师再点一次即可重跑。
            targets = documents
        else:
            # 增量动的是正在服务的集合：本次已写完的书是真写进去了，不能一起标失败。
            # 只把没写完的标 failed，并清掉它们写了一半的块（重试时会先删再写）。
            targets = [
                document for document in documents if document.index_status != "ready"
            ]
            if targets:
                try:
                    remove_document_chunks(
                        settings.chroma_persist_dir,
                        collection_name,
                        [document.document_id for document in targets],
                    )
                except Exception:
                    logger.warning(
                        "清理失败文档的半成品块出错（重试时会重建）", exc_info=True
                    )
        for document in targets:
            document.index_status = "failed"
            document.error_code = "KNOWLEDGE_INDEX_FAILED"
            document.error_message = str(exc)
            document.updated_at = failed_at
        db.commit()
        raise KnowledgeIndexError(
            "知识库向量索引失败",
            details={"document_ids": [document.document_id for document in targets]},
        ) from exc

    indexed_at = _now()
    # 只遍历本次处理的文档：增量模式下被跳过的就绪文档不在 documents 里，照旧写一遍
    # 会把它们的 chunk_count 覆盖成 0。
    for document in documents:
        document.index_status = "ready"
        document.index_namespace = collection_name
        document.indexed_at = indexed_at
        document.metadata_json = {
            **(document.metadata_json or {}),
            "chunk_count": len(chunks_by_document[document.document_id]),
            "chunks_written": len(chunks_by_document[document.document_id]),
        }
        document.updated_at = indexed_at
    db.commit()
    reset_retriever(owner_id)
    return {
        "status": "ready",
        "indexed_document_ids": document_ids,
        "chunk_count": len(chunks),
        "changed": True,
    }


# 同一账号正在跑的进程内重建。``rebuild_index`` 里的锁保护的是"两个重建同时写同一个
# collection"，而这里回答的是另一个问题：这次请求该不该再启动一个后台线程。
# 没有它，连点两次"建立索引"会让后一次请求一直阻塞在同一把锁上（开发环境尤其久，
# 有活要干时一次索引是分钟级起步），界面表现为"点一下没反应"。
_RUNNING_REBUILDS: set[str] = set()
_RUNNING_REBUILDS_LOCK = Lock()


def is_rebuild_running(owner_id: str) -> bool:
    with _RUNNING_REBUILDS_LOCK:
        return owner_id in _RUNNING_REBUILDS


def _queue_rebuild(db: DBSession, *, plan: _IndexPlan) -> int:
    """把本次要处理的文档标成"索引中"并落库，返回文档数。

    这一步在**请求里**同步做完，是为了让 ``/knowledge/index/progress`` 立刻报出
    ``running``：接口返回后前端马上开始轮询，如果这时文档还是 pending，前几次轮询
    读到的都是 idle，"已开始的索引"会被当成"什么都没发生"。

    只标本次真正要处理的文档：增量模式下其余就绪文档保持 ready——它们既不会被重嵌，
    也不该在界面上显示成"索引中"。
    """
    if not plan.to_index_ids:
        return 0

    documents = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.document_id.in_(plan.to_index_ids))
        .order_by(KnowledgeDocument.created_at.asc())
        .all()
    )
    document_ids = [document.document_id for document in documents]
    # 只数块数，不取正文：这里只为了让进度条立刻显示"已写入 0 / N 块"，
    # 真正读块、切块、嵌入都留在线程里（那边会重新查一遍）。
    counts = dict(
        db.query(
            EvidenceChunk.knowledge_document_id,
            func.count(EvidenceChunk.evidence_id),
        )
        .filter(
            EvidenceChunk.knowledge_document_id.in_(document_ids),
            EvidenceChunk.is_valid.is_(True),
        )
        .group_by(EvidenceChunk.knowledge_document_id)
        .all()
    )

    run_id = uuid.uuid4().hex
    now = _now()
    for document in documents:
        document.index_status = "indexing"
        document.error_code = None
        document.error_message = None
        document.metadata_json = {
            **(document.metadata_json or {}),
            "chunk_total": counts.get(document.document_id, 0),
            "chunks_written": 0,
            # 本次运行的标记：进度接口据此把"第 N / M 本"限定在本次处理的文档里，
            # 增量模式下就绪的老书不占位置。
            "index_run": run_id,
        }
        document.updated_at = now
    db.commit()
    return len(documents)


def start_rebuild(
    db: DBSession,
    *,
    owner_id: str,
    document_ids: Sequence[str] | None = None,
    force: bool = False,
) -> tuple[bool, bool]:
    """在后台线程里跑一次索引任务，立刻返回 ``(是否启动, 是否有活要干)``。

    索引是分钟级起步的 CPU 活，而它过去就跑在请求线程里：接口一直挂着，直到浏览器
    先失去耐心——教师看到的是"建到一半报超时"，服务端却还在跑。改成后台线程之后，
    请求只负责启动，进度继续由 ``/knowledge/index/progress`` 提供。

    默认是增量任务：只处理新增/未就绪的文档，绝大多数点击立刻就能回答"没有活要干"
    （``(True, False)``，连线程都不启动）。

    返回 ``started=False`` 表示这个账号已经有一次索引在跑，本次没有重复启动。
    """
    with _RUNNING_REBUILDS_LOCK:
        if owner_id in _RUNNING_REBUILDS:
            return False, True
        _RUNNING_REBUILDS.add(owner_id)

    try:
        plan = _resolve_index_target(
            db, owner_id=owner_id, document_ids=document_ids, force=force
        )
        if plan.mode == "noop":
            # 没有活要干：不启动线程，也要立刻摘掉守卫，否则这个账号之后的请求会
            # 一直被告知"已有一次索引在跑"。
            with _RUNNING_REBUILDS_LOCK:
                _RUNNING_REBUILDS.discard(owner_id)
            return True, False

        _queue_rebuild(db, plan=plan)
        thread = Thread(
            target=_run_rebuild_in_thread,
            args=(owner_id, plan),
            name=f"knowledge-index-{owner_id[:24]}",
            # 守护线程：索引中途退出进程时不留住解释器。代价是这次任务的状态会停在
            # "索引中"，由 progress 接口在超时后报 stalled，教师重新点一次即可。
            daemon=True,
        )
        thread.start()
        return True, True
    except Exception:
        with _RUNNING_REBUILDS_LOCK:
            _RUNNING_REBUILDS.discard(owner_id)
        raise


def _run_rebuild_in_thread(owner_id: str, plan: _IndexPlan) -> None:
    """线程入口：自建数据库会话，失败只记日志。

    异常不能往外抛（没人接），但也不能悄悄咽下：索引失败会把本次未完成的文档标成
    failed 并写入 error_message，界面据此显示"失败，需重试"，日志留一份完整的栈。

    ``SessionLocal`` 必须在这里 import，不能在模块顶层 from ... import：测试用
    monkeypatch 覆盖的是 ``backend.db.database.SessionLocal``，顶层 import 会把绑定
    固定在旧对象上，后台线程就会连上真实的库（见 tests/test_progress.py 的同一约定）。
    """
    from backend.db.database import SessionLocal

    db = SessionLocal()
    changed = False
    try:
        result = rebuild_index(db, owner_id=owner_id, plan=plan)
        changed = bool(result.get("changed"))
    except Exception:
        logger.exception("后台知识库索引失败（owner=%s）", owner_id)
    finally:
        db.close()
        # 先摘掉"正在索引"再预热：预热只关系到下一次检索快不快，不该让"能不能再点一次
        # 建立索引"多等它几秒。
        with _RUNNING_REBUILDS_LOCK:
            _RUNNING_REBUILDS.discard(owner_id)

    if changed:
        # 索引结束会清掉检索器缓存，下一次检索要重新加载整库索引与向量模型（实测冷
        # 启动十几秒，正好卡在检索接口的等待上限附近）。这里顺手预热，把这段代价
        # 挪到没人等待的后台线程里。什么都没改的空操作不预热：预热会把整库读进内存，
        # 白等几秒。
        warm_retriever(owner_id)


# 没有任何进度更新超过这个时长，就认为这次重建已经不推进了。阈值刻意放宽到 10
# 分钟：首次运行要先下载向量模型，那段时间本来就不会有任何批次回调。
_INDEX_STALE_AFTER_SECONDS = 600


def _index_stalled(document: KnowledgeDocument, *, now: datetime) -> bool:
    updated = document.updated_at
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (now - updated).total_seconds() > _INDEX_STALE_AFTER_SECONDS


def index_progress(db: DBSession, *, owner_id: str) -> dict[str, Any]:
    """当前索引进度：正在处理的文件、块进度与整体进度。

    进度完全由已有字段推导（index_status + metadata_json + updated_at），不引入独立
    的运行态存储：刷新页面、换进程、重启进程读到的都是同一份事实，也不会出现
    "内存里有、库里没有"两套数据对不上。

    索引跑在后台线程里，启动接口返回时什么都还没做完，所以进度必须能单独查询。
    默认的增量运行只处理少数文档，其余就绪文档仍然计入"已建索引"的整库口径；本次
    处理的文档由 ``index_run`` 标记圈出（见 ``_queue_rebuild``）。
    """
    documents = _rebuild_candidates(db, owner_id=owner_id)
    total_documents = len(documents)
    ready_documents = sum(1 for item in documents if item.index_status == "ready")
    failed_documents = sum(1 for item in documents if item.index_status == "failed")
    pending = [item for item in documents if item.index_status == "indexing"]

    now = _now()
    # 用 all 而不是 any：只有所有在跑的文档都停止更新，才算真卡住。已经写完的文档
    # 不会再更新 updated_at，用 any 会把正常推进的索引误报成中断。
    stalled = bool(pending) and all(_index_stalled(item, now=now) for item in pending)
    running = bool(pending) and not stalled

    def _count(document: KnowledgeDocument, key: str) -> int:
        try:
            return max(0, int((document.metadata_json or {}).get(key) or 0))
        except (TypeError, ValueError):
            return 0

    def _written_and_total(document: KnowledgeDocument) -> tuple[int, int]:
        """整库口径下这一本贡献的 (已写入, 总数)。

        就绪的文档按满额算：它的 chunk_total 只停在上一次被处理的那一刻，历史中断留下
        的 chunks_written（比如 7/100）如果照实累加，会让百分比被永远拉低。
        chunk_total 缺失时退回导入时写下的 chunk_count（刚导入、还没索引过的书）。
        """
        expected = _count(document, "chunk_total") or _count(document, "chunk_count")
        if document.index_status == "ready":
            return expected, expected
        return _count(document, "chunks_written"), expected

    written_chunks = 0
    total_chunks = 0
    for item in documents:
        item_written, item_total = _written_and_total(item)
        written_chunks += item_written
        total_chunks += item_total

    current = pending[0] if pending else None
    # 本次运行的文档集合：以索引中文档的 index_run 标记为准。找不到标记（历史数据）
    # 时退回"正在索引的文档"本身，至少保证位置与阶段信息可用。
    run_id = (current.metadata_json or {}).get("index_run") if current is not None else None
    run_documents = [
        item
        for item in documents
        if run_id is not None and (item.metadata_json or {}).get("index_run") == run_id
    ]
    if not run_documents:
        run_documents = pending
    run_written_chunks = sum(_written_and_total(item)[0] for item in run_documents)
    current_position = 0
    if current is not None:
        current_position = next(
            (
                index + 1
                for index, item in enumerate(run_documents)
                if item.document_id == current.document_id
            ),
            0,
        )

    if total_documents == 0:
        percent = 0
    elif total_chunks > 0:
        percent = min(100, round(written_chunks / total_chunks * 100))
    else:
        percent = round(ready_documents / total_documents * 100)
    if not running and total_documents and ready_documents == total_documents:
        percent = 100

    if running:
        # 一块都还没写入，说明还在加载（首次运行包含模型下载），这跟"正在嵌入"是
        # 两件完全不同的事，界面上必须分开说，否则教师只会以为卡死了。这里要用
        # **本次运行**的写入量：增量运行里就绪的老书会让整库口径从第一帧起就大于 0，
        # "正在准备向量模型"就再也显示不出来了。
        stage = "embedding" if run_written_chunks > 0 else "preparing"
    elif stalled:
        stage = "stalled"
    else:
        stage = "idle"

    return {
        "running": running,
        "stalled": stalled,
        "stage": stage,
        "total_documents": total_documents,
        "ready_documents": ready_documents,
        "failed_documents": failed_documents,
        "current_document_id": current.document_id if current is not None else None,
        "current_document_title": current.title if current is not None else "",
        "current_document_position": current_position,
        "current_chunks_written": _count(current, "chunks_written") if current is not None else 0,
        "current_chunks_total": _count(current, "chunk_total") if current is not None else 0,
        "run_documents": len(run_documents),
        "run_written_chunks": run_written_chunks,
        "written_chunks": written_chunks,
        "total_chunks": total_chunks,
        "percent": percent,
    }
