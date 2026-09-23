import pytest
import os

from ai.rag.retriever import RAGRetriever
from backend.config import settings


def register(client, email: str = "m0-contract@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "测试教师"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_session_and_sse_contract(client, stub_intent_analyzer):
    headers = register(client)
    created = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"teacher_name": "测试教师", "subject": "Python"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    chat = client.post(
        f"/api/v1/sessions/{session_id}/chat",
        headers=headers,
        json={"message": "设计一节 Python 入门课"},
    )
    assert chat.status_code == 200
    assert "event: question" in chat.text
    assert '"content"' in chat.text
    assert "event: delta" in chat.text


def test_error_envelope_is_stable(client):
    response = client.get("/api/v1/files/missing-file")
    assert response.status_code == 401
    assert set(response.json()["error"]) == {
        "code",
        "message",
        "details",
        "request_id",
        "recoverable",
        "suggested_action",
    }


def test_business_endpoints_require_authentication(client):
    assert client.post("/api/v1/sessions", json={"subject": "匿名课程"}).status_code == 401
    assert client.get("/api/v1/sessions").status_code == 401
    assert client.post("/api/v1/generate", json={"session_id": "s_missing"}).status_code == 401
    assert client.get("/api/v1/tasks/task_missing/status").status_code == 401
    assert client.get("/api/v1/download/f_missing").status_code == 401


def test_runtime_paths_are_absolute():
    assert settings.data_dir.is_absolute()
    assert settings.upload_dir.is_absolute()
    assert settings.output_dir.is_absolute()
    assert settings.chroma_persist_dir.is_absolute()
    assert settings.embedding_cache_dir.is_absolute()
    if os.name == "nt":
        assert str(settings.chroma_persist_dir).isascii()


def test_empty_chroma_collection_is_reported_without_embedding_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="collection not found"):
        RAGRetriever(tmp_path, collection_name="knowledge_user_u_test")
