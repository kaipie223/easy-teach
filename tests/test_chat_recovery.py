"""模型抽风时对话不能卡住：答案要保住、下一问由状态决定。

实测 deepseek-v4-flash 会偶尔完全无视 JSON 模式、返回一句纯文本；老实现会把整轮
（包括教师刚点的答案）丢掉，需求单没变化，于是同一个问题被反复问，而且每轮还要白等
两次重试。这里把新的兜底行为钉死。
"""

import asyncio
import json

from backend.models.session import Session
from backend.services.brief import (
    apply_teacher_answer,
    brief_context_block,
    next_question,
    normalize_content,
)
from backend.services.intent import IntentServiceError
from backend.services.orchestrator import get_orchestrator


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "兜底教师"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_next_question_follows_the_field_order():
    field, question, options = next_question({}, {})
    assert field == "grade"
    assert "学段" in question
    assert len(options) >= 2

    # 学段与学科都确认后，下一项就轮到授课对象
    filled = {"grade": "高二", "subject": "生物"}
    refs = {"grade": "teacher", "subject": "teacher"}
    assert next_question(filled, refs)[0] == "target_audience"

    # 全齐了就不再追问
    complete = {
        "grade": "高二",
        "subject": "生物",
        "target_audience": "高二学生",
        "teaching_goal": "能判断显隐性并书写遗传图解",
        "knowledge_points": [{"order": 1, "title": "显隐性判断", "estimated_minutes": 20}],
        "teaching_focus": "遗传图解规范书写",
        "teaching_difficulties": "配子与子代基因型书写",
        "output_types": ["pptx", "docx"],
        "logic_flow": ["复习导入", "新授", "练习", "小结"],
        "duration_minutes": 45,
    }
    complete_refs = dict.fromkeys(complete, "teacher")
    assert next_question(complete, complete_refs) is None


def test_context_block_spells_out_what_to_ask():
    block = brief_context_block(None)
    assert "这节课是给哪个学段的学生上？" in block
    assert "options" in block
    assert "grade" in block


def test_teacher_answer_is_validated_against_the_field():
    content = normalize_content(None)
    refs: dict = {}
    # 形状不符的答案宁可不写：兜底是按"上一轮问的字段"落笔的，写错会污染需求单
    apply_teacher_answer(content, refs, "grade", "遗传图解中配子与子代基因型书写混乱")
    assert not content.get("grade")
    apply_teacher_answer(content, refs, "grade", "高二")
    assert content["grade"] == "高二"
    assert refs["grade"] == "teacher"
    apply_teacher_answer(content, refs, "subject", "生物")
    assert content["subject"] == "生物"
    apply_teacher_answer(content, refs, "duration_minutes", "大概 90 分钟吧")
    assert content["duration_minutes"] == 90


def make_session(client, db_session_factory, email: str) -> tuple[str, str]:
    headers = register(client, email)
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "抽风兜底", "scenario": "遗传"}
    ).json()["project_id"]
    session_id = client.post(
        "/api/v1/sessions", headers=headers, json={"subject": "生物", "project_id": project_id}
    ).json()["session_id"]
    return project_id, session_id


def test_invalid_model_response_does_not_lose_the_turn(client, db_session_factory, monkeypatch):
    project_id, session_id = make_session(client, db_session_factory, "recover@example.com")

    def broken_analyze_stream(_self, _session_id, _messages, _context_block=""):
        raise IntentServiceError(
            "AI 返回的数据格式无效，本次内容没有保存", code="AI_INVALID_RESPONSE"
        )
        yield  # pragma: no cover - 生成器签名需要

    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream", broken_analyze_stream
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        session.intent_data = None
        # 造出"上一轮问的是学段"的现场：先有草稿，再记下本轮问的字段
        from backend.services.brief import ensure_draft, remember_asked_field

        brief = ensure_draft(
            db,
            user_id=session.user_id,
            project_id=project_id,
            session_id=session_id,
        )
        remember_asked_field(db, brief, "grade")
        db.commit()

        async def run():
            return [
                event
                async for event in get_orchestrator().chat(
                    session_id, "高二", db=db, session=session
                )
            ]

        events = asyncio.run(run())
    finally:
        db.close()

    kinds = [getattr(event.event_type, "value", event.event_type) for event in events]
    # 关键：这一轮没有变成 error，教师看到的是下一个问题
    assert "error" not in kinds
    assert kinds[-1] == "question"

    db = db_session_factory()
    try:
        from backend.services.brief import get_latest_brief

        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        content = brief.content_json or {}
        # 教师的回答被保住了（写进上一轮问的字段），而不是随那一轮一起丢掉
        assert content.get("grade") == "高二"
        # 既然学段已经拿到，下一问不该再问学段
        assert content.get("asking_field") != "grade"
    finally:
        db.close()
