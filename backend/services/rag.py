"""M3 — RAG 检索服务"""

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OpenAIEmbeddings

from config import settings


def get_vectorstore() -> Chroma:
    """获取 ChromaDB 向量存储实例"""
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    return Chroma(
        persist_directory=settings.chroma_persist_dir,
        embedding_function=embeddings,
    )


async def search(query: str, top_k: int = 5) -> list[dict]:
    """
    语义检索知识库。

    Args:
        query: 检索查询
        top_k: 返回结果数

    Returns:
        RAGDocument 列表
    """
    # TODO: 向量检索 → 返回相关文档
    raise NotImplementedError("M3 RAG — 待陈澜实现")
