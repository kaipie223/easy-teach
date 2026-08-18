"""验证 ChromaDB + 中文 embedding 检索精度"""
from backend.config import settings
from ai.rag.retriever import RAGRetriever

PERSIST_DIR = settings.chroma_persist_dir

retriever = RAGRetriever(PERSIST_DIR)

# 5 个教学查询，覆盖不同教材
TEST_QUERIES = [
    ("Python列表常用方法", ["Python基础教程"]),
    ("二叉树的遍历方式", ["数据结构"]),
    ("TCP三次握手过程", ["计算机网络"]),
    ("监督学习和无监督学习的区别", ["机器学习"]),
    ("Python函数的参数传递", ["Python基础教程"]),
]

print("=== ChromaDB 检索精度测试（中文 embedding） ===\n")
total_correct = 0
total_checks = 0

for query, expected_sources in TEST_QUERIES:
    results = retriever.search(query, top_k=3)
    print(f"查询: {query}")
    correct = 0
    for i, r in enumerate(results):
        hit = any(es in r.source for es in expected_sources)
        mark = "✓" if hit else "✗"
        if hit:
            correct += 1
        print(f"  [{i+1}] {mark} {r.source} (score={r.score:.4f})")
        print(f"      {r.content[:100]}...")
    precision = correct / len(results) if results else 0
    total_correct += correct
    total_checks += len(results)
    print(f"  Precision@3: {correct}/3 = {precision:.0%}\n")

overall = total_correct / total_checks if total_checks else 0
print(f"总体 Precision@3: {total_correct}/{total_checks} = {overall:.0%}")
print("=== 测试通过 ===" if overall >= 0.6 else f"=== 未达 60% 阈值，当前 {overall:.0%} ===")
