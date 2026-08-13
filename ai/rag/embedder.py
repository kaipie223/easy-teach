"""向量化器 — 将文本块写入 ChromaDB（中文优化）"""

import os
import chromadb
from chromadb.utils import embedding_functions

# 中文优化的 embedding 模型
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


def build_index(chunks: list[dict], persist_dir: str,
                collection_name: str = "knowledge_base"):
    """
    将切分后的文本块向量化并写入 ChromaDB。
    """
    os.makedirs(persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=persist_dir)

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
    )

    # 删除旧 collection 后重建（全量重建模式）
    try:
        client.delete_collection(collection_name)
        print(f"[embedder] 已删除旧 collection: {collection_name}")
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"description": "easy-teach 教学知识库"},
        embedding_function=ef,
    )

    if not chunks:
        print("[embedder] 无数据需要向量化")
        return

    batch_size = 100
    total = len(chunks)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = chunks[start:end]
        collection.add(
            documents=[c["content"] for c in batch],
            metadatas=[{"source": c["source"], "chunk_index": c["chunk_index"]} for c in batch],
            ids=[f"chunk_{i}" for i in range(start, end)],
        )
        print(f"[embedder] {end}/{total} 块已写入")

    print(f"[embedder] 全部完成: {total} 个块 → collection '{collection_name}'")
