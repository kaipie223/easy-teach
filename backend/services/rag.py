"""M3 - RAG retrieval service with an explicit empty-index fallback."""

import logging
from functools import partial
from threading import RLock

from starlette.concurrency import run_in_threadpool

from backend.config import settings
from ai.rag.retriever import RAGRetriever
from backend.schemas import RAGDocument

logger = logging.getLogger(__name__)
_retrievers: dict[str, RAGRetriever] = {}
_owner_collections: dict[str, set[str]] = {}
_retriever_lock = RLock()


def _get_retriever(owner_id: str) -> RAGRetriever:
    from backend.services.knowledge_scope import active_knowledge_collection_name

    collection_name = active_knowledge_collection_name(owner_id)
    with _retriever_lock:
        if collection_name not in _retrievers:
            _retrievers[collection_name] = RAGRetriever(
                settings.chroma_persist_dir,
                collection_name=collection_name,
            )
        _owner_collections.setdefault(owner_id, set()).add(collection_name)
        return _retrievers[collection_name]


def _search_sync(query: str, top_k: int, owner_id: str) -> list[RAGDocument]:
    return _get_retriever(owner_id).search(query, top_k)


def search_sync(query: str, top_k: int = 5, *, owner_id: str | None) -> list[RAGDocument]:
    """Synchronous RAG entry point for callers already running in a worker thread."""
    if not query.strip():
        return []
    if not owner_id:
        logger.info("RAG search skipped because no teacher owner is available")
        return []
    try:
        return _search_sync(query, top_k, owner_id)
    except FileNotFoundError as exc:
        logger.warning("RAG is unavailable: %s", exc)
        return []
    except Exception:
        logger.exception("RAG search failed")
        return []


async def search(query: str, top_k: int = 5, *, owner_id: str | None) -> list[RAGDocument]:
    """Async wrapper that keeps the blocking vector search off the event loop."""
    return await run_in_threadpool(partial(search_sync, query, top_k, owner_id=owner_id))


def warm_retriever(owner_id: str | None) -> None:
    """先把检索器建起来并跑一次最小查询。

    冷启动（构建 RAGRetriever：打开 Chroma、把整库读进内存、加载 HNSW 索引，再初始化
    向量模型）实测十几秒，而它过去落在**教师第一次点"检索"**的那一刻——那正是最不该
    让人等的地方，也贴着检索接口的等待上限。放在重建刚结束时预热，这段时间没有人在等。

    预热失败不改变任何状态：检索路径本来就有空索引兜底，下一次真正检索会自己再建一次。
    """
    if not owner_id:
        return
    try:
        _get_retriever(owner_id).search("预热", 1)
    except FileNotFoundError:
        # 还没有建过索引的账号走这里，属于正常情况。
        pass
    except Exception:
        logger.warning("RAG 预热失败（不影响检索本身）", exc_info=True)


def reset_retriever(owner_id: str | None = None) -> None:
    """Reset the process cache for tests and after rebuilding the index."""
    if owner_id is None:
        with _retriever_lock:
            _retrievers.clear()
            _owner_collections.clear()
        return
    with _retriever_lock:
        for collection_name in _owner_collections.pop(owner_id, set()):
            _retrievers.pop(collection_name, None)
