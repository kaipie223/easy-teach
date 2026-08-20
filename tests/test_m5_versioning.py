"""M5 immutable versions, constrained patches and export records."""

import pytest


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "M5 Teacher"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


async def empty_rag(*args, **kwargs):
    return []


def create_project_with_plan(client, headers):
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "M5 版本测试", "scenario": "版本与局部修改"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]

    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "TCP", "project_id": project_id},
    )
    assert session.status_code == 201, session.text

    brief = client.patch(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json={
            "teaching_goal": "理解 TCP 三次握手",
            "target_audience": "大一新生",
            "duration_minutes": 45,
            "knowledge_points": [
                {
                    "order": 1,
                    "title": "连接建立过程",
                    "key_points": ["SYN", "SYN-ACK", "ACK"],
                    "examples": ["客户端与服务器建立连接"],
                    "estimated_minutes": 20,
                },
                {
                    "order": 2,
                    "title": "报文确认机制",
                    "key_points": ["序列号", "确认号"],
                    "estimated_minutes": 15,
                },
            ],
            "logic_flow": ["问题导入", "过程讲解", "例题练习", "总结"],
            "teaching_focus": "报文时序",
            "teaching_difficulties": "SYN 与 ACK 的区别",
            "output_types": ["pptx", "docx", "html"],
        },
    )
    assert brief.status_code == 200, brief.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": brief.json()["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    plan = client.post(f"/api/v1/projects/{project_id}/plan", headers=headers, json={})
    assert plan.status_code == 201, plan.text
    return project_id, session.json()["session_id"], plan.json()["plan_id"]


def test_patch_creates_immutable_version_and_restore_preserves_history(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.rag_search", empty_rag)
    monkeypatch.setattr("backend.services.orchestrator.Orchestrator.run_generation", lambda self, task_id: None)
    headers = register(client, "m5-version@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)

    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert generation.status_code == 202, generation.text
    initial_version_id = generation.json()["artifact_version_id"]
    assert initial_version_id

    versions = client.get(f"/api/v1/projects/{project_id}/versions", headers=headers)
    assert versions.status_code == 200
    initial = versions.json()[0]
    assert initial["artifact_version_id"] == initial_version_id
    assert initial["version"] == 1

    preview = client.post(
        f"/api/v1/projects/{project_id}/revisions/interpret",
        headers=headers,
        json={"instruction": "简化第 3 页", "base_version_id": initial_version_id},
    )
    assert preview.status_code == 201, preview.text
    patch = preview.json()
    assert patch["status"] == "preview"
    assert patch["target_ids"] == [initial["snapshot"]["slides"][2]["slide_id"]]

    applied = client.post(
        f"/api/v1/projects/{project_id}/revisions/apply",
        headers=headers,
        json={"patch_id": patch["patch_id"]},
    )
    assert applied.status_code == 201, applied.text
    changed = applied.json()
    assert changed["version"] == 2
    assert changed["base_version_id"] == initial_version_id
    assert len(changed["snapshot"]["slides"][2]["bullets"]) < len(
        initial["snapshot"]["slides"][2]["bullets"]
    )
    assert changed["snapshot"]["slides"][0] == initial["snapshot"]["slides"][0]

    replay = client.post(
        f"/api/v1/projects/{project_id}/revisions/apply",
        headers=headers,
        json={"patch_id": patch["patch_id"]},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "REVISION_PATCH_NOT_PENDING"

    restored = client.post(
        f"/api/v1/projects/{project_id}/versions/{initial_version_id}/restore",
        headers=headers,
        json={"summary": "恢复到初始成果"},
    )
    assert restored.status_code == 201, restored.text
    restored_version = restored.json()
    assert restored_version["version"] == 3
    assert restored_version["snapshot"] == initial["snapshot"]

    history = client.get(f"/api/v1/projects/{project_id}/versions", headers=headers).json()
    assert [item["version"] for item in history] == [3, 2, 1]


def test_exports_are_bound_to_version_and_idempotent(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.rag_search", empty_rag)
    monkeypatch.setattr("backend.services.orchestrator.Orchestrator.run_generation", lambda self, task_id: None)
    monkeypatch.setattr("backend.routers.revisions.run_export", lambda export_id: None)
    headers = register(client, "m5-export@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    version_id = generation.json()["artifact_version_id"]

    created = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=headers,
        json={"artifact_version_id": version_id, "formats": ["html"]},
    )
    assert created.status_code == 202, created.text
    export = created.json()["exports"][0]
    assert export["artifact_version_id"] == version_id
    assert export["format"] == "html"
    assert export["status"] == "pending"

    repeated = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=headers,
        json={"artifact_version_id": version_id, "formats": ["html"]},
    )
    assert repeated.status_code == 202
    assert repeated.json()["exports"][0]["export_id"] == export["export_id"]

    not_ready = client.get(
        f"/api/v1/exports/{export['export_id']}/download",
        headers=headers,
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "EXPORT_NOT_READY"


def test_revision_rejects_arbitrary_fields(client, monkeypatch):
    from backend.schemas import RevisionOperation
    from backend.services.versions import apply_operations

    snapshot = {
        "title": "课程",
        "target_audience": "教师",
        "duration_minutes": 10,
        "teaching_goal": "目标",
        "slides": [{"slide_id": "slide_001", "order": 1, "title": "页", "purpose": "导入"}],
        "lesson_sections": [],
        "interactions": [],
    }
    with pytest.raises(Exception) as error:
        apply_operations(
            snapshot,
            [RevisionOperation(op="replace", target_id="slide_001", field="snapshot_json", value={})],
        )
    assert getattr(error.value, "code", None) == "REVISION_FIELD_NOT_ALLOWED"
