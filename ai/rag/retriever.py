"""RAGRetriever — 语义检索知识库"""

import os
import chromadb
from backend.models.schemas import RAGDocument


class RAGRetriever:
    """向量检索器 — 从 ChromaDB 知识库检索相关文档块"""

    def __init__(self, chroma_persist_dir: str, collection_name: str = "knowledge_base"):
        if not os.path.isdir(chroma_persist_dir):
            raise FileNotFoundError(f"ChromaDB 目录不存在: {chroma_persist_dir}，请先运行 build_kb.py")

        self.client = chromadb.PersistentClient(path=chroma_persist_dir)
        self.collection = self.client.get_collection(collection_name)

    def search(self, query: str, top_k: int = 5) -> list[RAGDocument]:
        results = self.collection.query(query_texts=[query], n_results=top_k)

        docs = []
        if not results["documents"][0]:
            return docs

        for i in range(len(results["documents"][0])):
            doc_id = results["ids"][0][i]
            content = results["documents"][0][i]
            meta = results["metadatas"][0][i] or {}
            distance = results["distances"][0][i] if results["distances"] else [0.0]
            score = 1.0 - min(distance[i] if isinstance(distance, list) else distance, 1.0)

            docs.append(RAGDocument(
                doc_id=doc_id,
                content=content,
                source=meta.get("source", "unknown"),
                score=round(score, 4),
                metadata={"chunk_index": meta.get("chunk_index", -1)},
            ))

        return docs

    def search_raw(self, query: str, top_k: int = 5) -> list[dict]:
        """返回原始 dict 列表，方便 fusion 模块使用"""
        return [d.model_dump() for d in self.search(query, top_k)]
