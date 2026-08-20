import pytest
import os

from ai.rag.retriever import RAGRetriever
from backend.config import settings


def test_session_and_sse_contract(client):
    created = client.post(
        "/api/v1/sessions",
        json={"teacher_name": "测试教师", "subject": "Python"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    chat = client.post(
        f"/api/v1/sessions/{session_id}/chat",
        json={"message": "设计一节 Python 入门课"},
    )
    assert chat.status_code == 200
    assert "event: question" in chat.text
    assert '"content"' in chat.text
    assert "event: text" in chat.text


def test_error_envelope_is_stable(client):
    response = client.get("/api/v1/files/missing-file")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {
        "code",
        "message",
        "details",
        "request_id",
        "recoverable",
        "suggested_action",
    }


def test_runtime_paths_are_absolute():
    assert settings.data_dir.is_absolute()
    assert settings.upload_dir.is_absolute()
    assert settings.output_dir.is_absolute()
    assert settings.chroma_persist_dir.is_absolute()
    if os.name == "nt":
        assert str(settings.chroma_persist_dir).isascii()


def test_empty_chroma_collection_is_reported_without_embedding_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="collection not found"):
        RAGRetriever(tmp_path)
