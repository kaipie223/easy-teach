import fitz
import numpy as np

from ai.rag.embedder import build_index
from ai.rag.embedding import DIRECT_EMBEDDING_MODEL, DIRECT_EMBEDDING_URL
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


def test_production_embedding_alias_uses_direct_mirror_only():
    model = next(
        item
        for item in TextEmbedding.list_supported_models()
        if item["model"] == DIRECT_EMBEDDING_MODEL
    )
    assert model["sources"]["hf"] is None
    assert model["sources"]["url"] == DIRECT_EMBEDDING_URL
    assert model["dim"] == 512


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


def test_knowledge_document_lifecycle(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
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

    def fake_build_index(chunks, persist_dir, collection_name):
        captured["chunks"] = chunks
        captured["persist_dir"] = persist_dir
        captured["collection_name"] = collection_name

    monkeypatch.setattr(knowledge_service, "build_index", fake_build_index)
    indexed = client.post("/api/v1/knowledge/index", headers=headers)
    assert indexed.status_code == 200, indexed.text
    assert indexed.json()["status"] == "ready"
    assert indexed.json()["chunk_count"] == 1
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


def test_knowledge_index_failure_is_retryable(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    _, headers = register_teacher(client, "m3-knowledge-retry@example.com")

    imported = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("retry.txt", "可重试的知识片段".encode(), "text/plain")},
        data={"enabled": "true"},
    )
    assert imported.status_code == 201, imported.text
    document_id = imported.json()["document_id"]

    def failing_build_index(chunks, persist_dir, collection_name):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(knowledge_service, "build_index", failing_build_index)
    failed = client.post("/api/v1/knowledge/index", headers=headers)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "KNOWLEDGE_INDEX_FAILED"

    status = client.get(
        "/api/v1/knowledge/documents",
        headers=headers,
        params={"include_deleted": "true"},
    )
    assert status.status_code == 200
    assert status.json()[0]["document_id"] == document_id
    assert status.json()[0]["index_status"] == "failed"

    monkeypatch.setattr(
        knowledge_service,
        "build_index",
        lambda chunks, persist_dir, collection_name: None,
    )
    retried = client.post("/api/v1/knowledge/index", headers=headers)
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"


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
