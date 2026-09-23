"""M7 — 生成链路端到端：POST /generate → 真实编排器 → 文件挂到版本 → 导出可按版本取。

其它生成测试把 ``run_generation`` 整体 stub 成空操作，只校验了任务创建与幂等。
这里跑**真实的**编排器实现（只把 AI / 文件生成 / RAG 换成无副作用的替身），
验证最容易被"前端能点、后端能返回"骗过的那一段：生成完成后产物文件确实落到了
对应的成果版本上，刷新页面仍然看得到。
"""

import os

import pytest
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models.file import FileRecord
from backend.models.task import Task
from backend.models.versioning import ArtifactVersion
from backend.schemas import IntentResult
from backend.services.orchestrator import get_orchestrator


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "M7 Teacher"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def empty_rag(*args, **kwargs):
    return []


def create_project_with_plan(client, headers):
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "M7 生成链路", "scenario": "生成后产物挂载"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]

    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "HTTP", "project_id": project_id},
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
        "/api/v1/projects/{0}/plan".format(project_id),
        headers=headers,
        json={"generation_mode": "template"},
    )
    assert plan.status_code in (200, 201), plan.text
    plan_id = plan.json()["plan_id"]
    return project_id, None, plan_id


def fake_lock_intent(self, session_id):
    return IntentResult(
        teaching_goal="测试课件",
        knowledge_points=[],
        target_audience="学生",
        duration_minutes=45,
        style_preference="清晰",
        logic_flow="",
        extra_requirements="",
    )


def make_file_stub(ext: str):
    def stub(*args, **kwargs):
        path = settings.output_dir / "gen_{0}.{1}".format(os.urandom(6).hex(), ext)
        path.write_bytes(b"fake generated courseware")
        return str(path)

    return stub


def eager_dispatch(task_name: str, entity_id: str):
    """测试替身：不连 Redis/Celery，直接在进程内跑真实编排器。"""
    get_orchestrator().run_generation(entity_id)

    class _Result:
        id = entity_id

    return _Result()


def test_full_generation_attaches_files_to_version(client, db_session_factory, tmp_path, monkeypatch):
    """真实跑一遍课件生成，产物文件必须绑定到成果版本，任务最终 completed。"""
    engine = db_session_factory().bind
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # run_generation 用的是 SessionLocal（独立会话），必须把它重定向到测试内存库，
    # 否则会写到真实 sqlite 文件里污染开发数据。
    # run_generation 内部局部 import SessionLocal，覆盖原模块即生效。
    monkeypatch.setattr("backend.db.database.SessionLocal", test_session)
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    # 不连 Celery/Redis：入队时直接同步执行真实编排器。
    monkeypatch.setattr("backend.services.task_queue._dispatch", eager_dispatch)
    monkeypatch.setattr("backend.services.orchestrator.search_sync", empty_rag)
    monkeypatch.setattr("backend.services.intent.IntentAnalyzer.lock_intent", fake_lock_intent)
    monkeypatch.setattr("backend.services.orchestrator.resolve_slide_images", lambda *a, **k: {})
    monkeypatch.setattr(
        "backend.services.orchestrator.require_courseware_quality",
        lambda *a, **k: {"status": "passed"},
    )
    monkeypatch.setattr("backend.services.orchestrator.generate_pptx", make_file_stub("pptx"))
    monkeypatch.setattr("backend.services.orchestrator.generate_docx", make_file_stub("docx"))
    monkeypatch.setattr("backend.services.orchestrator.generate_html", make_file_stub("html"))

    headers = register(client, "m7-generation-chain@example.com")
    project_id, _, plan_id = create_project_with_plan(client, headers)

    response = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    task_id = body["task_id"]
    artifact_version_id = body["artifact_version_id"]

    # POST /generate 已通过 _dispatch 替身同步跑完真实编排器。

    db = db_session_factory()
    task = db.query(Task).filter(Task.task_id == task_id).first()
    assert task is not None
    assert task.status == "completed", task.error

    # 核心断言：三个产物文件都挂到了这个成果版本上。
    version_files = (
        db.query(FileRecord)
        .filter(FileRecord.artifact_version_id == artifact_version_id)
        .all()
    )
    assert {f.file_type for f in version_files} == {"pptx", "docx", "html"}

    version = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.artifact_version_id == artifact_version_id)
        .first()
    )
    assert version is not None
    assert version.generation_mode == "initial"
    assert version.quality_status == "passed"

    # 刷新页面后，成果编辑按版本取文件仍然看得到产物。
    files_response = client.get(
        f"/api/v1/projects/{project_id}/files?artifact_version_id={artifact_version_id}",
        headers=headers,
    )
    assert files_response.status_code == 200, files_response.text
    assert len(files_response.json()) == 3
