"""M3 - RAG retrieval service with an explicit empty-index fallback."""

import logging
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


async def search(query: str, top_k: int = 5, *, owner_id: str | None) -> list[RAGDocument]:
    if not query.strip():
        return []
    if not owner_id:
        logger.info("RAG search skipped because no teacher owner is available")
        return []
    try:
        return await run_in_threadpool(_search_sync, query, top_k, owner_id)
    except FileNotFoundError as exc:
        logger.warning("RAG is unavailable: %s", exc)
        return []
    except Exception:
        logger.exception("RAG search failed")
        return []


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
