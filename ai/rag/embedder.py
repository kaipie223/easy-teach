"""Write parsed teaching chunks into a persistent Chroma collection."""

import logging
import os
from pathlib import Path
from typing import Callable, Sequence

import chromadb

from ai.rag.embedding import FastEmbedEmbeddingFunction

logger = logging.getLogger(__name__)

_COLLECTION_METADATA = {"description": "easy-teach 教学知识库"}
_BATCH_SIZE = 100


def collection_stats(persist_dir: str, collection_name: str) -> tuple[bool, int]:
    """返回 (集合是否存在, 已有块数)。

    调用方据此判断"当前 active 集合还能不能接着追加"：集合不存在或为空（被人手工删掉、
    或上次构建中断）时必须退回全量重建，否则增量会把"库里没有旧块"当成"已经是最新"。
    这里刻意不构造嵌入函数：只回答存在性，不该为此加载模型。
    """
    persist_path = Path(persist_dir).expanduser().resolve()
    client = chromadb.PersistentClient(path=str(persist_path))
    names = [
        item.name if hasattr(item, "name") else str(item)
        for item in client.list_collections()
    ]
    if collection_name not in names:
        return False, 0
    return True, client.get_collection(collection_name).count()


def _batch_ids(batch: list[dict], start: int) -> list[str]:
    """块在集合里的 id：优先用 evidence_id。

    必须在整个 collection 内唯一：chromadb 对已存在的 id 是**静默忽略**（不覆盖、也不
    报错），撞 id 的表现是"这一块根本没写进去，状态却是就绪"。所以缺 evidence_id 时
    回退到带动档名的编号，绝不回退到位置编号（chunk_0 会在每本文档间重复）。
    """
    ids: list[str] = []
    for offset, chunk in enumerate(batch):
        metadata = chunk.get("metadata") or {}
        evidence_id = metadata.get("evidence_id")
        if evidence_id:
            ids.append(str(evidence_id))
        else:
            document_id = metadata.get("document_id") or "anon"
            ids.append(f"{document_id}_{chunk.get('chunk_index', start + offset)}")
    return ids


def _batch_metadatas(batch: list[dict]) -> list[dict]:
    metadatas = []
    for chunk in batch:
        metadata = {
            "source": chunk["source"],
            "chunk_index": chunk["chunk_index"],
        }
        # chroma 的 metadata 值不接受 None（会直接抛 TypeError），这里的过滤不能省。
        metadata.update(
            {
                key: value
                for key, value in (chunk.get("metadata") or {}).items()
                if value is not None and isinstance(value, (str, int, float, bool))
            }
        )
        metadatas.append(metadata)
    return metadatas


def _write_batches(
    collection,
    chunks: list[dict],
    on_batch: Callable[[int, int], None] | None,
) -> int:
    """分批写入并回调进度，返回写入块数。回调异常只记日志、不打断索引。"""
    total = len(chunks)
    for start in range(0, total, _BATCH_SIZE):
        end = min(start + _BATCH_SIZE, total)
        batch = chunks[start:end]
        collection.add(
            ids=_batch_ids(batch, start),
            documents=[chunk["content"] for chunk in batch],
            metadatas=_batch_metadatas(batch),
        )
        print(f"[embedder] {end}/{total} 块已写入")
        if on_batch is not None:
            try:
                on_batch(end, total)
            except Exception:
                # 进度写不进去不该让一次十几分钟的索引白做，所以这里吞掉异常。
                # 但必须用 error 级别：吞掉的后果是"进度永远不动"，如果不显眼，
                # 排查时只会看到界面卡住而找不到原因。
                logger.error(
                    "索引进度回调失败（进度将不再更新，索引本身不受影响）", exc_info=True
                )
    return total


def _verify_readable(collection) -> None:
    """写完后读一条：把 HNSW 持久化问题暴露在"文档被标成就绪"之前。"""
    if collection.count() > 0:
        preview = collection.peek(limit=1)
        if not preview.get("ids"):
            raise RuntimeError("Chroma index was built but no readable entry was found")


def build_index(
    chunks: list[dict],
    persist_dir: str,
    collection_name: str,
    on_batch: Callable[[int, int], None] | None = None,
) -> int:
    """全量重建：删掉同名 collection 后从头写入，返回写入块数。

    只用于"暂存 collection + 原子切换"的全量路径（首次建库、集合丢失自愈与显式
    force 重建）——它写的必须是一个还没在对外服务的名字。日常增量走 ``add_index``。

    ``on_batch(已写入块数, 总块数)`` 在每个批次写完后回调一次，调用方据此把"现在处理
    到哪一本、哪一块"落库——界面上的进度显示完全靠它。回调抛异常只记日志、不打断
    索引：进度是观测手段，不该变成新的失败点。
    """
    # 与 retriever 一致：调用方传入的目录原样使用，重定向只在配置解析阶段发生
    persist_path = Path(persist_dir).expanduser().resolve()
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
        metadata=_COLLECTION_METADATA,
        embedding_function=embedding_function,
    )

    if not chunks:
        print("[embedder] 无数据需要向量化")
        return 0

    try:
        total = _write_batches(collection, chunks, on_batch)
        _verify_readable(collection)
    except Exception:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        raise

    print(f"[embedder] 全部完成: {total} 个块 → collection '{collection_name}'")
    return total


def add_index(
    chunks: list[dict],
    persist_dir: str,
    collection_name: str,
    on_batch: Callable[[int, int], None] | None = None,
    *,
    replace_document_ids: Sequence[str] = (),
) -> int:
    """把块增量写入正在服务的 collection（不删除、不重建），返回写入块数。

    与 ``build_index`` 的关键差别：这里写的就是教师正在检索的那份索引，所以任何情况下
    都不许删集合、也不许动别人的块。

    ``replace_document_ids`` 里的文档会先按 ``document_id`` 删掉旧块、再统一写入新块。
    这一步是正确性要求而不是优化：chromadb 对已存在的 id 静默忽略，不先删就会出现
    "新块没写进去、文档却被标成就绪"。删除对没有旧块的文档是 no-op（返回 deleted: 0），
    调用方不需要先查存在性。

    失败不会清理集合：由调用方按文档粒度标记失败并收拾残留（见 backend 服务层）。
    """
    # 与 retriever 一致：调用方传入的目录原样使用，重定向只在配置解析阶段发生
    persist_path = Path(persist_dir).expanduser().resolve()
    os.makedirs(str(persist_path), exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_path))

    embedding_function = FastEmbedEmbeddingFunction()
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata=_COLLECTION_METADATA,
        embedding_function=embedding_function,
    )

    if replace_document_ids:
        collection.delete(where={"document_id": {"$in": list(replace_document_ids)}})

    if not chunks:
        print("[embedder] 无数据需要向量化")
        return 0

    total = _write_batches(collection, chunks, on_batch)
    _verify_readable(collection)
    print(f"[embedder] 增量完成: {total} 个块 → collection '{collection_name}'")
    return total


def remove_document_chunks(
    persist_dir: str,
    collection_name: str,
    document_ids: Sequence[str],
) -> int:
    """按文档删除块（停用/删除的文档在增量运行里会被清出去），返回删除块数。

    集合不存在、文档没有块都是 no-op——删除是幂等的，调用方不需要先查存在性。
    这里同样不构造嵌入函数：删除不需要模型。
    """
    if not document_ids:
        return 0
    persist_path = Path(persist_dir).expanduser().resolve()
    client = chromadb.PersistentClient(path=str(persist_path))
    names = [
        item.name if hasattr(item, "name") else str(item)
        for item in client.list_collections()
    ]
    if collection_name not in names:
        return 0
    collection = client.get_collection(collection_name)
    result = collection.delete(where={"document_id": {"$in": list(document_ids)}})
    if isinstance(result, dict):
        return int(result.get("deleted") or 0)
    return 0
