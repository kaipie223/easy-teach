"""文档切分器 — 中文友好的文档分块"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_documents(docs: list[dict], chunk_size: int = 500,
                    chunk_overlap: int = 50) -> list[dict]:
    """
    将文档切分为语义块。
    返回: [{"content": str, "source": str, "chunk_index": int}, ...]
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
    )

    chunks = []
    for doc in docs:
        text = doc["content"]
        source = doc["source"]
        doc_chunks = splitter.split_text(text)
        for i, chunk_text in enumerate(doc_chunks):
            chunks.append({
                "content": chunk_text.strip(),
                "source": source,
                "chunk_index": i,
            })

    print(f"[splitter] 输入 {len(docs)} 份文档 → 输出 {len(chunks)} 个块")
    return chunks
