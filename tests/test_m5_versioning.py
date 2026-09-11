"""M5 immutable versions, constrained patches and export records."""

import hashlib
from pathlib import Path

import pytest

from backend.services.quality import inspect_courseware
from backend.services.revision_ai import RevisionAIResult


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
    plan = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers=headers,
        json={"generation_mode": "template"},
    )
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


def test_four_completed_exports_can_be_downloaded(
    client,
    db_session_factory,
    monkeypatch,
    tmp_path,
):
    from backend.config import settings
    from backend.services.exports import run_export

    monkeypatch.setattr("backend.routers.courseware.rag_search", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id: None,
    )
    monkeypatch.setattr("backend.routers.revisions.enqueue_export", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    monkeypatch.setattr(settings, "output_dir", Path(tmp_path))

    headers = register(client, "m5-download@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert generation.status_code == 202, generation.text
    version_id = generation.json()["artifact_version_id"]

    created = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=headers,
        json={
            "artifact_version_id": version_id,
            "formats": ["pptx", "docx", "pdf", "html"],
        },
    )
    assert created.status_code == 202, created.text
    records = created.json()["exports"]
    assert {item["format"] for item in records} == {"pptx", "docx", "pdf", "html"}

    expected_media_types = {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "html": "text/html; charset=utf-8",
    }
    expected_signatures = {
        "pptx": b"PK",
        "docx": b"PK",
        "pdf": b"%PDF-",
        "html": b"<!DOCTYPE html>",
    }

    for record in records:
        run_export(record["export_id"], raise_errors=True)
        status = client.get(f"/api/v1/exports/{record['export_id']}", headers=headers)
        assert status.status_code == 200, status.text
        completed = status.json()
        assert completed["status"] == "completed"
        assert completed["artifact_version_id"] == version_id
        assert completed["file_id"].startswith("f_")
        assert len(completed["file_id"]) <= 40
        assert completed["download_url"]

        download = client.get(completed["download_url"], headers=headers)
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == expected_media_types[record["format"]]
        assert download.content.startswith(expected_signatures[record["format"]])
        assert len(download.content) == completed["size_bytes"]
        assert hashlib.sha256(download.content).hexdigest() == completed["checksum_sha256"]
        assert f'.{record["format"]}' in download.headers["content-disposition"]


def test_export_title_is_bounded_by_utf8_bytes():
    from backend.services.exports import _safe_title

    title = _safe_title("超长中文课程名称" * 40)

    assert len(title.encode("utf-8")) <= 120
    assert title


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


def test_generation_feedback_creates_an_applicable_revision(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.rag_search", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id: None,
    )
    headers = register(client, "m5-feedback@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert generation.status_code == 202, generation.text

    feedback = client.post(
        "/api/v1/generate/feedback",
        headers=headers,
        json={"task_id": generation.json()["task_id"], "feedback": "简化第 3 页"},
    )
    assert feedback.status_code == 201, feedback.text
    preview = feedback.json()
    assert preview["status"] == "revision_preview_created"
    assert preview["project_id"] == project_id
    assert preview["patch_id"].startswith("patch_")

    applied = client.post(
        f"/api/v1/projects/{project_id}/revisions/apply",
        headers=headers,
        json={"patch_id": preview["patch_id"]},
    )
    assert applied.status_code == 201, applied.text
    assert applied.json()["version"] == 2


def test_ai_target_regeneration_creates_traced_immutable_version(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.rag_search", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id: None,
    )
    calls = []

    def fake_regenerate(snapshot, *, target_type, target_id, instruction):
        calls.append((target_type, target_id, instruction))
        data = snapshot.model_dump(mode="json")
        target = next(item for item in data["slides"] if item["slide_id"] == target_id)
        target["title"] = "AI 重写后的连接建立"
        target["bullets"] = ["客户端发送 SYN", "服务端回复 SYN-ACK", "客户端确认 ACK"]
        target["speaker_notes"] = "用时序图逐步追问三个报文各自解决的问题。"
        spec = type(snapshot).model_validate(data)
        return RevisionAIResult(
            spec=spec,
            model_name="deepseek-chat",
            prompt_version="artifact-target-v2",
            usage={"prompt_tokens": 80, "completion_tokens": 120, "total_tokens": 200},
            quality_report=inspect_courseware(spec),
        )

    monkeypatch.setattr("backend.routers.revisions.regenerate_target", fake_regenerate)
    headers = register(client, "m5-ai-revision@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    initial_id = generation.json()["artifact_version_id"]
    initial = client.get(
        f"/api/v1/projects/{project_id}/versions/{initial_id}", headers=headers
    ).json()
    target_id = initial["snapshot"]["slides"][2]["slide_id"]

    missing_target = client.post(
        f"/api/v1/projects/{project_id}/revisions/regenerate",
        headers=headers,
        json={
            "base_version_id": initial_id,
            "target_type": "slide",
            "target_id": "slide_missing",
            "instruction": "重写这一页",
        },
    )
    assert missing_target.status_code == 422
    assert not calls

    regenerated = client.post(
        f"/api/v1/projects/{project_id}/revisions/regenerate",
        headers=headers,
        json={
            "base_version_id": initial_id,
            "target_type": "slide",
            "target_id": target_id,
            "instruction": "改成时序图驱动的讲解",
        },
    )
    assert regenerated.status_code == 201, regenerated.text
    changed = regenerated.json()
    assert changed["version"] == 2
    assert changed["base_version_id"] == initial_id
    assert changed["generation_mode"] == "ai"
    assert changed["model_name"] == "deepseek-chat"
    assert changed["prompt_version"] == "artifact-target-v2"
    assert changed["usage"]["total_tokens"] == 200
    assert changed["snapshot"]["slides"][2]["title"] == "AI 重写后的连接建立"
    assert changed["snapshot"]["slides"][0] == initial["snapshot"]["slides"][0]
    assert changed["quality_status"] in {"passed", "warning"}
    assert calls == [("slide", target_id, "改成时序图驱动的讲解")]

    original_again = client.get(
        f"/api/v1/projects/{project_id}/versions/{initial_id}", headers=headers
    ).json()
    assert original_again["snapshot"] == initial["snapshot"]

    stale = client.post(
        f"/api/v1/projects/{project_id}/revisions/regenerate",
        headers=headers,
        json={
            "base_version_id": initial_id,
            "target_type": "slide",
            "target_id": target_id,
            "instruction": "再次重写",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
