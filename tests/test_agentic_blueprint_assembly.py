"""流水线（agentic）拼装契约：模型少给字段不能让整册蓝图生成失败。

真实事故：骨架步骤的提示词要求"只给 interaction_type、prompt、items、answer_groups"
（不含 title），填充步骤只要求"写全四项"（不含 title / duration_minutes），而
InteractionSpec.title 与 LessonPlanSectionSpec.title 在 schema 里是必填无默认值 ——
于是每一次流水线生成都会在整册校验时抛 ValidationError，教师只看到一句"生成失败"。
"""

import asyncio
import json
from types import SimpleNamespace

from backend.services import courseware_ai
from backend.services.ai_stream import aiter_threaded_producer
from backend.services.courseware_ai import (
    _validation_summary,
    generate_courseware_spec_agentic,
)

# 需求单：流水线只负责 slides/教案/互动/成果，其余字段由 _normalize_and_validate 补齐
BRIEF = {
    "course_name": "显性遗传",
    "subject": "生物",
    "grade": "高二",
    "duration_minutes": 45,
    "target_audience": "高二学生",
    "teaching_goal": "学生能判断性状的显隐性并规范书写遗传图解",
    "knowledge_points": [{"order": 1, "title": "显隐性判断", "estimated_minutes": 45}],
    "logic_flow": ["复习导入", "新授", "练习", "小结"],
    "teaching_focus": "遗传图解的规范书写",
    "teaching_difficulties": "配子与子代基因型书写混乱",
    "output_types": ["pptx", "docx"],
    "homework_type": "由子代反推亲本的变式题",
    "style_preference": "清晰、克制",
}

# 骨架：刻意不给 interaction 的 title（提示词就是这么要求的）
SKELETON = {
    "analysis": {"page_plan": "6 页：封面→复习→新授→示范→练习→小结"},
    "skeleton": {
        "title": "显性遗传",
        "slides": [
            {
                "order": index,
                "layout": layout,
                "title": f"第 {index} 页",
                "purpose": f"这一页的具体导语 {index}",
                "bullets": [f"草案 {index}-1", f"草案 {index}-2", f"草案 {index}-3"],
            }
            for index, layout in enumerate(
                ["cover", "bullets", "bullets", "bullets", "bullets", "summary"], start=1
            )
        ],
        "lesson_sections": [
            {"order": 1, "title": "复习导入", "duration_minutes": 8, "objective": "回忆性状概念"},
            {"order": 2, "title": "新授：显隐性判断", "duration_minutes": 15, "objective": "学会判断依据"},
            {"order": 3, "title": "练习与小结", "duration_minutes": 22, "objective": "规范书写并小结"},
        ],
        "interactions": [
            {
                # 模型实测会自造题型，且 answer_groups 写成与 items 平行的数组
                "interaction_type": "找错图解",
                "title": "找出图解中的错误",
                "prompt": "下面两份图解各有一处错误，请指出来。",
                "items": ["图解 1：Aa × Aa，只写了亲本和子代", "图解 2：Aa × aa，漏写配子比例"],
                "answer_groups": ["图解 1 错误：漏写配子类型", "图解 2 错误：漏写配子比例"],
            }
        ],
    },
}

# 填充教案：刻意只写"四项"，不带 title / duration_minutes（提示词就是这么要求的）
TEACHING = {
    "plan_notes": "先复习再示范，最后用变式题检验。",
    "lesson_sections": [
        {
            "order": 1,
            "objective": "学生能说出性状与基因型的关系",
            # 实测模型会把"2-4 条"写成一段话（schema 要 list[str]）
            "teacher_actions": "0-8 分钟：提问前概念，请两名学生板演。8-12 分钟：点评书写。",
            "student_actions": "独立写出两个基因型。同桌互查。",
            "assessment": "能写出 Aa 即为达标",
        },
        {
            "order": 2,
            "objective": "学生能依据子代表现型判断显隐性",
            "teacher_actions": "8-23 分钟：示范判断链条",
            "student_actions": "完成 2 道判断练习",
            "assessment": "判断依据完整",
        },
        {
            "order": 3,
            "objective": "学生能规范书写遗传图解",
            "teacher_actions": "23-45 分钟：巡视点拨",
            "student_actions": "完成变式题并互评",
            "assessment": "配子与子代书写完整",
        },
    ],
    # 实测 docx 的列表字段会被写成一段文字，html 还会自造键名
    "output_specs": {
        "docx": {
            "teacher_preparation": "教具：多媒体投影、彩色粉笔。学具：练习本、互评量表。",
            "differentiation": "学有余力：尝试 Aa × aa 的图解。需要帮扶：给出步骤模板。",
        },
        "html": {"interactive_id": "genetics_practice", "completion_feedback": "完成练习后查看解析。"},
    },
}

SLIDES = {
    "draft_notes": "要点已压缩，细节由教师口述。",
    "slides": [
        {
            # 实测模型用 slide_id 指代页码（格式还每批不同：'slide_1' / 9），且不带 order ——
            # 只认 order 会让整批填充结果落空（要点退回骨架草案、讲稿全空）
            "slide_id": f"slide_{index}" if index % 2 else index,
            "bullets": [f"扩写 {index}-1", f"扩写 {index}-2", f"扩写 {index}-3"],
            "speaker_notes": f"第 {index} 页讲稿，含关键提问与预设回答。" * 4,
        }
        for index in range(1, 7)
    ],
}


# 测试用的开关：模拟"某些页没写讲稿""互动题没法用"这两种真实失败
REPAIR_REQUESTS: list[dict] = []
OMIT_NOTES: set[int] = set()
EMPTY_INTERACTION = False


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, reasoning_content=""),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


def _fake_completion(_client, messages, **_kwargs):
    # 吞掉 thinking 之类的可选参数：假补全只关心"哪一段、要什么"，把签名写成
    # **kwargs 才不会因为调用方多传一个开关就整组测试挂掉。
    system = str(messages[0].get("content") or "")
    user_text = str(messages[1].get("content") or "")
    # 局部修复请求：只补缺讲稿的那几页。把请求记下来供断言"没有重发整册"。
    if "还缺 speaker_notes" in user_text:
        request = json.loads(user_text)
        REPAIR_REQUESTS.append(request)
        return _response(
            json.dumps(
                {
                    "slides": [
                        {
                            "order": slide["order"],
                            "speaker_notes": f"补写的讲稿：第 {slide['order']} 页的讲解思路与关键提问。" * 5,
                        }
                        for slide in request["slides"]
                    ]
                },
                ensure_ascii=False,
            )
        )
    if "未通过结构校验" in user_text and "offending" in user_text:
        request = json.loads(user_text)
        REPAIR_REQUESTS.append(request)
        return _response(
            json.dumps(
                {
                    "interactions": [
                        {
                            "interaction_type": "classification",
                            "title": "显隐性归类",
                            "prompt": "把下列性状分为显性与隐性两组。",
                            "items": ["双眼皮", "单眼皮"],
                            "answer_groups": {"显性": ["双眼皮"], "隐性": ["单眼皮"]},
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
    if "只搭骨架" in system:
        skeleton = json.loads(json.dumps(SKELETON))
        if EMPTY_INTERACTION:
            skeleton["skeleton"]["interactions"][0]["items"] = []
        return _response(json.dumps(skeleton, ensure_ascii=False))
    if "第二步" in system:
        return _response(json.dumps(TEACHING, ensure_ascii=False))
    if "第三步" in system:
        slides = json.loads(json.dumps(SLIDES))
        for position, item in enumerate(slides["slides"], start=1):
            if position in OMIT_NOTES:
                item.pop("speaker_notes", None)
        return _response(json.dumps(slides, ensure_ascii=False))
    # 审校：返回空对象即可，这一步按设计允许退回已校验候选
    return _response("{}")


def test_pipeline_survives_missing_required_titles(monkeypatch):
    """骨架/填充少给 title 时，整册拼装仍必须产出合法蓝图。"""
    REPAIR_REQUESTS.clear()
    monkeypatch.setattr(courseware_ai, "_completion", _fake_completion)

    result = generate_courseware_spec_agentic(BRIEF, [], client=object())
    spec = result.spec

    assert len(spec.slides) == 6
    assert spec.slides[0].slide_id == "slide_001"
    # 填充批次按 slide_id 也能配对：要点是扩写后的、讲稿不为空
    assert spec.slides[0].bullets[0] == "扩写 1-1"
    assert all(slide.speaker_notes.strip() for slide in spec.slides)
    # 教案环节的 title 与时长来自骨架（模型只补四项）
    assert [item.title for item in spec.lesson_sections] == ["复习导入", "新授：显隐性判断", "练习与小结"]
    assert sum(item.duration_minutes for item in spec.lesson_sections) == BRIEF["duration_minutes"]
    # 整段文字被切成逐条动作
    first = spec.lesson_sections[0]
    assert len(first.teacher_actions) == 2, first.teacher_actions
    assert len(first.student_actions) == 2, first.student_actions
    # 自造题型按答案形状归一，且答案来自模型自己写的文字
    interaction = spec.interactions[0]
    assert interaction.interaction_type == "matching"
    assert sorted(interaction.answer_groups) == ["图解 1 错误：漏写配子类型", "图解 2 错误：漏写配子比例"]
    assert interaction.title
    # 成果设定里的列表字段被归一成列表
    assert isinstance(spec.output_specs.docx.teacher_preparation, list)
    assert len(spec.output_specs.docx.teacher_preparation) >= 1
    assert isinstance(spec.output_specs.docx.differentiation, list)
    # 自造键名被映射到规范字段
    assert spec.output_specs.html.interaction_ids == ["interaction_001"]
    assert spec.output_specs.html.completion_message == "完成练习后查看解析。"
    assert spec.output_specs.pptx.max_bullets_per_slide >= 2
    # 形状合法时不该动用修复步骤
    assert REPAIR_REQUESTS == []


def test_repair_only_requests_the_pages_missing_notes(monkeypatch):
    """缺讲稿时只补那几页，绝不重发整册。

    实测重发整册在 13 页时会被输出上限截断（JSON 断在半路），修复反而成为新的失败点。
    """
    REPAIR_REQUESTS.clear()
    OMIT_NOTES.update({3, 5})
    monkeypatch.setattr(courseware_ai, "_completion", _fake_completion)
    try:
        result = generate_courseware_spec_agentic(BRIEF, [], client=object())
    finally:
        OMIT_NOTES.clear()

    assert all(slide.speaker_notes.strip() for slide in result.spec.slides)
    assert len(REPAIR_REQUESTS) == 1, REPAIR_REQUESTS
    request = REPAIR_REQUESTS[0]
    assert [slide["order"] for slide in request["slides"]] == [3, 5]
    assert "invalid_output" not in request
    assert "offending" not in request
    # 其余内容没被改动
    assert len(result.spec.slides) == 6
    assert result.spec.slides[0].bullets[0] == "扩写 1-1"


def test_repair_sends_only_the_offending_block(monkeypatch):
    """其它校验失败只发出错的那一块，补丁并回后必须能通过校验。"""
    global EMPTY_INTERACTION
    REPAIR_REQUESTS.clear()
    EMPTY_INTERACTION = True
    monkeypatch.setattr(courseware_ai, "_completion", _fake_completion)
    try:
        result = generate_courseware_spec_agentic(BRIEF, [], client=object())
    finally:
        EMPTY_INTERACTION = False

    assert len(REPAIR_REQUESTS) == 1, REPAIR_REQUESTS
    request = REPAIR_REQUESTS[0]
    assert "offending" in request
    assert list(request["offending"]) == ["interactions"], request["offending"]
    assert "invalid_output" not in request
    # 补丁生效：互动题恢复成可用的一道
    assert result.spec.interactions[0].items == ["双眼皮", "单眼皮"]
    assert result.spec.interactions[0].answer_groups == {"显性": ["双眼皮"], "隐性": ["单眼皮"]}


def test_validation_summary_names_the_broken_field():
    """日志里必须能看出是哪个字段错了 —— 不然只能靠猜。"""
    from pydantic import ValidationError

    from backend.schemas import InteractionSpec

    try:
        InteractionSpec.model_validate({"interaction_id": "interaction_001", "prompt": "选一选"})
    except ValidationError as error:
        summary = _validation_summary(error)
    else:  # pragma: no cover - 数据非法时必然抛错
        raise AssertionError("InteractionSpec 缺少 title 时应当校验失败")

    assert "title" in summary
    assert "Field required" in summary or "required" in summary


def test_threaded_producer_raises_instead_of_forwarding_the_exception():
    """生产者异常必须抛出，不能被当成数据帧透传（否则错误会被编码层顶掉）。"""

    def boom(_emit):
        raise RuntimeError("blueprint failed")

    async def consume() -> list:
        return [frame async for frame in aiter_threaded_producer(boom)]

    try:
        asyncio.run(consume())
    except RuntimeError as error:
        assert "blueprint failed" in str(error)
    else:  # pragma: no cover - 异常必须冒出来
        raise AssertionError("生产者异常应当抛给消费端")
