"""Every long-running pipeline reports the same thing: what it is doing, and how far along."""

from backend.config import settings
from backend.schemas import ExportFormat, FileType
from backend.services.progress import (
    EXPORT_STAGES,
    GENERATION_STAGES,
    PLAN_STAGES,
    REVISION_STAGES,
    export_stage_label,
    material_stages,
)

from tests.test_m5_versioning import create_project_with_plan, empty_rag, register


def test_stage_percentages_never_go_backwards():
    """百分比由阶段派生，所以每一张阶段表都必须是单调的。"""
    tables = (
        GENERATION_STAGES,
        PLAN_STAGES,
        REVISION_STAGES,
        EXPORT_STAGES,
        material_stages("document"),
        material_stages("image"),
        material_stages("video"),
    )
    for stages in tables:
        percents = [stage.percent for stage in stages]
        assert percents == sorted(percents), [stage.key for stage in stages]
        assert 0 < percents[0]
        assert percents[-1] <= 100


def test_export_stage_label_names_the_format():
    """渲染文案必须带上格式名，否则四条导出记录会显示同一句话。"""
    assert export_stage_label("pptx", "render") == "正在渲染演示文稿"
    assert export_stage_label("pdf", "render") == "正在渲染打印版"
    # 枚举成员也要能查到：str 枚举的哈希基于成员名，直接拿成员当字典键会漏
    assert export_stage_label(ExportFormat.PPTX, "render") == "正在渲染演示文稿"


def test_material_stages_depend_on_the_file_type():
    """图片多一步视觉识别，视频多一步转录解析；文档没有模型步骤。"""
    assert [stage.key for stage in material_stages("document")] == ["extract", "index"]
    assert [stage.key for stage in material_stages("image")] == ["extract", "vision", "index"]
    assert [stage.key for stage in material_stages("video")] == ["extract", "parse", "index"]
    # 枚举成员同样要能命中
    assert [stage.key for stage in material_stages(FileType.IMAGE)] == [
        "extract",
        "vision",
        "index",
    ]


def test_generation_task_reports_its_stages(client, monkeypatch, db_session_factory):
    """生成任务逐个阶段推进，并把步骤清单随任务一起下发。"""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(settings, "task_queue_eager", True)
    # 后台任务自己开 SessionLocal，conftest 只覆盖了 get_db，不指向测试库的话
    # 这次生成会写到真实数据库上。
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    headers = register(client, "m5-generation-stages@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)

    generation = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert generation.status_code == 202, generation.text
    task_id = generation.json()["task_id"]

    status = client.get(f"/api/v1/tasks/{task_id}/status", headers=headers).json()
    assert status["status"] == "completed", status
    assert status["progress"] == 100
    # 最后一步是"登记生成产物"
    assert status["stage"] == "persist"
    # 文案与清单都来自后端，前端不必维护自己的阶段文案表
    assert status["stage_label"]
    keys = [step["key"] for step in status["stages"]]
    assert keys[:3] == ["brief", "search", "parse"]
    assert keys[-1] == "persist"
    assert all(step["label"] for step in status["stages"])


def test_export_records_report_their_progress(client, monkeypatch, db_session_factory):
    """导出记录同样带阶段与百分比；已完成的记录就是 100%。"""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    monkeypatch.setattr(settings, "task_queue_eager", True)
    monkeypatch.setattr("backend.db.database.SessionLocal", db_session_factory)
    headers = register(client, "m5-export-progress@example.com")
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
        json={"artifact_version_id": version_id, "formats": ["pptx", "pdf"]},
    )
    assert created.status_code < 300, created.text
    records = created.json()["exports"]
    assert [record["format"] for record in records] == ["pptx", "pdf"]
    for record in records:
        assert record["status"] == "completed", record
        assert record["stage"] == "save"
        assert record["stage_percent"] == 100
        assert record["started_at"]


def test_plan_stream_carries_percent_and_stages(client, monkeypatch):
    """蓝图 SSE 不再只发一句文案，还要发百分比与步骤清单。"""
    monkeypatch.setattr("backend.routers.courseware.search_sync", empty_rag)
    headers = register(client, "m5-plan-progress@example.com")
    project_id, _, _ = create_project_with_plan(client, headers)

    streamed = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers={**headers, "Accept": "text/event-stream"},
        json={"generation_mode": "template", "force_rebuild": True},
    )
    assert streamed.status_code == 200
    assert "event: progress" in streamed.text
    assert '"stage_label"' in streamed.text
    assert '"percent"' in streamed.text
    assert '"stages"' in streamed.text
