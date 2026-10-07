"""Every long-running pipeline reports the same thing: what it is doing, and how far along."""

from backend.config import settings
from backend.schemas import ExportFormat, FileType
from backend.services.progress import (
    EXPORT_STAGES,
    GENERATION_STAGES,
    PLAN_BRANCHES,
    PLAN_STAGES,
    REVISION_BRANCHES,
    REVISION_STAGES,
    export_stage_label,
    frame,
    label_of,
    material_stages,
    percent_of,
)

from tests.test_m5_versioning import create_project_with_plan, empty_rag, register


def test_sub_stages_fold_onto_a_step_that_exists_in_the_stepper():
    """子阶段必须给出它所属的里程碑，否则步骤条一个都对不上、全部变灰。

    实测症状：生成/重生成期间前端"只有进度条，看不出现在在哪一步"—— stage 上报的是
    skeleton / fill_teaching / fill_slides，而步骤条里只有 brief / evidence / generate /
    review / persist，前端 indexOf 拿 -1，5 个步骤全被判定为"未开始"。
    """
    manifest_keys = [stage.key for stage in PLAN_STAGES]
    for key in (
        "skeleton",
        "fill_teaching",
        "fill_slides",
        "repair",
        "review_repair",
        "reused",
        "review",
        "persist",
    ):
        payload = frame(PLAN_STAGES, key, branches=PLAN_BRANCHES)
        assert payload["step"] in manifest_keys, (key, payload["step"])

    # 三个子阶段都归到"生成教学蓝图"这一步
    for key in ("skeleton", "fill_teaching", "fill_slides"):
        assert frame(PLAN_STAGES, key, branches=PLAN_BRANCHES)["step"] == "generate"
    # 回头修复归到审校
    assert frame(PLAN_STAGES, "review_repair", branches=PLAN_BRANCHES)["step"] == "review"
    # 里程碑本身原样返回
    assert frame(PLAN_STAGES, "persist", branches=PLAN_BRANCHES)["step"] == "persist"
    # 局部重生成那条链路有同样的问题：repair 不在 REVISION_STAGES 里
    assert frame(REVISION_STAGES, "repair", branches=REVISION_BRANCHES)["step"] == "generate"


def test_every_reported_blueprint_stage_has_wording_and_a_nonzero_percent():
    """蓝图流水线上报的每个阶段都必须在进度表里。

    实测事故：agentic 流水线上报 skeleton / fill_teaching / fill_slides，而它们不在
    PLAN_BRANCHES 里 —— percent_of 对未知 key 返回 0、label_of 返回 None，于是每上报
    一次，进度条就被**打回 0**、文案停在上一句，教师看到的就是"一直在思考、进度条
    不动"。缺一项就要在这里失败，而不是等跑了几分钟才被人发现。
    """
    reported = (
        "skeleton",
        "fill_teaching",
        "fill_slides",
        "review",
        "persist",
        "repair",
        "review_repair",
        "template",
        "reused",
    )
    for stage in reported:
        assert label_of(PLAN_STAGES, stage, PLAN_BRANCHES), f"{stage} 没有文案"
        assert percent_of(PLAN_STAGES, stage, PLAN_BRANCHES) > 0, (
            f"{stage} 的百分比是 0：进度条会被打回起点"
        )

    # 正常路径必须一路走高（repair / review_repair 是回头修复，不参与）
    happy = [
        percent_of(PLAN_STAGES, stage, PLAN_BRANCHES)
        for stage in ("skeleton", "fill_teaching", "fill_slides", "review", "persist")
    ]
    assert happy == sorted(happy), happy


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
