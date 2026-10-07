"""回归：对话不能卡在同一个字段上（实测过"不管说什么都在问学段"）。

成因：教师答"大学"，模型只把信息写进 target_audience，grade 字段始终为空，
于是待问清单永远第一项是 grade。
"""

import asyncio

from backend.models.session import Session
from backend.schemas import IntentResult
from backend.services.brief import (
    CORE_FIELD_ORDER,
    FIELD_LABELS,
    FIELD_QUESTIONS,
    MAX_ASKS_PER_FIELD,
    capture_teacher_answer,
    ensure_draft,
    get_latest_brief,
    infer_grade,
    next_question,
    normalize_content,
    pending_core_fields,
    remember_asked_field,
)
from backend.services.orchestrator import get_orchestrator


def _content(**overrides):
    base = normalize_content(None)
    base.update(overrides)
    return base


def test_grade_is_inferred_from_context_when_field_empty():
    """模型把学段写进 target_audience 时，也能推断出 grade。"""
    content = _content(target_audience="大学本科生，具备基础物理", teaching_goal="理解伯努利方程")
    assert infer_grade(content) == "大学"

    assert infer_grade(_content(target_audience="高二学生")) == "高中"
    assert infer_grade(_content(target_audience="初中三年级")) == "初中"
    assert infer_grade(_content(target_audience="小学五年级")) == "小学"
    # 没有线索时不能瞎猜
    assert infer_grade(_content(target_audience="对物理感兴趣的学生")) == ""


def test_teacher_answer_is_captured_when_model_missed_the_field():
    """教师已经回答过的字段，后端要能补上（模型没写进该字段时）。"""
    content = _content()
    refs = {}
    assert capture_teacher_answer(content, refs, "grade", "大学") is True
    assert content["grade"] == "大学"
    assert refs["grade"] == "teacher"

    # 已经填过就不要覆盖
    assert capture_teacher_answer(content, refs, "grade", "高中") is False
    assert content["grade"] == "大学"

    # 答非所问（形状不符）不写入
    assert capture_teacher_answer(_content(), {}, "grade", "这节课重点是公式推导") is False


def test_question_advances_after_repeated_asks():
    """同一字段问够上限就跳过：宁可少问一项，也不能把对话卡死。"""
    content = _content()
    refs = {}
    assert next_question(content, refs)[0] == "grade"

    content["ask_counts"] = {"grade": MAX_ASKS_PER_FIELD}
    # 学段被跳过，推进到下一项
    assert next_question(content, refs)[0] == "subject"

    # 全部问过上限后不再追问
    filled_counts = {field: MAX_ASKS_PER_FIELD for field in (
        "grade", "subject", "target_audience", "duration_minutes", "teaching_goal",
        "knowledge_points", "teaching_focus", "teaching_difficulties",
        "output_types", "logic_flow",
    )}
    assert next_question(_content(ask_counts=filled_counts), refs) is None


def test_remember_asked_field_counts_each_ask():
    class _Brief:
        def __init__(self):
            self.content_json = normalize_content(None)
            self.source_refs = {}

    class _DB:
        def flush(self):
            pass

    brief = _Brief()
    remember_asked_field(_DB(), brief, "grade")
    assert brief.content_json["asking_field"] == "grade"
    assert brief.content_json["ask_counts"]["grade"] == 1
    remember_asked_field(_DB(), brief, "grade")
    assert brief.content_json["ask_counts"]["grade"] == 2


def test_pending_is_empty_once_grade_inferred():
    """推断出学段后，待问清单要真的少一项（不是只在显示层糊过去）。"""
    content = _content(
        target_audience="大学本科生",
        teaching_goal="理解伯努利方程",
        subject="流体力学",
        knowledge_points=[{"order": 1, "title": "静压强"}],
        teaching_focus="伯努利方程",
        teaching_difficulties="流速与压强关系",
        output_types=["pptx"],
        logic_flow=["导入", "新授"],
    )
    content["grade"] = infer_grade(content)
    refs = {"grade": "inferred"}
    pending = pending_core_fields(content, refs)
    assert "grade" not in pending


def test_skipped_field_is_no_longer_pending():
    """教师主动跳过的字段按默认处理，不再算待确认。

    否则跳过之后进度永远清不掉、确认按钮也点不动 —— 那是另一种死结。
    """
    content = _content(subject="流体力学")
    refs = {"grade": "default"}
    assert "grade" not in pending_core_fields(content, refs)
    # 跳过课时也一样：按 45 分钟处理，但不再追问
    assert "duration_minutes" not in pending_core_fields(content, {"duration_minutes": "default"})


# ── 以下走真实编排器：下一问永远由需求单状态决定，模型的话只是措辞 ────────────────


def _register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "对话教师"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _make_session(client, email: str) -> tuple[str, str]:
    headers = _register(client, email)
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "对话推进", "scenario": "流体"}
    ).json()["project_id"]
    session_id = client.post(
        "/api/v1/sessions", headers=headers, json={"subject": "流体力学", "project_id": project_id}
    ).json()["session_id"]
    return project_id, session_id


def _fake_analyzer(*, follow_up: str, options: list[str], **fields):
    """固定模型的返回：措辞与候选答案采用它的，但"问哪一项"由后端状态决定。"""

    def analyze_stream(_self, _session_id, _messages, _context_block=""):
        yield "text", "好的，我看看。"
        yield "result", (
            IntentResult(follow_up_question=follow_up, options=list(options), **fields),
            "好的，我看看。",
        )

    return analyze_stream


def _run(session_id: str, message: str, db, session, **kwargs) -> list:
    async def runner():
        return [
            event
            async for event in get_orchestrator().chat(
                session_id, message, db=db, session=session, **kwargs
            )
        ]

    return asyncio.run(runner())


def _kinds(events: list) -> list:
    return [getattr(event.event_type, "value", event.event_type) for event in events]


def _last(events: list, kind: str):
    matched = [
        event for event in events if getattr(event.event_type, "value", event.event_type) == kind
    ]
    assert matched, f"没有 {kind} 事件：{_kinds(events)}"
    return matched[-1]


def _complete_draft(db, session, project_id: str, session_id: str):
    """造一份"核心字段已齐"的需求单（教师在第一条消息里就全说清了）。"""
    brief = ensure_draft(
        db, user_id=session.user_id, project_id=project_id, session_id=session_id
    )
    content = normalize_content(brief.content_json)
    content.update(
        {
            "grade": "高二",
            "subject": "物理",
            "target_audience": "高二学生",
            "duration_minutes": 90,
            "teaching_goal": "理解开普勒三定律并能做天体质量与周期的基本计算",
            "knowledge_points": [{"order": 1, "title": "开普勒三定律"}],
            "teaching_focus": "三定律的建立与理解",
            "teaching_difficulties": "天体质量与周期的计算",
            "output_types": ["pptx", "docx"],
            "logic_flow": ["复习导入", "新授", "练习", "小结"],
        }
    )
    brief.content_json = content
    # 全部记 teacher：这是"教师在第一条消息里就全说清了"的现场，不存在待确认项。
    # 设计类字段（目标/重难点/产出…）只有 teacher 来源才算确认过，记成 ai 的话
    # 这份"已齐"的草稿仍会被继续追问（见 TEACHER_CONFIRM_FIELDS）。
    brief.source_refs = {field: "teacher" for field in CORE_FIELD_ORDER}
    db.flush()
    return brief


def test_refinement_question_is_asked_once_before_confirming(
    client, db_session_factory, monkeypatch
):
    """核心信息齐、但模型还有一处取舍要问时：先让它问一次，而不是直接甩确认面板。

    实测的界面矛盾：正文写"想先确认一个最影响设计的问题……"，下面却已经是"确认教学
    信息"——那句话教师再也没机会回答，而且这两块自相矛盾。
    """
    project_id, session_id = _make_session(client, "refine@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(follow_up="这两课时您更希望我把重心放在哪一侧？", options=[]),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        _complete_draft(db, session, project_id, session_id)
        db.commit()
        first = _run(session_id, "第一课时讲定律建立，第二课时讲天体计算。", db, session)
        db.commit()
    finally:
        db.close()

    question = _last(first, "question")
    assert question.data["refine"] is True
    assert question.data["options"] == []
    assert "重心" in question.content
    assert not [event for event in first if _kinds([event])[0] == "confirm"]

    # 同样的追问再来一次：只放行一次，必须落到确认面板（否则永远到不了确认那一步）
    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        second = _run(session_id, "第一课时多给点时间。", db, session)
        db.commit()
    finally:
        db.close()

    assert not [event for event in second if _kinds([event])[0] == "question"]
    assert _kinds(second)[-1] == "confirm"


def test_capped_state_confirms_instead_of_reusing_the_model_question(
    client, db_session_factory, monkeypatch
):
    """所有待问项都问满上限时，要收敛到确认总结，而不是退回模型的问题。

    老实现在这一刻会把 target=None 的追问退回成"模型自己的问题与选项"；模型看到
    字段仍为空，于是又把学段问一遍 —— 正是"不管说什么都在问学段"。
    """
    project_id, session_id = _make_session(client, "capped@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(
            follow_up="这节课是给哪个学段的学生上？",
            options=["小学", "初中", "高中", "大学"],
        ),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        brief = ensure_draft(
            db, user_id=session.user_id, project_id=project_id, session_id=session_id
        )
        content = normalize_content(brief.content_json)
        # 每一项都问过上限、却仍是空的：状态机必须承认"问不动了"，不再追问
        content["ask_counts"] = {field: MAX_ASKS_PER_FIELD for field in CORE_FIELD_ORDER}
        brief.content_json = content
        db.commit()

        events = _run(session_id, "我还是定不下来", db, session)
    finally:
        db.close()

    kinds = _kinds(events)
    assert "question" not in kinds, f"问不动了还在追问：{kinds}"
    assert kinds[-1] == "confirm"
    # 模型那句话一次都不该出现在事件里
    assert all("哪个学段" not in event.content for event in events)


def test_question_event_carries_progress_for_the_ui(client, db_session_factory, monkeypatch):
    """追问事件要带上"第几项 / 还剩几项"，界面才能把推进显示出来。

    问哪一项由需求单状态决定；措辞与候选答案用模型本轮的 —— 只有它看得到
    具体课题，选项才能贴题（后端写死的四选一只是兜底）。
    """
    project_id, session_id = _make_session(client, "progress@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(
            follow_up="这些学生处在哪个学段？",
            options=["小学高年级", "初中", "高中", "大学本科"],
        ),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        ensure_draft(db, user_id=session.user_id, project_id=project_id, session_id=session_id)
        db.commit()
        events = _run(session_id, "给大学本科生讲流体力学", db, session)
    finally:
        db.close()

    question = _last(events, "question")
    # 进度与字段由状态决定（模型即使想聊别的字段也改不了推进顺序）
    assert question.data["field"] == "grade"
    assert question.data["field_label"] == FIELD_LABELS["grade"]
    assert question.data["core_total"] == len(CORE_FIELD_ORDER)
    assert "grade" in question.data["pending_fields"]
    # 措辞与候选答案用模型本轮的
    assert question.content == "这些学生处在哪个学段？"
    assert question.data["options"] == ["小学高年级", "初中", "高中", "大学本科"]


def test_question_falls_back_to_backend_wording_without_model_options(
    client, db_session_factory, monkeypatch
):
    """模型没给候选答案时用后端的固定问法兜底：对话不能因为没有按钮就停住。"""
    project_id, session_id = _make_session(client, "fallback@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(follow_up="模型自己的问法", options=[]),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        ensure_draft(db, user_id=session.user_id, project_id=project_id, session_id=session_id)
        db.commit()
        events = _run(session_id, "我想设计一节流体力学课。", db, session)
    finally:
        db.close()

    question = _last(events, "question")
    assert question.data["field"] == "grade"
    assert question.content == FIELD_QUESTIONS["grade"][0]
    assert question.data["options"] == FIELD_QUESTIONS["grade"][1]


def test_design_fields_filled_by_ai_still_ask_for_teacher_confirmation(
    client, db_session_factory, monkeypatch
):
    """模型自己填的目标/重难点只是提议：必须请教师确认过，不能直接甩确认面板。

    修的就是"问几轮就出方案"：模型顺手把设计字段填满，老实现当场跳确认面板，
    而面板里的目标、重难点教师一句都没过目。
    """
    project_id, session_id = _make_session(client, "proposal@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(
            follow_up="这节课最想让学生达成什么？",
            options=["能说出开普勒三定律", "能用三定律计算天体质量"],
        ),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        brief = _complete_draft(db, session, project_id, session_id)
        # 设计字段全部来自模型的提议（source=ai）——正是要修的那个现场
        brief.source_refs = {field: "ai" for field in CORE_FIELD_ORDER}
        db.commit()
        first = _run(session_id, "先按你的理解整理一版。", db, session)
        db.commit()
    finally:
        db.close()

    kinds = _kinds(first)
    assert "confirm" not in kinds, f"设计字段未经教师确认就出确认面板：{kinds}"
    question = _last(first, "question")
    # 问的是设计字段里排最前的教学目标，而不是模型想聊的别的
    assert question.data["field"] == "teaching_goal"
    # 模型提议的值摆成第一个按钮：点一下即确认，不必重新打字
    assert question.data["options"][0] == "理解开普勒三定律并能做天体质量与周期的基本计算"
    assert question.data["options"][1:] == ["能说出开普勒三定律", "能用三定律计算天体质量"]
    assert "teaching_goal" in question.data["pending_fields"]

    db = db_session_factory()
    try:
        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        assert (brief.source_refs or {}).get("teaching_goal") == "ai"
    finally:
        db.close()

    # 教师给出了自己的回答（与提议值不同）→ 该项记 teacher，推进到下一项
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(
            follow_up="这节课要覆盖哪些知识点？",
            options=["三定律的建立过程", "用定律计算天体质量"],
            teaching_goal="能说出开普勒三定律",
        ),
    )
    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        second = _run(session_id, "能说出开普勒三定律", db, session)
        db.commit()
    finally:
        db.close()

    assert _last(second, "question").data["field"] == "knowledge_points"
    db = db_session_factory()
    try:
        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        assert (brief.source_refs or {}).get("teaching_goal") == "teacher"
    finally:
        db.close()


def test_opening_turn_invites_instead_of_asking_grade(client, db_session_factory, monkeypatch):
    """新会话的开场是邀请老师描述，不是一上来甩"哪个学段"的四选一。"""
    project_id, session_id = _make_session(client, "opening@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(follow_up="哪个学段？", options=["小学", "初中"]),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        ensure_draft(db, user_id=session.user_id, project_id=project_id, session_id=session_id)
        db.commit()
        events = _run(session_id, "我想设计一节“流体力学”课程。", db, session, opening=True)
    finally:
        db.close()

    question = _last(events, "question")
    assert question.data["invite"] is True
    assert question.data["options"] == []
    assert question.data["field"] is None
    assert question.data["core_total"] == len(CORE_FIELD_ORDER)

    # 开场不算"问过学段"：老师随后一句话里带上学段，就不该再被问一次
    db = db_session_factory()
    try:
        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        assert not (brief.content_json.get("ask_counts") or {}).get("grade")
    finally:
        db.close()


def test_clicking_an_option_confirms_even_without_model_extraction(
    client, db_session_factory, monkeypatch
):
    """教师点了按钮（消息就是按钮文字）就算确认：模型这轮没抽出值也不该再问一遍。

    "点一下即确认"是提议按钮的承诺；如果只认"值变了没有"，模型抽风时教师点了
    按钮还会被问第二遍 —— 承诺落空，对话又变磨叽。
    """
    project_id, session_id = _make_session(client, "click@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(
            follow_up="这节课最想让学生达成什么？",
            options=["能说出开普勒三定律"],
        ),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        brief = _complete_draft(db, session, project_id, session_id)
        brief.source_refs = {field: "ai" for field in CORE_FIELD_ORDER}
        db.commit()
        first = _run(session_id, "先按你的理解整理一版。", db, session)
        db.commit()
    finally:
        db.close()

    question = _last(first, "question")
    assert question.data["field"] == "teaching_goal"
    clicked = question.data["options"][1]  # 模型给的按钮（第一个是提议值）

    # 教师点了按钮；这一轮模型什么字段都没抽出来（值不变）
    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        second = _run(session_id, clicked, db, session)
        db.commit()
    finally:
        db.close()

    assert _last(second, "question").data["field"] == "knowledge_points"
    db = db_session_factory()
    try:
        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        assert (brief.source_refs or {}).get("teaching_goal") == "teacher"
    finally:
        db.close()


def test_skip_field_marks_default_and_never_writes_the_text(
    client, db_session_factory, monkeypatch
):
    """跳过某一项：按默认处理、进度往前走，而那句"跳过"绝不能变成字段的值。"""
    project_id, session_id = _make_session(client, "skip@example.com")
    monkeypatch.setattr(
        "backend.services.intent.IntentAnalyzer.analyze_stream",
        _fake_analyzer(follow_up="这节课是给哪个学段的学生上？", options=["小学", "初中"]),
    )

    db = db_session_factory()
    try:
        session = db.query(Session).filter(Session.session_id == session_id).one()
        brief = ensure_draft(
            db, user_id=session.user_id, project_id=project_id, session_id=session_id
        )
        remember_asked_field(db, brief, "grade")
        db.commit()

        events = _run(
            session_id, "「学段」先跳过，按默认处理。", db, session, skip_field="grade"
        )
        # 真实链路里由 SSE 流的每帧提交；直接调编排器时要自己落盘
        db.commit()
    finally:
        db.close()

    db = db_session_factory()
    try:
        brief = get_latest_brief(db, project_id=project_id, session_id=session_id)
        content = brief.content_json or {}
        refs = brief.source_refs or {}
        # 跳过那句话没有被当成答案写进去
        assert not content.get("grade")
        assert refs.get("grade") == "default"
        assert "grade" not in pending_core_fields(content, refs)
    finally:
        db.close()

    # 对话继续推进到下一项，而不是再问一遍学段
    assert _last(events, "question").data["field"] == "subject"
