from backend.db.database import get_db
from backend.main import app
from backend.models.brief import TeachingBrief


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "M2 Teacher"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def complete_brief_payload(**overrides):
    payload = {
        "teaching_goal": "帮助学生理解 TCP 三次握手并能解释每次报文的作用",
        "target_audience": "大一新生",
        "duration_minutes": 45,
        "knowledge_points": [
            {
                "order": 1,
                "title": "连接建立过程",
                "difficulty": "basic",
                "key_points": ["SYN", "SYN-ACK", "ACK"],
                "examples": ["客户端与服务器建立连接"],
                "estimated_minutes": 20,
            },
            {
                "order": 2,
                "title": "报文确认机制",
                "difficulty": "intermediate",
                "key_points": ["序列号", "确认号"],
                "examples": [],
                "estimated_minutes": 15,
            },
        ],
        "logic_flow": ["问题导入", "过程讲解", "例题练习", "总结"],
        "teaching_focus": "三次报文交换的时序和作用",
        "teaching_difficulties": "区分 SYN 与 ACK 的含义",
        "output_types": ["pptx", "docx", "html"],
        "interaction_ideas": "让学生根据时序图补全报文",
    }
    payload.update(overrides)
    return payload


def test_project_messages_persist_sse_events_and_brief(client):
    headers = register(client, "m2-events@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "TCP Lesson", "scenario": "First-year students"},
    )
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/messages",
        headers=headers,
        json={"message": "Design a TCP three-way handshake lesson"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: text" in response.text
    assert "event: question" in response.text

    brief = client.get(f"/api/v1/projects/{project_id}/brief", headers=headers)
    assert brief.status_code == 200
    brief_data = brief.json()
    assert brief_data["version"] == 1
    assert brief_data["status"] == "draft"
    assert brief_data["is_complete"] is False
    assert brief_data["teaching_goal"] == "Design a TCP three-way handshake lesson"

    session_id = client.get(f"/api/v1/projects/{project_id}", headers=headers).json()["session_id"]
    generation = client.post(
        "/api/v1/generate",
        headers=headers,
        json={"session_id": session_id},
    )
    assert generation.status_code == 409
    assert generation.json()["error"]["code"] == "BRIEF_NOT_CONFIRMED"

    session = client.get(f"/api/v1/sessions/{session_id}", headers=headers)
    assert session.status_code == 200
    messages = session.json()["messages"]
    assert [message["role"] for message in messages[:2]] == ["user", "assistant"]
    question = next(message for message in messages if message["msg_type"] == "question")
    assert question["event_data"]["prompt"]
    assert "missing_info" in question["event_data"]
    assert session.json()["intent_state"] == "probing"
    assert session.json()["brief_id"] == brief_data["brief_id"]


def test_teaching_brief_validation_confirmation_and_versioning(client):
    headers = register(client, "m2-brief@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "Versioned Lesson"},
    )
    project_id = project.json()["project_id"]

    message = client.post(
        f"/api/v1/projects/{project_id}/messages",
        headers=headers,
        json={"message": "Create a lesson about network connections"},
    )
    assert message.status_code == 200

    incomplete = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": 1},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "BRIEF_INCOMPLETE"
    assert incomplete.json()["error"]["details"]["missing_fields"]

    updated = client.patch(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json=complete_brief_payload(),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_complete"] is True
    assert updated.json()["status"] == "draft"
    assert updated.json()["source_refs"]["teaching_focus"] == "teacher"

    confirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": updated.json()["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    first_version = confirmed.json()
    assert first_version["version"] == 1
    assert first_version["status"] == "confirmed"
    assert first_version["confirmed_at"] is not None

    changed = client.patch(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json={"teaching_goal": "学生能够比较 TCP 与 UDP 的连接特点"},
    )
    assert changed.status_code == 200, changed.text
    second_version = changed.json()
    assert second_version["brief_id"] != first_version["brief_id"]
    assert second_version["version"] == 2
    assert second_version["status"] == "draft"
    assert second_version["teaching_goal"] == "学生能够比较 TCP 与 UDP 的连接特点"

    conflict = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "BRIEF_VERSION_CONFLICT"

    reconfirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": 2},
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["version"] == 2
    assert reconfirmed.json()["status"] == "confirmed"

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        versions = (
            db.query(TeachingBrief)
            .filter(TeachingBrief.project_id == project_id)
            .order_by(TeachingBrief.version.asc())
            .all()
        )
        assert [brief.version for brief in versions] == [1, 2]
        assert versions[0].status == "confirmed"
        assert versions[0].content_json["teaching_goal"] == (
            "帮助学生理解 TCP 三次握手并能解释每次报文的作用"
        )
        assert versions[1].status == "confirmed"
    finally:
        db.close()
