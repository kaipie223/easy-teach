"""一键知识库构建脚本 — E 可独立运行"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.rag.loader import load_documents
from ai.rag.splitter import split_documents
from ai.rag.embedder import build_index
from ai.rag.retriever import RAGRetriever


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kb_dir = os.path.join(project_root, "knowledge-base")
    persist_dir = os.path.join(project_root, "chroma_data")

    print("=" * 50)
    print("easy-teach 知识库构建")
    print(f"知识库目录: {kb_dir}")
    print(f"向量库目录: {persist_dir}")
    print("=" * 50)

    # Step 1: 加载文档
    print("\n[1/3] 加载文档…")
    start = time.time()
    docs = load_documents(kb_dir)
    print(f"  耗时: {time.time() - start:.1f}s")

    if not docs:
        print("\n⚠ 未发现任何文档，请在 knowledge-base/ 下放入 PDF/TXT/DOCX 文件后重试")
        return

    # Step 2: 切分文档
    print("\n[2/3] 切分文档…")
    start = time.time()
    chunks = split_documents(docs, chunk_size=500, chunk_overlap=50)
    print(f"  耗时: {time.time() - start:.1f}s")

    # Step 3: 向量化入库
    print("\n[3/3] 向量化入库…")
    start = time.time()
    build_index(chunks, persist_dir)
    print(f"  耗时: {time.time() - start:.1f}s")

    # 最终统计
    print("\n" + "=" * 50)
    print(f"构建完成！文档数: {len(docs)}，块数: {len(chunks)}")
    print(f"向量库路径: {persist_dir}")
    print("=" * 50)

    # 快速验证
    print("\n快速验证：检索 'Python基础' …")
    retriever = RAGRetriever(persist_dir)
    results = retriever.search("Python基础", top_k=3)
    for i, r in enumerate(results):
        print(f"  [{i+1}] {r.source} (score={r.score})\n      {r.content[:80]}...")


if __name__ == "__main__":
    main()
