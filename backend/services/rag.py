"""M3 — RAG 检索服务"""

import logging

from chromadb import PersistentClient
from chromadb.utils import embedding_functions

from config import settings
from schemas import RAGDocument

logger = logging.getLogger(__name__)

COLLECTION_NAME = "teaching_materials"


def _get_collection():
    """获取或创建 ChromaDB collection。"""
    client = PersistentClient(path=str(settings.chroma_persist_dir))
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
    )


def add_documents(documents: list[dict]) -> None:
    """将文档入库。

    Args:
        documents: [{"id": "doc1", "content": "...", "metadata": {"source": "..."}}, ...]
    """
    if not documents:
        return
    collection = _get_collection()
    collection.add(
        ids=[d["id"] for d in documents],
        documents=[d["content"] for d in documents],
        metadatas=[d.get("metadata", {}) for d in documents],
    )
    logger.info("Added %d documents to ChromaDB", len(documents))


async def search(query: str, top_k: int = 5) -> list[RAGDocument]:
    """语义检索知识库。

    Args:
        query: 检索查询
        top_k: 返回结果数

    Returns:
        RAGDocument 列表
    """
    try:
        collection = _get_collection()
        results = collection.query(query_texts=[query], n_results=top_k)
        docs = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                docs.append(RAGDocument(
                    content=results["documents"][0][i] if results["documents"] else "",
                    source=results["metadatas"][0][i].get("source", "") if results["metadatas"] else "",
                    score=round(1 - (results["distances"][0][i] if results["distances"] else 1), 4),
                ))
        return docs
    except Exception:
        logger.exception("RAG search failed, returning empty results")
        return []


def get_vectorstore():
    """获取向量存储实例（兼容旧接口）。"""
    return _get_collection()
