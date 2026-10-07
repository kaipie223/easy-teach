"""追问的候选答案：模型没给也要有按钮。

实测三次真实调用，模型一次都没返回 options —— 只靠提示词必然出现"没有按钮"的
情况。所以这里是三级兜底：模型给的 → 正文里的 ①②③ → 按缺失字段的默认候选。
"""

from types import SimpleNamespace

from backend.schemas import IntentResult
from backend.services.brief import brief_to_intent, intent_to_content
from backend.services.intent import (
    IntentAnalyzer,
    probing_policy,
    resolve_options,
    strip_option_markers,
)


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "选项教师"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_persisted_brief_carries_options_all_the_way(client, db_session_factory):
    """编排器用的是持久化后的结果：这一路丢了 options，界面就永远没有按钮。"""
    from backend.models.session import Session
    from backend.services.brief import persist_intent_result

    headers = register(client, "options-merge@example.com")
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "选项合并", "scenario": "追问选项"}
    ).json()["project_id"]
    session_id = client.post(
        "/api/v1/sessions", headers=headers, json={"subject": "物理", "project_id": project_id}
    ).json()["session_id"]

    db = db_session_factory()
    session = db.query(Session).filter(Session.session_id == session_id).one()

    _, first = persist_intent_result(
        db,
        session,
        IntentResult(
            teaching_goal="理解浮力",
            missing_info=["课时"],
            options=["45 分钟", "2 课时（90 分钟）"],
            is_complete=False,
        ),
    )
    assert first.options == ["45 分钟", "2 课时（90 分钟）"]

    # 下一轮没有候选答案时，旧选项必须被清掉，否则按钮会答非所问
    _, second = persist_intent_result(
        db,
        session,
        IntentResult(teaching_goal="理解浮力", missing_info=["教学侧重点"], options=[], is_complete=False),
    )
    assert second.options == []
    db.close()


def test_options_survive_the_brief_round_trip():
    """追问事件用的是持久化后的结果，选项必须穿过需求单，否则按钮永远不出现。"""
    result = IntentResult(
        teaching_goal="理解浮力",
        missing_info=["课时"],
        options=["45 分钟", "2 课时（90 分钟）"],
        is_complete=False,
    )
    content = intent_to_content(result)
    assert content["options"] == ["45 分钟", "2 课时（90 分钟）"]
    rebuilt = brief_to_intent(SimpleNamespace(content_json=content, source_refs={}))
    assert rebuilt.options == ["45 分钟", "2 课时（90 分钟）"]


def parse(data: dict):
    return IntentAnalyzer._parse_intent(data)


def test_model_options_win():
    data = {
        "is_complete": False,
        "missing_info": ["课时安排"],
        "reply": "我们先定个课时？① 45 分钟 ② 90 分钟",
        "options": ["45 分钟", "2 课时（90 分钟）", "由内容定"],
    }
    assert parse(data).options == ["45 分钟", "2 课时（90 分钟）", "由内容定"]


def test_options_are_extracted_from_reply_text_when_model_omits_them():
    data = {
        "is_complete": False,
        "missing_info": ["教学侧重点"],
        "reply": "这节课你更想突出哪条线？① 文言字词与翻译 ② 写景抒情脉络 ③ 忧乐观主旨",
    }
    # 序号标记要剥掉，否则按钮上会出现"① …"
    assert parse(data).options == ["文言字词与翻译", "写景抒情脉络", "忧乐观主旨"]


def test_letter_options_are_extracted_from_reply_text():
    """模型也爱用 A/B/C/D 编号写候选，三种写法都要能抽出来。"""
    data = {
        "is_complete": False,
        "missing_info": ["教学侧重点"],
        "reply": "这节课你更想突出哪条线？\nA. 文言字词与翻译\nB、写景抒情脉络\nC) 忧乐观主旨",
    }
    assert parse(data).options == ["文言字词与翻译", "写景抒情脉络", "忧乐观主旨"]


def test_option_prefixes_are_stripped_from_buttons():
    """模型给的 options 自带 ① / A. 编号时，按钮上不该再显示一遍。"""
    data = {
        "is_complete": False,
        "missing_info": ["课时"],
        "options": ["① 45 分钟", "A. 90 分钟"],
    }
    assert parse(data).options == ["45 分钟", "90 分钟"]


def test_options_fall_back_to_defaults_by_missing_field():
    assert parse({"is_complete": False, "missing_info": ["duration_minutes"]}).options == [
        "45 分钟",
        "2 课时（90 分钟）",
        "由你按内容定",
    ]
    grade_options = parse({"is_complete": False, "missing_info": ["grade"]}).options
    assert "高中" in grade_options


def test_no_options_once_the_brief_is_complete():
    data = {"is_complete": True, "missing_info": [], "options": ["不该出现"]}
    assert parse(data).options == []


def test_unknown_missing_field_yields_no_options_rather_than_wrong_ones():
    assert resolve_options({"reply": ""}, ["某个无法归类的字段"], complete=False) == []


def test_strip_option_markers_removes_the_duplicate_list():
    reply = "这次课你更想突出哪条线？① 文言字词与翻译 ② 写景抒情脉络 ③ 忧乐观主旨"
    cleaned = strip_option_markers(reply, ["文言字词与翻译", "写景抒情脉络", "忧乐观主旨"])
    assert cleaned == "这次课你更想突出哪条线？"
    # 改空了就保留原文，宁可重复也不要丢消息
    assert strip_option_markers("① 甲 ② 乙", ["甲", "乙"]) == "① 甲 ② 乙"


def test_explicit_direct_request_stops_probing():
    policy = probing_policy([{"role": "user", "content": "别问了，直接生成"}])
    assert "不要再追问" in policy
