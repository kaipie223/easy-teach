"""M3 - RAG retrieval service with an explicit empty-index fallback."""

import logging

from backend.config import settings
from ai.rag.retriever import RAGRetriever
from backend.schemas import RAGDocument

logger = logging.getLogger(__name__)
_retriever: RAGRetriever | None = None


def _get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever(settings.chroma_persist_dir)
    return _retriever


async def search(query: str, top_k: int = 5) -> list[RAGDocument]:
    if not query.strip():
        return []
    try:
        return _get_retriever().search(query, top_k)
    except FileNotFoundError as exc:
        logger.warning("RAG is unavailable: %s", exc)
        return []
    except Exception:
        logger.exception("RAG search failed")
        return []


def reset_retriever() -> None:
    """Reset the process cache for tests and after rebuilding the index."""
    global _retriever
    _retriever = None
