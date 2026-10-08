import os
import threading
import time

import fitz
import numpy as np

from ai.rag.embedder import build_index
from ai.rag.embedding import DEFAULT_EMBEDDING_MODEL
from ai.rag.retriever import RAGRetriever
from fastembed import TextEmbedding
from backend.db.database import get_db
from backend.main import app
from backend.models.user import User
from backend.schemas import RAGDocument
from backend.services import knowledge as knowledge_service
from backend.services.knowledge_scope import active_knowledge_collection_name


def register_teacher(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "测试教师"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def wait_for_index(client, headers, owner_id: str, *, timeout: float = 30.0) -> dict:
    """等后台重建真的跑完，返回结束后的那一帧进度。

    启动接口只负责启动（202）：成功与失败都是*之后*才发生的，所以测试也必须走真实的
    客户端路径——轮询进度接口。但光等 stage 变 idle 不够：块写完的文档当场落库
    ready，而激活 collection（切 active 指针）发生在 build_index 返回之后，于是
    "文档全 ready、指针还没切"的窗口里进度已经是 idle 了。进程内的"重建在跑"标记
    在 rebuild_index 返回后才摘除（见 _run_rebuild_in_thread），以它为准才能保证
    断言读到的是最终状态。断言失败时把最后一帧带出来，否则"卡住了"和"跑完了但状态
    不对"看起来一模一样。
    """
    deadline = time.monotonic() + timeout
    body: dict = {}
    while True:
        if not knowledge_service.is_rebuild_running(owner_id):
            body = client.get("/api/v1/knowledge/index/progress", headers=headers).json()
            if body["stage"] in {"idle", "stalled"}:
                return body
        assert time.monotonic() < deadline, f"重建没有在 {timeout}s 内结束：{body}"
        time.sleep(0.02)


class RecordingEmbeddingModel:
    """假嵌入模型：记录每一段真正被嵌入的文本，可对包含毒药标记的文本抛错。

    "这次索引到底嵌了哪些块"只有嵌入调用本身能回答：重写一遍同样的 id 与同样的内容，
    事后翻 collection 是看不出来的。文本计数就是那条唯一证据。
    """

    def __init__(self, *, poison: str | None = None):
        self.texts: list[str] = []
        self.poison = poison

    def embed(self, texts, batch_size=64):
        del batch_size
        for text in texts:
            if self.poison and self.poison in text:
                raise RuntimeError("embedding model unavailable")
            self.texts.append(text)
            yield np.array([1.0, 0.5, 0.25], dtype=np.float32)

    @property
    def document_texts(self) -> list[str]:
        """只保留文档块。

        索引跑完会顺手 warm_retriever（嵌入一次最小查询 "预热"），它只回答"检索器能不能
        用"，不属于本次索引的工作量；不滤掉它，块数断言就会多看一条。
        """
        return [text for text in self.texts if text != "预热"]


def install_recording_embeddings(monkeypatch, *, poison: str | None = None) -> RecordingEmbeddingModel:
    model = RecordingEmbeddingModel(poison=poison)
    monkeypatch.setattr(
        "ai.rag.embedding._get_embedding_model",
        lambda model_name, cache_dir, threads: model,
    )
    return model


def patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    # 后台线程自建会话：不换掉 SessionLocal 就会连上真实的库
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    return settings


def upload_plain_document(client, headers, *, title: str, content: str) -> dict:
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": (f"{title}.txt", content.encode(), "text/plain")},
        data={"enabled": "true", "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


def active_collection_metadatas(owner_id: str, persist_dir) -> list[dict]:
    """直接读 active collection 里每条块的 metadata。

    "谁的块还在、这条块的 source 是谁"只能看集合本身：文档状态与进度都是推导值，
    两者对不上时不会有任何一处报错。
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(persist_dir))
    collection = client.get_collection(active_knowledge_collection_name(owner_id))
    return collection.get(include=["metadatas"])["metadatas"] or []


def spy_on_add_index(monkeypatch, client, headers):
    """包一层增量写入：记录每轮收到的块，并在运行中取一帧进度快照。

    运行中这一帧只能在嵌入回调里取：索引跑在后台线程，测试线程没有别的窗口能看到
    "本次要处理几本、现在轮到第几本"。
    """
    real_add_index = knowledge_service.add_index
    calls: list[dict] = []
    snapshots: list[dict] = []

    def wrapper(chunks, persist_dir, collection_name, on_batch=None, **kwargs):
        calls.append(
            {
                "document_ids": {chunk["metadata"]["document_id"] for chunk in chunks},
                "replace": list(kwargs.get("replace_document_ids") or []),
                "collection_name": collection_name,
            }
        )

        def record_on_batch(written, total):
            if not snapshots:
                snapshots.append(
                    client.get("/api/v1/knowledge/index/progress", headers=headers).json()
                )
            if on_batch is not None:
                on_batch(written, total)

        return real_add_index(
            chunks,
            persist_dir,
            collection_name,
            on_batch=record_on_batch,
            **kwargs,
        )

    monkeypatch.setattr(knowledge_service, "add_index", wrapper)
    return calls, snapshots


def test_embedding_model_keeps_a_mirror_capable_source():
    """模型条目必须保留 hf 源，否则镜像下载这条路等于不存在。

    老实现把 BAAI/bge-small-zh-v1.5 重定向成一个只有 GCS url 的自定义别名，
    把 fastembed 的"先试 HuggingFace、失败再退 GCS"砍掉一半。两个官方源在国内
    网络都不可达时，模型永远下不下来，建索引必然失败（实测报
    "Could not load model ... from any source"）。这里钉住：别再把它的 hf 源去掉。
    """
    model = next(
        item
        for item in TextEmbedding.list_supported_models()
        if item["model"] == DEFAULT_EMBEDDING_MODEL
    )
    assert model["sources"].get("hf"), "必须保留 hf 源，否则只能走不可达的 GCS"
    assert model["dim"] == 512


def test_page_marked_text_keeps_page_numbers(tmp_path):
    """带 [[page N]] 标记的文本（扫描件 OCR 的产物）要产出页码定位与体检口径。

    没有页码的话，引用只能显示一个字符偏移，对老师毫无意义；页数与"有字页数"是
    "这本到底有没有内容"的唯一可见指标（见 scripts/ocr_textbooks.py）。
    """
    from backend.services.knowledge import _parse_text

    path = tmp_path / "book.txt"
    path.write_text(
        "[[page 1]]\n第一章 引言\n" + "内容" * 400 + "\n\n[[page 2]]\n\n[[page 3]]\n第三章 结论\n短",
        encoding="utf-8",
    )
    parsed = _parse_text(path, max_chars=600)
    assert parsed.result_json["page_count"] == 3
    assert parsed.result_json["text_pages"] == 2  # 第 2 页 OCR 没识别出字
    assert sorted({chunk.locator["page"] for chunk in parsed.chunks}) == [1, 3]
    assert all("offset" not in chunk.locator for chunk in parsed.chunks)
    first_page = [chunk for chunk in parsed.chunks if chunk.locator["page"] == 1]
    assert len(first_page) > 1
    assert [chunk.locator.get("part") for chunk in first_page] == [1, 2]


def test_plain_text_keeps_offset_locators(tmp_path):
    """没有页码标记的纯文本仍是老行为（按字符偏移），别把已有的文本导入改坏。"""
    from backend.services.knowledge import _parse_text

    path = tmp_path / "plain.txt"
    path.write_text("A" * 1500, encoding="utf-8")
    parsed = _parse_text(path, max_chars=600)
    assert "page_count" not in parsed.result_json
    assert [chunk.locator["offset"] for chunk in parsed.chunks] == [0, 600, 1200]


def test_hf_endpoint_is_applied_before_fastembed_import():
    """镜像地址必须在导入 fastembed 之前生效。

    huggingface_hub 只在 import 时读一次 HF_ENDPOINT，之后再改环境变量就晚了；
    所以 embedding 模块必须在 import fastembed 之前把它写进环境变量。
    """
    from backend.config import settings

    assert settings.hf_endpoint.startswith("https://")
    assert os.environ.get("HF_ENDPOINT") == settings.hf_endpoint


def test_fastembed_adapter_builds_and_queries_chroma(tmp_path, monkeypatch):
    class FakeEmbeddingModel:
        def embed(self, texts, batch_size=64):
            del batch_size
            for text in texts:
                yield np.array(
                    [
                        1.0 if "浮力" in text else 0.0,
                        1.0 if "网络" in text else 0.0,
                        0.25,
                    ],
                    dtype=np.float32,
                )

    monkeypatch.setattr(
        "ai.rag.embedding._get_embedding_model",
        lambda model_name, cache_dir, threads: FakeEmbeddingModel(),
    )
    collection_name = "knowledge_user_test_adapter"
    build_index(
        [
            {"content": "浮力等于排开液体的重力", "source": "physics.pdf", "chunk_index": 0},
            {"content": "计算机网络采用分层结构", "source": "network.pdf", "chunk_index": 1},
        ],
        tmp_path / "chroma",
        collection_name,
    )

    results = RAGRetriever(tmp_path / "chroma", collection_name).search("浮力", top_k=1)
    assert len(results) == 1
    assert results[0].source == "physics.pdf"
    assert "浮力" in results[0].content


def test_knowledge_document_lifecycle(client, db_session_factory, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    # 重建跑在后台线程里、自建会话：conftest 只覆盖了 get_db，不重定向 SessionLocal
    # 的话线程会连上真实的库（那里没有这个测试账号）。
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    teacher, headers = register_teacher(client, "m3-knowledge@example.com")

    source_pdf = pdf_bytes("TCP 三次握手建立可靠连接")
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("network.pdf", source_pdf, "application/pdf")},
        data={"collection_id": "network", "enabled": "false"},
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["owner_id"] == teacher["user"]["user_id"]
    assert document["file_type"] == "pdf"
    assert document["index_status"] == "pending"
    assert document["chunk_count"] == 1

    duplicate = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("network-copy.pdf", source_pdf, "application/pdf")},
        data={"collection_id": "network"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "KNOWLEDGE_DOCUMENT_EXISTS"

    enabled = client.patch(
        f"/api/v1/knowledge/documents/{document['document_id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["index_status"] == "pending"

    captured = {}

    def fake_build_index(chunks, persist_dir, collection_name, on_batch=None):
        captured["chunks"] = chunks
        captured["persist_dir"] = persist_dir
        captured["collection_name"] = collection_name
        # 真实实现每 100 块回调一次；这里是假实现，一次写完就报满，用来驱动
        # "逐本落库 ready"这条路径。
        if on_batch is not None:
            on_batch(len(chunks), len(chunks))

    monkeypatch.setattr(knowledge_service, "build_index", fake_build_index)
    indexed = client.post("/api/v1/knowledge/index", headers=headers)
    # 接口只负责启动：结果不看这个响应，看进度与文档状态
    assert indexed.status_code == 202, indexed.text
    assert indexed.json()["status"] == "indexing"
    assert indexed.json()["started"] is True
    progress = wait_for_index(client, headers, teacher["user"]["user_id"])
    assert progress["ready_documents"] == 1
    assert progress["written_chunks"] == 1
    assert progress["percent"] == 100
    assert f"knowledge_user_{teacher['user']['user_id']}_" in captured["collection_name"]
    assert active_knowledge_collection_name(teacher["user"]["user_id"]) == captured["collection_name"]
    assert captured["chunks"][0]["metadata"]["document_id"] == document["document_id"]
    assert captured["chunks"][0]["metadata"]["locator_page"] == 1

    listed = client.get(
        "/api/v1/knowledge/documents",
        headers=headers,
        params={"collection_id": "network"},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["index_status"] == "ready"
    assert listed.json()[0]["chunk_count"] == 1

    async def fake_search(query, top_k, *, owner_id):
        assert owner_id == teacher["user"]["user_id"]
        return [
            RAGDocument(
                content=query,
                source="network.pdf",
                score=0.9,
                document_id=document["document_id"],
            )
        ]

    monkeypatch.setattr("backend.routers.knowledge.rag_search", fake_search)
    search = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={"query": "可靠连接", "top_k": 3},
    )
    assert search.status_code == 200
    assert search.json()[0]["document_id"] == document["document_id"]

    disabled = client.patch(
        f"/api/v1/knowledge/documents/{document['document_id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    filtered = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={"query": "可靠连接", "top_k": 3},
    )
    assert filtered.status_code == 200
    assert filtered.json() == []

    deleted = client.delete(
        f"/api/v1/knowledge/documents/{document['document_id']}",
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_at"] is not None
    assert not [path for path in (tmp_path / "data" / "knowledge").rglob("*") if path.is_file()]
    active = client.get("/api/v1/knowledge/documents", headers=headers)
    assert active.status_code == 200
    assert active.json() == []


def test_knowledge_index_failure_is_retryable(client, db_session_factory, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    teacher, headers = register_teacher(client, "m3-knowledge-retry@example.com")

    imported = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("retry.txt", "可重试的知识片段".encode(), "text/plain")},
        data={"enabled": "true"},
    )
    assert imported.status_code == 201, imported.text
    document_id = imported.json()["document_id"]

    def failing_build_index(chunks, persist_dir, collection_name, on_batch=None):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(knowledge_service, "build_index", failing_build_index)
    failed = client.post("/api/v1/knowledge/index", headers=headers)
    # 失败不再是这个接口的状态码：接口在重建开始前就返回了。失败必须能从文档上读到，
    # 界面正是靠它显示"失败，需重试"。
    assert failed.status_code == 202
    wait_for_index(client, headers, teacher["user"]["user_id"])

    status = client.get(
        "/api/v1/knowledge/documents",
        headers=headers,
        params={"include_deleted": "true"},
    )
    assert status.status_code == 200
    assert status.json()[0]["document_id"] == document_id
    assert status.json()[0]["index_status"] == "failed"
    assert status.json()[0]["error_code"] == "KNOWLEDGE_INDEX_FAILED"

    monkeypatch.setattr(
        knowledge_service,
        "build_index",
        lambda chunks, persist_dir, collection_name, on_batch=None: None,
    )
    retried = client.post("/api/v1/knowledge/index", headers=headers)
    assert retried.status_code == 202
    assert retried.json()["started"] is True
    assert wait_for_index(client, headers, teacher["user"]["user_id"])["ready_documents"] == 1


def test_second_rebuild_request_joins_the_running_one(
    client, db_session_factory, tmp_path, monkeypatch
):
    """已有重建在跑时，再点一次：不重复启动，也不能把请求卡住。

    重建要十几分钟。没有这层判断，第二次点击会一直阻塞在同一把重建锁上（开发环境是
    进程内 Lock，等到第一次跑完为止），界面表现为"点一下没反应"——比报错更难解释。
    """
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    teacher, headers = register_teacher(client, "m3-knowledge-join@example.com")

    created = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("join.txt", "慢一点的重建".encode(), "text/plain")},
        data={"enabled": "true"},
    )
    assert created.status_code == 201, created.text

    entered = threading.Event()
    release = threading.Event()

    def slow_build_index(chunks, persist_dir, collection_name, on_batch=None):
        entered.set()
        assert release.wait(timeout=10), "测试没有放行这次重建"
        if on_batch is not None:
            on_batch(len(chunks), len(chunks))

    monkeypatch.setattr(knowledge_service, "build_index", slow_build_index)
    first = client.post("/api/v1/knowledge/index", headers=headers)
    assert first.status_code == 202
    assert first.json()["started"] is True
    assert entered.wait(timeout=10), "后台重建没有真的跑起来"

    second = client.post("/api/v1/knowledge/index", headers=headers)
    assert second.status_code == 202
    assert second.json()["started"] is False

    release.set()
    assert wait_for_index(client, headers, teacher["user"]["user_id"])["ready_documents"] == 1


def test_private_knowledge_isolated_and_admin_forbidden(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    alice, alice_headers = register_teacher(client, "kb-alice@example.com")
    _, bob_headers = register_teacher(client, "kb-bob@example.com")
    _, admin_headers = register_teacher(client, "kb-admin@example.com")

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        admin = db.query(User).filter(User.email == "kb-admin@example.com").one()
        admin.role = "admin"
        db.commit()
    finally:
        db.close()

    content = "教师私有资料".encode()
    alice_document = client.post(
        "/api/v1/knowledge/documents",
        headers=alice_headers,
        files={"file": ("private.txt", content, "text/plain")},
        data={"collection_id": "shared-name"},
    )
    assert alice_document.status_code == 201, alice_document.text
    document_id = alice_document.json()["document_id"]
    assert alice_document.json()["owner_id"] == alice["user"]["user_id"]

    bob_same_content = client.post(
        "/api/v1/knowledge/documents",
        headers=bob_headers,
        files={"file": ("private.txt", content, "text/plain")},
        data={"collection_id": "shared-name"},
    )
    assert bob_same_content.status_code == 201, bob_same_content.text
    assert client.get("/api/v1/knowledge/documents", headers=bob_headers).json() == [
        bob_same_content.json()
    ]
    assert client.patch(
        f"/api/v1/knowledge/documents/{document_id}",
        headers=bob_headers,
        json={"enabled": True},
    ).status_code == 404
    assert client.delete(
        f"/api/v1/knowledge/documents/{document_id}",
        headers=bob_headers,
    ).status_code == 404

    assert client.get("/api/v1/knowledge/documents", headers=admin_headers).status_code == 403
    assert client.post(
        "/api/v1/knowledge/documents",
        headers=admin_headers,
        files={"file": ("admin.txt", content, "text/plain")},
    ).status_code == 403


def test_knowledge_index_progress_is_idle_when_nothing_is_running(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    _, headers = register_teacher(client, "m3-knowledge-idle@example.com")

    response = client.get("/api/v1/knowledge/index/progress", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["running"] is False
    assert body["stalled"] is False
    assert body["stage"] == "idle"
    assert body["total_documents"] == 0
    assert body["current_document_title"] == ""
    assert body["percent"] == 0


def test_knowledge_index_progress_reports_the_document_being_processed(
    client, db_session_factory, tmp_path, monkeypatch
):
    """进度要能回答"现在在处理哪一本、写到第几块"。

    重建跑在后台，进度只能靠这个接口轮询，所以断点必须取在真正的中间态
    （处理到第二本一半）而不是结束态——否则测的是"跑完了显示 100%"这种废断言。
    """
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    teacher, headers = register_teacher(client, "m3-knowledge-progress@example.com")

    document_ids = []
    for index in range(2):
        created = client.post(
            "/api/v1/knowledge/documents",
            headers=headers,
            files={"file": (f"book-{index}.txt", f"第 {index} 本的内容".encode(), "text/plain")},
            data={"enabled": "true"},
        )
        assert created.status_code == 201, created.text
        document_ids.append(created.json()["document_id"])

    snapshots = []

    def build_halfway(chunks, persist_dir, collection_name, on_batch=None):
        total = len(chunks)
        assert total == 2, chunks
        if on_batch is not None:
            # 第一本写完（第 1 块）、第二本刚轮到但还没写入
            on_batch(1, total)
            snapshots.append(
                client.get("/api/v1/knowledge/index/progress", headers=headers).json()
            )

    monkeypatch.setattr(knowledge_service, "build_index", build_halfway)
    indexed = client.post("/api/v1/knowledge/index", headers=headers)
    assert indexed.status_code == 202, indexed.text
    wait_for_index(client, headers, teacher["user"]["user_id"])
    assert snapshots, "没有采集到运行中的进度快照"

    midway = snapshots[0]
    assert midway["running"] is True
    assert midway["stalled"] is False
    assert midway["stage"] == "embedding"
    assert midway["total_documents"] == 2
    assert midway["ready_documents"] == 1
    assert midway["current_document_id"] == document_ids[1]
    assert midway["current_document_title"] == "book-1"
    assert midway["current_chunks_written"] == 0
    assert midway["current_chunks_total"] == 1
    assert midway["written_chunks"] == 1
    assert midway["total_chunks"] == 2
    assert midway["percent"] == 50

    # 跑完之后同一接口必须回到"完成"：进度不能停在中间态
    finished = client.get("/api/v1/knowledge/index/progress", headers=headers).json()
    assert finished["running"] is False
    assert finished["stage"] == "idle"
    assert finished["ready_documents"] == 2
    assert finished["written_chunks"] == 2
    assert finished["percent"] == 100


def test_knowledge_index_progress_reports_a_stalled_run(
    client, db_session_factory, tmp_path, monkeypatch
):
    """中途崩掉的重建不能永远显示"正在处理"，否则教师只能靠猜。"""
    from datetime import datetime, timedelta, timezone

    from backend.config import settings
    from backend.models.knowledge import KnowledgeDocument

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    _, headers = register_teacher(client, "m3-knowledge-stalled@example.com")

    created = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("stuck.txt", "被中断的内容".encode(), "text/plain")},
        data={"enabled": "true"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["document_id"]

    # 直接把状态改成"一小时前开始索引"，模拟进程被杀
    db = db_session_factory()
    try:
        document = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.document_id == document_id)
            .first()
        )
        document.index_status = "indexing"
        document.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/knowledge/index/progress", headers=headers).json()

    assert body["stalled"] is True
    assert body["running"] is False
    assert body["stage"] == "stalled"
    # 卡住的这一本仍要能报出来，教师才知道是哪一本需要重试
    assert body["current_document_id"] == document_id
    assert body["current_document_title"] == "stuck"


def test_rebuild_index_persists_progress_through_the_real_embedder(
    client, db_session_factory, tmp_path, monkeypatch
):
    """真实走一遍嵌入器：回调签名与落库必须真的对上。

    这里刻意**不替换** build_index，只替换嵌入模型。回调一旦抛异常，嵌入器会吞掉它
    （进度是观测手段，不该让重建失败），于是"少收一个参数"这种错误在界面上只表现为
    "进度永远停在准备中"——只有真实跑一遍才拦得住。
    """
    from backend.config import settings
    from backend.models.knowledge import KnowledgeDocument

    class FakeEmbeddingModel:
        def embed(self, texts, batch_size=64):
            del batch_size
            for _text in texts:
                yield np.array([1.0, 0.5, 0.25], dtype=np.float32)

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    monkeypatch.setattr(
        "ai.rag.embedding._get_embedding_model",
        lambda model_name, cache_dir, threads: FakeEmbeddingModel(),
    )
    teacher, headers = register_teacher(client, "m3-knowledge-real-progress@example.com")

    created = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("real.txt", "真实嵌入进度".encode(), "text/plain")},
        data={"enabled": "true"},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["document_id"]

    indexed = client.post("/api/v1/knowledge/index", headers=headers)
    assert indexed.status_code == 202, indexed.text
    wait_for_index(client, headers, teacher["user"]["user_id"])

    db = db_session_factory()
    try:
        document = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.document_id == document_id)
            .first()
        )
        metadata = document.metadata_json or {}
        status = document.index_status
    finally:
        db.close()

    # 回调真的跑过：chunk_total 在嵌入前落库，chunks_written 由回调写入。
    # 少了这一步，进度接口永远只会显示"准备中"而没有任何报错。
    assert status == "ready"
    assert metadata["chunk_total"] == 1
    assert metadata["chunks_written"] == 1
    assert metadata["chunk_count"] == 1

    progress = client.get("/api/v1/knowledge/index/progress", headers=headers).json()
    assert progress["running"] is False
    assert progress["ready_documents"] == 1
    assert progress["written_chunks"] == 1
    assert progress["percent"] == 100


def test_incremental_index_only_embeds_the_new_document(
    client, db_session_factory, tmp_path, monkeypatch
):
    """导入新教材后再点"建立索引"：只嵌新导入的这一本，老书一个块都不重嵌。

    这是增量改造的核心承诺：以前每加一本新书，该账号所有已启用文档都要重新向量化
    （实测一次 14–74 分钟）。
    """
    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    model = install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-incremental@example.com")
    owner_id = teacher["user"]["user_id"]

    first = upload_plain_document(client, headers, title="旧教材", content="甲" * 30)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)
    assert model.document_texts == ["甲" * 30]

    model.texts.clear()
    second = upload_plain_document(client, headers, title="新教材", content="乙" * 30)
    calls, snapshots = spy_on_add_index(monkeypatch, client, headers)
    response = client.post("/api/v1/knowledge/index", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["work_pending"] is True
    wait_for_index(client, headers, owner_id)

    # 唯一的证据：这一轮只给新书做了向量化
    assert model.document_texts == ["乙" * 30], model.document_texts
    assert calls[0]["document_ids"] == {second["document_id"]}
    assert calls[0]["replace"] == [second["document_id"]]
    # 运行中的进度按"本次运行"报：这一轮只有一本要处理，就是它
    assert snapshots[0]["run_documents"] == 1
    assert snapshots[0]["current_document_position"] == 1
    assert snapshots[0]["current_document_id"] == second["document_id"]
    assert snapshots[0]["total_documents"] == 2

    # 跳过老书不等于把它丢掉：两本的块都在集合里
    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert {meta["document_id"] for meta in metadatas} == {
        first["document_id"],
        second["document_id"],
    }


def test_repeat_index_click_is_a_noop(client, db_session_factory, tmp_path, monkeypatch):
    """全部就绪后再点一次：不启动后台任务、不嵌任何东西、文档一个字段都不变。"""
    patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    model = install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-noop@example.com")
    owner_id = teacher["user"]["user_id"]

    upload_plain_document(client, headers, title="唯一一本", content="内容" * 10)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)
    before = client.get("/api/v1/knowledge/documents", headers=headers).json()

    calls: list[str] = []
    monkeypatch.setattr(knowledge_service, "build_index", lambda *a, **k: calls.append("build"))
    monkeypatch.setattr(knowledge_service, "add_index", lambda *a, **k: calls.append("add"))
    monkeypatch.setattr(
        knowledge_service, "remove_document_chunks", lambda *a, **k: calls.append("remove")
    )
    model.texts.clear()

    response = client.post("/api/v1/knowledge/index", headers=headers)

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "indexing", "started": True, "work_pending": False}
    assert calls == []
    assert model.document_texts == []
    # 守卫要当场摘掉：否则这个账号之后的每一次索引都会被回答"已有一次在跑"
    assert knowledge_service.is_rebuild_running(owner_id) is False
    assert client.get("/api/v1/knowledge/documents", headers=headers).json() == before
    progress = client.get("/api/v1/knowledge/index/progress", headers=headers).json()
    assert progress["running"] is False
    assert progress["stage"] == "idle"
    assert progress["percent"] == 100


def test_reindexing_one_document_leaves_the_others_untouched(
    client, db_session_factory, tmp_path, monkeypatch
):
    """单本"重新索引"只重做这一本：其它书既不重嵌，块也原样留在集合里。"""
    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    model = install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-one-doc@example.com")
    owner_id = teacher["user"]["user_id"]

    first = upload_plain_document(client, headers, title="第一本", content="甲" * 30)
    second = upload_plain_document(client, headers, title="第二本", content="乙" * 30)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)
    before = {
        meta["document_id"]: meta["chunk_index"]
        for meta in active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    }

    model.texts.clear()
    calls, snapshots = spy_on_add_index(monkeypatch, client, headers)
    response = client.post(
        f"/api/v1/knowledge/documents/{second['document_id']}/index", headers=headers
    )
    assert response.status_code == 202, response.text
    assert response.json()["work_pending"] is True
    wait_for_index(client, headers, owner_id)

    assert model.document_texts == ["乙" * 30], model.document_texts
    assert calls[0]["document_ids"] == {second["document_id"]}
    assert snapshots[0]["run_documents"] == 1
    assert snapshots[0]["current_document_position"] == 1

    after = {
        meta["document_id"]: meta["chunk_index"]
        for meta in active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    }
    assert after == before
    assert set(after) == {first["document_id"], second["document_id"]}


def test_disabling_a_document_removes_its_chunks_on_the_next_index(
    client, db_session_factory, tmp_path, monkeypatch
):
    """停用的书不再作为检索来源，重新索引时它的块要真的从集合里清掉。"""
    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-disabled@example.com")
    owner_id = teacher["user"]["user_id"]

    first = upload_plain_document(client, headers, title="将要停用", content="甲" * 30)
    second = upload_plain_document(client, headers, title="保留", content="乙" * 30)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)

    disabled = client.patch(
        f"/api/v1/knowledge/documents/{first['document_id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["index_status"] == "pending"

    response = client.post("/api/v1/knowledge/index", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["work_pending"] is True
    wait_for_index(client, headers, owner_id)

    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert {meta["document_id"] for meta in metadatas} == {second["document_id"]}


def test_incremental_failure_only_fails_the_unfinished_document(
    client, db_session_factory, tmp_path, monkeypatch
):
    """增量运行里一本嵌入失败：只标它自己，已就绪的老书保持 ready、块也不动。

    以前是"所有候选一起标 failed"——一本新书嵌入失败，会把一整套本来好好的索引
    在界面上显示成全部失败。
    """
    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    install_recording_embeddings(monkeypatch, poison="毒药")
    teacher, headers = register_teacher(client, "m3-knowledge-partial-fail@example.com")
    owner_id = teacher["user"]["user_id"]

    first = upload_plain_document(client, headers, title="已就绪", content="安全内容" * 10)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)

    upload_plain_document(client, headers, title="会失败的", content="毒药内容" * 10)
    response = client.post("/api/v1/knowledge/index", headers=headers)
    assert response.status_code == 202, response.text
    wait_for_index(client, headers, owner_id)

    documents = client.get("/api/v1/knowledge/documents", headers=headers).json()
    by_title = {item["title"]: item for item in documents}
    assert by_title["已就绪"]["index_status"] == "ready"
    assert by_title["会失败的"]["index_status"] == "failed"
    assert by_title["会失败的"]["error_code"] == "KNOWLEDGE_INDEX_FAILED"

    # 老书的块原样留着；失败那本不带进半成品
    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert {meta["document_id"] for meta in metadatas} == {first["document_id"]}

    progress = client.get("/api/v1/knowledge/index/progress", headers=headers).json()
    assert progress["running"] is False
    assert progress["ready_documents"] == 1
    assert progress["failed_documents"] == 1


def test_missing_collection_falls_back_to_a_full_rebuild(
    client, db_session_factory, tmp_path, monkeypatch
):
    """集合被删掉后不能"增量"：那会把"库里没有旧块"当成"已经是最新"。

    这条自愈路径同时也是换嵌入模型后的逃生口——staged 全量重建天然免疫维度不一致。
    """
    import chromadb

    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-selfheal@example.com")
    owner_id = teacher["user"]["user_id"]

    first = upload_plain_document(client, headers, title="第一本", content="甲" * 30)
    second = upload_plain_document(client, headers, title="第二本", content="乙" * 30)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)

    old_name = active_knowledge_collection_name(owner_id)
    chromadb.PersistentClient(path=str(settings.chroma_persist_dir)).delete_collection(old_name)

    calls, _ = spy_on_add_index(monkeypatch, client, headers)
    response = client.post("/api/v1/knowledge/index", headers=headers)
    assert response.status_code == 202, response.text
    wait_for_index(client, headers, owner_id)

    # 走的是全量分支（建新集合 + 切指针），而不是往一个不存在的集合里追加
    assert calls == []
    new_name = active_knowledge_collection_name(owner_id)
    assert new_name != old_name
    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert {meta["document_id"] for meta in metadatas} == {
        first["document_id"],
        second["document_id"],
    }
    documents = client.get("/api/v1/knowledge/documents", headers=headers).json()
    assert {item["index_status"] for item in documents} == {"ready"}
    assert {item["index_namespace"] for item in documents} == {new_name}


def test_renaming_a_ready_document_marks_it_for_reindex(
    client, db_session_factory, tmp_path, monkeypatch
):
    """标题改了要重新索引：集合里的 source 是旧标题，不重嵌会一直显示旧名字。

    顺带钉住增量写入"先按 document_id 删、再写"的顺序：chromadb 对已存在的 id 是
    静默忽略，少了这一步，重嵌只会看起来成功而集合里还是旧元数据。
    """
    settings = patch_knowledge_storage(monkeypatch, tmp_path, db_session_factory)
    install_recording_embeddings(monkeypatch)
    teacher, headers = register_teacher(client, "m3-knowledge-rename@example.com")
    owner_id = teacher["user"]["user_id"]

    document = upload_plain_document(client, headers, title="旧标题", content="内容" * 10)
    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)
    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert [meta["source"] for meta in metadatas] == ["旧标题"]

    renamed = client.patch(
        f"/api/v1/knowledge/documents/{document['document_id']}",
        headers=headers,
        json={"title": "新标题"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["index_status"] == "pending"
    assert renamed.json()["index_namespace"] is None

    assert client.post("/api/v1/knowledge/index", headers=headers).status_code == 202
    wait_for_index(client, headers, owner_id)

    metadatas = active_collection_metadatas(owner_id, settings.chroma_persist_dir)
    assert [meta["source"] for meta in metadatas] == ["新标题"]
