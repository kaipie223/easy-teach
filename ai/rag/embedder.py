"""Write parsed teaching chunks into a persistent Chroma collection."""

import os
import chromadb

from ai.rag.embedding import FastEmbedEmbeddingFunction
from backend.config import normalize_chroma_path


def build_index(chunks: list[dict], persist_dir: str, collection_name: str):
    """
    将切分后的文本块向量化并写入 ChromaDB。
    """
    persist_path = normalize_chroma_path(persist_dir)
    os.makedirs(str(persist_path), exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_path))

    # Initializing before replacing a collection makes model download/config
    # failures non-destructive to the currently active teacher index.
    embedding_function = FastEmbedEmbeddingFunction()

    # 删除旧 collection 后重建（全量重建模式）
    try:
        client.delete_collection(collection_name)
        print(f"[embedder] 已删除旧 collection: {collection_name}")
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"description": "easy-teach 教学知识库"},
        embedding_function=embedding_function,
    )

    if not chunks:
        print("[embedder] 无数据需要向量化")
        return

    try:
        batch_size = 100
        total = len(chunks)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = chunks[start:end]
            metadatas = []
            for chunk in batch:
                metadata = {
                    "source": chunk["source"],
                    "chunk_index": chunk["chunk_index"],
                }
                metadata.update(
                    {
                        key: value
                        for key, value in chunk.get("metadata", {}).items()
                        if value is not None and isinstance(value, (str, int, float, bool))
                    }
                )
                metadatas.append(metadata)
            collection.add(
                documents=[c["content"] for c in batch],
                metadatas=metadatas,
                ids=[f"chunk_{i}" for i in range(start, end)],
            )
            print(f"[embedder] {end}/{total} 块已写入")

        # Opening one entry surfaces incomplete HNSW persistence before the
        # database marks documents ready and switches the active pointer.
        if collection.count() > 0:
            preview = collection.peek(limit=1)
            if not preview.get("ids"):
                raise RuntimeError("Chroma index was built but no readable entry was found")
    except Exception:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        raise

    print(f"[embedder] 全部完成: {total} 个块 → collection '{collection_name}'")
