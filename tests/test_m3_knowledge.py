import fitz

from backend.core.security import get_current_user
from backend.main import app
from backend.models.user import User
from backend.schemas import RAGDocument
from backend.services import knowledge as knowledge_service


def register_teacher(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "知识库管理员"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return User(
        user_id=payload["user"]["user_id"],
        email=email,
        display_name="知识库管理员",
        role="admin",
        is_active=True,
    )


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def test_knowledge_document_lifecycle(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "knowledge_base_dir", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    admin = register_teacher(client, "m3-knowledge@example.com")
    app.dependency_overrides[get_current_user] = lambda: admin

    try:
        source_pdf = pdf_bytes("TCP 三次握手建立可靠连接")
        response = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("network.pdf", source_pdf, "application/pdf")},
            data={"collection_id": "network", "enabled": "false"},
        )
        assert response.status_code == 201, response.text
        document = response.json()
        assert document["file_type"] == "pdf"
        assert document["index_status"] == "pending"
        assert document["chunk_count"] == 1

        duplicate = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("network-copy.pdf", source_pdf, "application/pdf")},
            data={"collection_id": "network"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "KNOWLEDGE_DOCUMENT_EXISTS"

        enabled = client.patch(
            f"/api/v1/knowledge/documents/{document['document_id']}",
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["index_status"] == "pending"

        captured = {}

        def fake_build_index(chunks, persist_dir):
            captured["chunks"] = chunks
            captured["persist_dir"] = persist_dir

        monkeypatch.setattr(knowledge_service, "build_index", fake_build_index)
        indexed = client.post("/api/v1/knowledge/index")
        assert indexed.status_code == 200, indexed.text
        assert indexed.json()["status"] == "ready"
        assert indexed.json()["chunk_count"] == 1
        assert captured["chunks"][0]["metadata"]["document_id"] == document["document_id"]
        assert captured["chunks"][0]["metadata"]["locator_page"] == 1

        listed = client.get("/api/v1/knowledge/documents", params={"collection_id": "network"})
        assert listed.status_code == 200
        assert listed.json()[0]["index_status"] == "ready"
        assert listed.json()[0]["chunk_count"] == 1

        async def fake_search(query, top_k):
            return [RAGDocument(content=query, source="network.pdf", score=0.9, document_id=document["document_id"])]

        monkeypatch.setattr("backend.routers.knowledge.rag_search", fake_search)
        search = client.post("/api/v1/knowledge/search", json={"query": "可靠连接", "top_k": 3})
        assert search.status_code == 200
        assert search.json()[0]["document_id"] == document["document_id"]

        disabled = client.patch(
            f"/api/v1/knowledge/documents/{document['document_id']}",
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        filtered = client.post("/api/v1/knowledge/search", json={"query": "可靠连接", "top_k": 3})
        assert filtered.status_code == 200
        assert filtered.json() == []

        deleted = client.delete(f"/api/v1/knowledge/documents/{document['document_id']}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted_at"] is not None
        active = client.get("/api/v1/knowledge/documents")
        assert active.status_code == 200
        assert active.json() == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_knowledge_index_failure_is_retryable(client, tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "knowledge_base_dir", tmp_path / "knowledge")
    monkeypatch.setattr(settings, "chroma_persist_dir", tmp_path / "chroma")
    admin = register_teacher(client, "m3-knowledge-retry@example.com")
    app.dependency_overrides[get_current_user] = lambda: admin

    try:
        imported = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("retry.txt", "可重试的知识片段".encode(), "text/plain")},
            data={"enabled": "true"},
        )
        assert imported.status_code == 201, imported.text
        document_id = imported.json()["document_id"]

        def failing_build_index(chunks, persist_dir):
            raise RuntimeError("embedding service unavailable")

        monkeypatch.setattr(knowledge_service, "build_index", failing_build_index)
        failed = client.post("/api/v1/knowledge/index")
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "KNOWLEDGE_INDEX_FAILED"

        status = client.get("/api/v1/knowledge/documents", params={"include_deleted": "true"})
        assert status.status_code == 200
        assert status.json()[0]["document_id"] == document_id
        assert status.json()[0]["index_status"] == "failed"

        monkeypatch.setattr(knowledge_service, "build_index", lambda chunks, persist_dir: None)
        retried = client.post("/api/v1/knowledge/index")
        assert retried.status_code == 200
        assert retried.json()["status"] == "ready"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
