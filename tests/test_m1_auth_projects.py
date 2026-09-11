from backend.db.database import get_db
from backend.main import app
from backend.models.session import ChatMessage
from backend.models.user import User
from backend.services.intent import IntentServiceError


def register(client, email: str, display_name: str = "测试教师"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def test_auth_project_lifecycle_and_message_persistence(client, stub_intent_analyzer):
    payload, headers = register(client, "teacher@example.com")

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user_id"] == payload["user"]["user_id"]

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": "TEACHER@example.com", "password": "password123", "display_name": "重复"},
    )
    assert duplicate.status_code == 409

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "Python 入门", "scenario": "大一新生"},
    )
    assert project.status_code == 201
    project_data = project.json()
    project_id = project_data["project_id"]
    assert project_data["status"] == "active"

    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "Python 入门", "project_id": project_id},
    )
    assert session.status_code == 201
    session_id = session.json()["session_id"]
    assert session.json()["project_id"] == project_id

    chat = client.post(
        f"/api/v1/sessions/{session_id}/chat",
        headers=headers,
        json={"message": "设计一节 Python 入门课"},
    )
    assert chat.status_code == 200
    assert "event: question" in chat.text

    refreshed = client.get(f"/api/v1/sessions/{session_id}", headers=headers)
    assert refreshed.status_code == 200
    messages = refreshed.json()["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "设计一节 Python 入门课"
    assert any(item["role"] == "assistant" for item in messages)

    archived = client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["status"] == "deleted"
    assert client.get(f"/api/v1/projects/{project_id}", headers=headers).status_code == 404

    listed = client.get("/api/v1/projects", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []

    restored = client.post(f"/api/v1/projects/{project_id}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert client.get(f"/api/v1/projects/{project_id}", headers=headers).status_code == 200


def test_project_and_session_are_isolated_between_users(client):
    _, alice_headers = register(client, "alice@example.com", "Alice")
    _, bob_headers = register(client, "bob@example.com", "Bob")

    project = client.post(
        "/api/v1/projects",
        headers=alice_headers,
        json={"title": "Alice 的项目"},
    )
    project_id = project.json()["project_id"]
    session = client.post(
        "/api/v1/sessions",
        headers=alice_headers,
        json={"subject": "Alice", "project_id": project_id},
    )
    session_id = session.json()["session_id"]

    assert client.get(f"/api/v1/projects/{project_id}", headers=bob_headers).status_code == 404
    assert client.get(f"/api/v1/sessions/{session_id}", headers=bob_headers).status_code == 404
    assert client.get("/api/v1/projects", headers=bob_headers).json() == []


def test_new_session_starts_ai_conversation_once(client, stub_intent_analyzer):
    _, headers = register(client, "bootstrap@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "三角函数图像与性质"},
    ).json()
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": project["title"], "project_id": project["project_id"]},
    ).json()

    started = client.post(f"/api/v1/sessions/{session['session_id']}/start", headers=headers)
    assert started.status_code == 200
    assert "event: question" in started.text

    repeated = client.post(f"/api/v1/sessions/{session['session_id']}/start", headers=headers)
    assert repeated.status_code == 204

    refreshed = client.get(f"/api/v1/sessions/{session['session_id']}", headers=headers).json()
    assert not [item for item in refreshed["messages"] if (item["event_data"] or {}).get("bootstrap")]
    assert any(item["role"] == "assistant" for item in refreshed["messages"])

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        bootstrap_rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session["session_id"])
            .all()
        )
        bootstrap_rows = [
            message for message in bootstrap_rows if (message.event_data or {}).get("bootstrap")
        ]
        assert len(bootstrap_rows) == 1
        assert "三角函数图像与性质" in bootstrap_rows[0].content
    finally:
        db.close()


def test_failed_initial_conversation_can_be_retried(client, stub_intent_analyzer, monkeypatch):
    _, headers = register(client, "bootstrap-retry@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "可重试课程"},
    ).json()
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": project["title"], "project_id": project["project_id"]},
    ).json()

    from backend.services import chat as chat_service

    real_orchestrator = chat_service.get_orchestrator()

    class FlakyOrchestrator:
        attempts = 0

        async def chat(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                if False:
                    yield None
                raise IntentServiceError(
                    "模型暂时不可用",
                    code="MODEL_TEMPORARILY_UNAVAILABLE",
                )
            async for event in real_orchestrator.chat(*args, **kwargs):
                yield event

    flaky = FlakyOrchestrator()
    monkeypatch.setattr(chat_service, "get_orchestrator", lambda: flaky)

    first = client.post(f"/api/v1/sessions/{session['session_id']}/start", headers=headers)
    assert first.status_code == 200
    assert "event: error" in first.text

    second = client.post(f"/api/v1/sessions/{session['session_id']}/start", headers=headers)
    assert second.status_code == 200
    assert "event: question" in second.text
    assert flaky.attempts == 2

    refreshed = client.get(f"/api/v1/sessions/{session['session_id']}", headers=headers).json()
    assert not [item for item in refreshed["messages"] if (item["event_data"] or {}).get("bootstrap")]
    assert any(item["msg_type"] == "question" for item in refreshed["messages"])


def test_admin_endpoint_requires_admin_role(client):
    _, teacher_headers = register(client, "teacher-admin-test@example.com")
    assert client.get("/api/v1/admin/users").status_code == 401
    assert client.get("/api/v1/admin/users", headers=teacher_headers).status_code == 403

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        user = db.query(User).filter(User.email == "teacher-admin-test@example.com").one()
        user.role = "admin"
        db.commit()
    finally:
        db.close()

    users = client.get("/api/v1/admin/users", headers=teacher_headers)
    assert users.status_code == 200
    assert any(item["email"] == "teacher-admin-test@example.com" for item in users.json())


def test_admin_cannot_bypass_teacher_project_ownership(client):
    _, teacher_headers = register(client, "project-owner@example.com")
    _, admin_headers = register(client, "project-admin@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=teacher_headers,
        json={"title": "教师私有项目"},
    )
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        admin = db.query(User).filter(User.email == "project-admin@example.com").one()
        admin.role = "admin"
        db.commit()
    finally:
        db.close()

    assert client.get("/api/v1/projects", headers=admin_headers).json() == []
    assert client.get(f"/api/v1/projects/{project_id}", headers=admin_headers).status_code == 404
    assert client.delete(f"/api/v1/projects/{project_id}", headers=admin_headers).status_code == 404
