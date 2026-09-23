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


def empty_rag(*args, **kwargs):
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


def test_plan_endpoint_streams_progress_when_the_client_asks_for_events(client, monkeypatch):
    """`Accept: text/event-stream` turns the same build into an observable stream."""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr("backend.routers.revisions.run_export", lambda export_id: None)
    headers = register(client, "m5-plan-stream@example.com")
    project_id, _, _ = create_project_with_plan(client, headers)

    streamed = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers={**headers, "Accept": "text/event-stream"},
        json={"generation_mode": "template", "force_rebuild": True},
    )

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in streamed.text
    assert '"label"' in streamed.text
    assert "event: result" in streamed.text
    assert '"plan_id"' in streamed.text

    # The streamed build persists exactly like the JSON path does.
    latest = client.get(f"/api/v1/projects/{project_id}/plan", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["version"] == 2


def test_project_events_endpoint_returns_the_progress_snapshot(client):
    """Clients that are not streaming still get the project snapshot as JSON."""
    headers = register(client, "m5-project-events@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "进度快照", "scenario": "项目级 SSE"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]

    snapshot = client.get(f"/api/v1/projects/{project_id}/events", headers=headers)

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["project_id"] == project_id
    assert body["active"] is False
    assert "revision" in body
    assert body["tasks"] == []
    assert body["exports"] == []
    assert body["materials"] == []


def test_task_events_endpoint_returns_json_without_stream_negotiation(client, monkeypatch):
    """Clients that are not streaming still get the plain snapshot."""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    headers = register(client, "m5-task-events@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert created.status_code == 202, created.text
    task_id = created.json()["task_id"]

    plain = client.get(f"/api/v1/tasks/{task_id}/events", headers=headers)

    assert plain.status_code == 200
    assert plain.json()["task_id"] == task_id
    assert plain.json()["status"] in {"pending", "processing"}


def test_patch_creates_immutable_version_and_restore_preserves_history(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr("backend.services.orchestrator.Orchestrator.run_generation", lambda self, task_id, **kwargs: None)
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
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr("backend.services.orchestrator.Orchestrator.run_generation", lambda self, task_id, **kwargs: None)
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

    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
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


def test_slide_picture_operations_are_allowed_and_clearable():
    """配图走确定性 patch：可以绑定，也可以用 null 清除；非法位置模式会被拒绝。"""
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

    bound = apply_operations(
        snapshot,
        [
            RevisionOperation(
                op="replace",
                target_id="slide_001",
                field="image",
                value={"material_id": "mat_x", "placement": "full", "caption": "示意图"},
            )
        ],
    )
    assert bound.slides[0].image is not None
    assert bound.slides[0].image.material_id == "mat_x"
    assert bound.slides[0].image.placement == "full"
    assert bound.slides[0].image.caption == "示意图"

    cleared = apply_operations(
        bound,
        [RevisionOperation(op="replace", target_id="slide_001", field="image", value=None)],
    )
    assert cleared.slides[0].image is None

    with pytest.raises(Exception) as error:
        apply_operations(
            snapshot,
            [
                RevisionOperation(
                    op="replace",
                    target_id="slide_001",
                    field="image",
                    value={"material_id": "mat_x", "placement": "diagonal"},
                )
            ],
        )
    assert getattr(error.value, "code", None) == "REVISION_RESULT_INVALID"


def test_slide_picture_binding_is_validated_and_versioned(
    client, db_session_factory, monkeypatch, tmp_path
):
    """配图绑定：归属校验发生在写入时，且绑定与移除都创建新的不可变版本。"""
    from PIL import Image as PILImage

    from backend.models.material import Material

    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    signup = client.post(
        "/api/v1/auth/register",
        json={
            "email": "m5-slide-picture@example.com",
            "password": "password123",
            "display_name": "M5 Picture",
        },
    )
    assert signup.status_code == 201, signup.text
    account = signup.json()
    headers = {"Authorization": f"Bearer {account['access_token']}"}
    project_id, _, plan_id = create_project_with_plan(client, headers)
    other_project_id = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "另一个项目", "scenario": "通用课程"},
    ).json()["project_id"]

    picture = tmp_path / "figure.png"
    PILImage.new("RGB", (800, 600), (10, 80, 200)).save(picture)
    document = tmp_path / "notes.pdf"
    document.write_bytes(b"%PDF-1.4\n")

    db = db_session_factory()
    try:
        def material(material_id, project, name, file_type, path):
            return Material(
                material_id=material_id,
                owner_id=account["user"]["user_id"],
                project_id=project,
                original_name=name,
                file_type=file_type,
                stored_path=str(path),
                size_bytes=1,
                checksum_sha256=material_id,
            )

        db.add_all(
            [
                material("mat_ok", project_id, "figure.png", "image", picture),
                material("mat_other_project", other_project_id, "figure.png", "image", picture),
                material("mat_pdf", project_id, "notes.pdf", "pdf", document),
                material("mat_missing", project_id, "gone.png", "image", tmp_path / "gone.png"),
            ]
        )
        db.commit()
    finally:
        db.close()

    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    version_id = generation.json()["artifact_version_id"]
    snapshot = client.get(
        f"/api/v1/projects/{project_id}/versions/{version_id}", headers=headers
    ).json()["snapshot"]
    slide_id = snapshot["slides"][2]["slide_id"]

    def put(body, base=version_id):
        return client.put(
            f"/api/v1/projects/{project_id}/versions/{base}/slides/{slide_id}/image",
            headers=headers,
            json=body,
        )

    # 他人/别项目的图片、非图片资料、文件已丢失，都在写入时被拒绝
    assert put({"material_id": "mat_other_project"}).json()["error"]["code"] == (
        "SLIDE_IMAGE_OTHER_PROJECT"
    )
    assert put({"material_id": "mat_pdf"}).json()["error"]["code"] == "SLIDE_IMAGE_NOT_AN_IMAGE"
    assert put({"material_id": "mat_missing"}).json()["error"]["code"] == (
        "SLIDE_IMAGE_FILE_MISSING"
    )
    assert put({"material_id": "mat_absent"}).json()["error"]["code"] == "MATERIAL_NOT_FOUND"

    bound = put({"material_id": "mat_ok", "placement": "background", "caption": " 时序图 "})
    assert bound.status_code == 201, bound.text
    bound_version = bound.json()
    assert bound_version["version"] == 2
    assert bound_version["base_version_id"] == version_id
    assert bound_version["generation_mode"] == "manual"
    assert bound_version["snapshot"]["slides"][2]["image"] == {
        "material_id": "mat_ok",
        "placement": "background",
        "caption": "时序图",
    }

    # 绑定是不可变的：旧版本仍然没有配图
    original = client.get(
        f"/api/v1/projects/{project_id}/versions/{version_id}", headers=headers
    ).json()
    assert original["snapshot"]["slides"][2].get("image") is None

    # 基于已被取代的版本再次修改必须冲突
    stale = put({"material_id": "mat_ok"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # 移除配图同样创建新版本
    removed = put({"material_id": None}, base=bound_version["artifact_version_id"])
    assert removed.status_code == 201, removed.text
    assert removed.json()["version"] == 3
    assert removed.json()["snapshot"]["slides"][2].get("image") is None


def test_generation_feedback_creates_an_applicable_revision(client, monkeypatch):
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
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
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    calls = []

    def fake_regenerate(snapshot, *, target_type, target_id, instruction, **kwargs):
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


def test_ai_target_regeneration_streams_progress_when_requested(client, monkeypatch):
    """The same regeneration becomes observable when the client negotiates SSE."""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr("backend.routers.revisions.run_export", lambda export_id: None)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )

    def fake_regenerate(snapshot, *, target_type, target_id, instruction, on_stage=None, **kwargs):
        if on_stage is not None:
            on_stage("generate")
        data = snapshot.model_dump(mode="json")
        target = next(item for item in data["slides"] if item["slide_id"] == target_id)
        target["title"] = "AI 重写后的连接建立"
        spec = type(snapshot).model_validate(data)
        return RevisionAIResult(
            spec=spec,
            model_name="deepseek-chat",
            prompt_version="artifact-target-v2",
            usage={"prompt_tokens": 80, "completion_tokens": 120, "total_tokens": 200},
            quality_report=inspect_courseware(spec),
        )

    monkeypatch.setattr("backend.routers.revisions.regenerate_target", fake_regenerate)
    headers = register(client, "m5-revision-stream@example.com")
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

    streamed = client.post(
        f"/api/v1/projects/{project_id}/revisions/regenerate",
        headers={**headers, "Accept": "text/event-stream"},
        json={
            "base_version_id": initial_id,
            "target_type": "slide",
            "target_id": target_id,
            "instruction": "改成时序图驱动的讲解",
        },
    )

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in streamed.text
    assert "AI 正在重生成目标内容" in streamed.text
    assert "event: result" in streamed.text
    assert '"version": 2' in streamed.text


def test_element_anchor_rewrites_only_the_selected_bullet(client, monkeypatch):
    """页内元素级锚点：只重写被点中的那一条要点，同页其它内容与其它页原样保留。"""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    seen = []

    def fake_regenerate(snapshot, *, target_type, target_id, instruction, field=None, index=None, **kwargs):
        seen.append((field, index))
        data = snapshot.model_dump(mode="json")
        target = next(item for item in data["slides"] if item["slide_id"] == target_id)
        target["bullets"][index] = "改写后的一条要点"
        spec = type(snapshot).model_validate(data)
        return RevisionAIResult(
            spec=spec,
            model_name="deepseek-chat",
            prompt_version="artifact-element-v1",
            usage={"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
            quality_report=inspect_courseware(spec),
        )

    monkeypatch.setattr("backend.routers.revisions.regenerate_target", fake_regenerate)
    headers = register(client, "m5-element-anchor@example.com")
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

    slide = next(item for item in initial["snapshot"]["slides"] if len(item["bullets"]) >= 2)
    slide_id = slide["slide_id"]

    def post_regenerate(**overrides):
        return client.post(
            f"/api/v1/projects/{project_id}/revisions/regenerate",
            headers=headers,
            json={
                "base_version_id": initial_id,
                "target_type": "slide",
                "target_id": slide_id,
                "instruction": "这条要点太长了",
                **overrides,
            },
        )

    # 锚点必须在消耗模型配额之前就被校验掉
    not_editable = post_regenerate(field="order")
    assert not_editable.status_code == 422
    assert not_editable.json()["error"]["code"] == "REVISION_FIELD_NOT_EDITABLE"

    out_of_range = post_regenerate(field="bullets", index=99)
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["code"] == "REVISION_FIELD_INDEX_INVALID"

    not_a_list = post_regenerate(field="title", index=0)
    assert not_a_list.status_code == 422
    assert not_a_list.json()["error"]["code"] == "REVISION_FIELD_INDEX_UNSUPPORTED"

    assert not seen

    regenerated = post_regenerate(field="bullets", index=1)
    assert regenerated.status_code == 201, regenerated.text
    changed = regenerated.json()
    assert seen == [("bullets", 1)]
    assert changed["version"] == 2
    assert changed["base_version_id"] == initial_id
    assert changed["prompt_version"] == "artifact-element-v1"

    def slide_from(payload):
        return next(item for item in payload["snapshot"]["slides"] if item["slide_id"] == slide_id)

    # 被点中的那一条被换掉，同页其它字段完全没动
    assert slide_from(changed) == {
        **slide,
        "bullets": [slide["bullets"][0], "改写后的一条要点", *slide["bullets"][2:]],
    }
    # 其它页面也必须保持原样
    assert [item for item in changed["snapshot"]["slides"] if item["slide_id"] != slide_id] == [
        item for item in initial["snapshot"]["slides"] if item["slide_id"] != slide_id
    ]


def test_element_prompt_rewrites_a_single_bullet(client, monkeypatch):
    """元素级锚点走自己的 {"value": ...} 契约，只替换被点中的那一条，不碰同页其它字段。"""
    import json as jsonlib
    from types import SimpleNamespace

    from backend.schemas import CoursewarePlanSpec
    from backend.services.revision_ai import regenerate_target

    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    headers = register(client, "m5-element-prompt@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)
    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    version_id = generation.json()["artifact_version_id"]
    payload = client.get(
        f"/api/v1/projects/{project_id}/versions/{version_id}", headers=headers
    ).json()
    spec = CoursewarePlanSpec.model_validate(payload["snapshot"])
    slide = next(item for item in spec.slides if len(item.bullets) >= 2)

    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"value": "改写后的一条要点"}')
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
            )

    stub_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = regenerate_target(
        spec,
        target_type="slide",
        target_id=slide.slide_id,
        instruction="这条要点太长了",
        client=stub_client,
        field="bullets",
        index=1,
    )

    assert result.prompt_version == "artifact-element-v1"
    assert result.quality_report["status"] in {"passed", "warning"}
    updated = next(item for item in result.spec.slides if item.slide_id == slide.slide_id)
    assert updated.bullets == [slide.bullets[0], "改写后的一条要点", *slide.bullets[2:]]
    assert updated.title == slide.title
    assert updated.speaker_notes == slide.speaker_notes

    # 只调一次模型，用的是元素级提示词和元素级上下文（而不是整目标 schema）
    assert len(calls) == 1
    assert calls[0]["messages"][0]["content"].startswith(
        "你是一名资深教学设计师。请只重写用户指定的那一处内容"
    )
    request_body = jsonlib.loads(calls[0]["messages"][1]["content"])
    assert request_body["edit"] == {
        "field": "bullets",
        "index": 1,
        "current_value": slide.bullets[1],
        "requirement": "只重写 current_value 这一处，value 的类型必须与 current_value 完全一致。",
    }
    assert "target_schema" not in request_body


def test_project_generation_is_idempotent_per_blueprint(client, monkeypatch):
    """同一份蓝图重复点生成复用同一个任务；蓝图重建后必须能重新生成。

    幂等键原先由客户端按 "latest" 拼出，换蓝图之后仍会命中旧任务，于是"重新
    生成"看起来毫无反应。现在由服务端按本次基准版本派生，这里锁住该行为。
    """
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(
        "backend.services.orchestrator.Orchestrator.run_generation",
        lambda self, task_id, **kwargs: None,
    )
    headers = register(client, "m5-generate-idempotency@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)

    def generate(**body):
        return client.post(
            f"/api/v1/projects/{project_id}/generate", headers=headers, json=body
        )

    first = generate(plan_id=plan_id)
    assert first.status_code == 202, first.text
    # 重复点击同一份蓝图：复用同一个任务，不堆重复任务
    again = generate(plan_id=plan_id)
    assert again.status_code == 202, again.text
    assert again.json()["task_id"] == first.json()["task_id"]

    # 蓝图重建会得到新的成果版本，即使客户端不传 plan_id 也必须能重新生成
    rebuilt = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers=headers,
        json={"generation_mode": "template", "force_rebuild": True},
    )
    assert rebuilt.status_code == 201, rebuilt.text
    after = generate()
    assert after.status_code == 202, after.text
    assert after.json()["task_id"] != first.json()["task_id"]
    assert after.json()["artifact_version_id"] != first.json()["artifact_version_id"]


def test_project_files_endpoint_returns_only_generated_files_of_one_version(
    client, db_session_factory
):
    """产物接口回答"这个版本生成过哪些文件"：教师上传的资料不算产物，且按版本隔离。"""
    from backend.models.file import FileRecord
    from backend.models.project import Project

    headers = register(client, "m5-version-files@example.com")
    project_id, _, _ = create_project_with_plan(client, headers)
    versions = client.get(f"/api/v1/projects/{project_id}/versions", headers=headers).json()
    assert len(versions) == 1
    version_id = versions[0]["artifact_version_id"]

    db = db_session_factory()
    try:
        owner_id = db.query(Project).filter(Project.project_id == project_id).one().owner_id
        db.add_all(
            [
                FileRecord(
                    file_id="f_generated_pptx",
                    user_id=owner_id,
                    project_id=project_id,
                    artifact_version_id=version_id,
                    session_id="s_any",
                    original_name="课件.pptx",
                    file_type="pptx",
                    stored_path="C:/tmp/deck.pptx",
                    size_kb=66.0,
                    ref_description="generated",
                ),
                # 教师上传的资料：同一项目，但绝不是生成产物
                FileRecord(
                    file_id="f_uploaded_pdf",
                    user_id=owner_id,
                    project_id=project_id,
                    artifact_version_id=None,
                    session_id="s_any",
                    original_name="参考资料.pdf",
                    file_type="pdf",
                    stored_path="C:/tmp/ref.pdf",
                    size_kb=12.0,
                    ref_description="教师上传",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    listed = client.get(f"/api/v1/projects/{project_id}/files", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["file_id"] for item in body] == ["f_generated_pptx"]
    # 返回的是产物形状（file_name / download_url），而不是上传资料的 FileInfo
    assert body[0]["file_name"] == "课件.pptx"
    assert body[0]["file_type"] == "pptx"
    assert body[0]["download_url"] == "/api/v1/download/f_generated_pptx"

    # 按版本过滤：别的版本没有产物
    scoped = client.get(
        f"/api/v1/projects/{project_id}/files",
        headers=headers,
        params={"artifact_version_id": "av_missing"},
    )
    assert scoped.status_code == 200
    assert scoped.json() == []

    # 别人的项目查不到产物
    stranger = register(client, "m5-version-files-other@example.com")
    denied = client.get(f"/api/v1/projects/{project_id}/files", headers=stranger)
    assert denied.status_code in {403, 404}
