"""M3 — RAG 检索服务"""

from config import settings
from ai.rag.retriever import RAGRetriever
from models.schemas import RAGDocument

_retriever: RAGRetriever | None = None


def _get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever(settings.chroma_persist_dir)
    return _retriever


async def search(query: str, top_k: int = 5) -> list[RAGDocument]:
    return _get_retriever().search(query, top_k)
