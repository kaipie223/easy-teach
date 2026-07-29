"""验证 ChromaDB 可用"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb
from chromadb.config import Settings

PERSIST_DIR = os.path.join(os.path.dirname(__file__), "..", "chroma_data")

client = chromadb.PersistentClient(path=PERSIST_DIR)
collection = client.get_or_create_collection("test_collection")

collection.add(
    documents=["Python 是一种广泛使用的解释型、高级和通用的编程语言。它由吉多·范罗苏姆创建，于1991年首次发布。"],
    metadatas=[{"source": "test", "chunk_index": 0}],
    ids=["test_1"],
)

results = collection.query(query_texts=["Python是什么"], n_results=3)

print("=== ChromaDB 测试 ===")
print(f"持久化目录: {PERSIST_DIR}")
print(f"检索结果数量: {len(results['documents'][0])}")
for i, (doc, meta, dist) in enumerate(zip(
    results["documents"][0], results["metadatas"][0], results["distances"][0]
)):
    print(f"  [{i+1}] 内容: {doc[:80]}...")
    print(f"      来源: {meta.get('source', 'unknown')}, 距离: {dist:.4f}")
print("=== 测试通过 ===")
