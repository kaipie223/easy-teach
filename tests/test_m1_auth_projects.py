from backend.db.database import get_db
from backend.main import app
from backend.models.user import User


def register(client, email: str, display_name: str = "测试教师"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def test_auth_project_lifecycle_and_message_persistence(client):
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
