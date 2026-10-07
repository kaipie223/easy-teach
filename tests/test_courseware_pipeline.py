"""流水线生成（骨架 → 填充教案与成果 → 填充讲稿 → 审校）。

一次调用塞不下整册蓝图，模型只能把每条都写短 —— 教案四项和 PDF 学习要点尤其明显。
这里把分段行为钉住：每段用各自的系统提示词，产出的教案/讲稿来自"填充"段，
模型的思考（analysis / plan_notes / draft_notes / findings）只进日志、不进产物。
"""

import json
from types import SimpleNamespace

from backend.schemas import EvidenceRef
from backend.services.courseware_ai import (
    AGENTIC_PROMPT_VERSION,
    generate_courseware_spec_agentic,
)


class FakeCompletions:
    """按请求内容作答的假客户端。

    刻意不按"第几次调用"返回固定 payload：分批粒度（FILL_SLIDES_BATCH）是产品参数，
    把它当成测试契约的话，每调一次粒度都要重写一遍测试。这里改成看系统提示词判断
    当前是哪一段，并按请求里实际带的页码返回对应讲稿——粒度怎么调都不会失效。
    """

    def __init__(
        self,
        slide_count: int = 6,
        *,
        invalid_skeleton_first: bool = False,
        truncate_teaching_times: int = 0,
        broken_tools: bool = False,
        repair_returns_fix: bool = True,
    ):
        self.slide_count = slide_count
        self.invalid_skeleton_first = invalid_skeleton_first
        self.truncate_teaching_times = truncate_teaching_times
        self.broken_tools = broken_tools
        self.repair_returns_fix = repair_returns_fix
        self.truncated_teaching_calls = 0
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        self.calls += 1
        system = str(kwargs["messages"][0].get("content") or "")
        user = json.loads(kwargs["messages"][1]["content"])

        if "只搭骨架" in system:
            if self.invalid_skeleton_first and self.calls == 1:
                return _response(
                    {"analysis": {"subject_logic": "先概念后定量"}, "skeleton": {"title": "浮力"}}
                )
            return _response(skeleton_payload(self.slide_count))
        if "第二步" in system:
            if isinstance(user, dict) and "offending" in user:
                # 教具修复轮：system 与"填教案"同源，靠请求体里的 offending 区分
                if not self.repair_returns_fix:
                    return _response({"interactive_tools": []})
                return _response({"interactive_tools": [orbit_tool_payload(broken=False)]})
            if self.truncated_teaching_calls < self.truncate_teaching_times:
                self.truncated_teaching_calls += 1
                # 还原真实故障：正文被输出上限截断，JSON 断在半路、解析不出对象
                return _response(
                    '{"lesson_sections": [{"order": 1, "title": "复习导入", "objective": "能说出',
                    finish_reason="length",
                )
            return _response(teaching_payload(broken_tools=self.broken_tools))
        if "第三步" in system:
            # 只回这一批请求的页码：整册重发在真实模型那里会被输出上限截断。
            orders = [slide["order"] for slide in user["slides"]]
            return _response(slides_payload(orders))
        # 审校，以及用整册提示词发起的修复
        return _response(reviewed_payload(self.slide_count))


def _response(payload, *, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=payload if isinstance(payload, str) else json.dumps(payload)
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    )


def fake_client(
    slide_count: int = 6,
    *,
    invalid_skeleton_first: bool = False,
    truncate_teaching_times: int = 0,
    broken_tools: bool = False,
    repair_returns_fix: bool = True,
):
    completions = FakeCompletions(
        slide_count,
        invalid_skeleton_first=invalid_skeleton_first,
        truncate_teaching_times=truncate_teaching_times,
        broken_tools=broken_tools,
        repair_returns_fix=repair_returns_fix,
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions), completions_spy=completions)


def brief_content():
    return {
        "teaching_goal": "理解浮力并解释生活中的浮力现象",
        "subject": "物理",
        "grade": "初二",
        "course_name": "初中物理·浮力",
        "target_audience": "初二学生",
        "duration_minutes": 45,
        "knowledge_points": [
            {"order": 1, "title": "浮力概念", "estimated_minutes": 15},
            {"order": 2, "title": "阿基米德原理", "estimated_minutes": 20},
        ],
        "logic_flow": ["情境导入", "实验探究", "规律应用", "总结"],
        "teaching_focus": "影响浮力大小的因素",
        "teaching_difficulties": "阿基米德原理的理解与应用",
        "output_types": ["pptx", "docx", "html"],
        "style_preference": "实验探究",
    }


def skeleton_payload(slides_count: int = 6):
    layouts = ["cover", "agenda", "steps", "summary", "bullets"]
    slides = [
        {
            "order": index,
            "layout": layouts[min(index - 1, len(layouts) - 1)],
            "title": f"浮力探究 {index}",
            "purpose": f"第 {index} 步：建立浮力认识",
            "bullets": [f"要点草案 {index}-1", f"要点草案 {index}-2"],
        }
        for index in range(1, slides_count + 1)
    ]
    sections = [
        {"order": 1, "title": "情境导入", "duration_minutes": 10, "objective": "学生能提出可探究的问题"},
        {"order": 2, "title": "实验探究", "duration_minutes": 20, "objective": "学生能测出浮力并记录数据"},
        {"order": 3, "title": "规律应用", "duration_minutes": 10, "objective": "学生能用原理解释现象"},
        {"order": 4, "title": "总结", "duration_minutes": 5, "objective": "学生能复述原理与条件"},
    ]
    return {
        "analysis": {
            "subject_logic": "先建立浮力概念，再用阿基米德原理定量",
            "page_plan": "导入用 cover，步骤用 steps，结尾用 summary",
            "risk_check": "必须写明同一液体与排开体积",
        },
        "skeleton": {
            "title": "浮力与阿基米德原理",
            "slides": slides,
            "lesson_sections": sections,
            "interactions": [
                {
                    "title": "浮力变化分类",
                    "interaction_type": "classification",
                    "prompt": "把下列现象按“浮力变大/变小/不变”分类",
                    "items": ["船装货", "石块浸入更深", "换用盐水"],
                    "answer_groups": {"变大": ["换用盐水"], "不变": ["石块浸入更深"]},
                    "explanation": "浮力只与液体密度和排开体积有关。",
                }
            ],
            "narrative_arc": ["情境", "探究", "应用", "总结"],
            "visual_direction": "以实验示意图为主",
        },
    }


def teaching_payload(*, broken_tools: bool = False):
    def section(order, title, minutes, objective):
        return {
            "order": order,
            "title": title,
            "duration_minutes": minutes,
            "objective": objective,
            "teacher_actions": [f"0-{minutes} 分钟：演示并提出关键问题原话"],
            "student_actions": ["记录数据并写出判断依据"],
            "assessment": "判准：能说出自变量与控制变量；典型错误：忽略液体密度",
        }

    payload = {
        "plan_notes": "先探究后应用，结尾回扣目标",
        "lesson_sections": [
            section(1, "情境导入", 10, "学生能提出可探究的问题"),
            section(2, "实验探究", 20, "学生能测出浮力并记录数据"),
            section(3, "规律应用", 10, "学生能用原理解释现象"),
            section(4, "总结", 5, "学生能复述原理与条件"),
        ],
        "output_specs": {
            "pptx": {"max_bullets_per_slide": 5, "narrative_arc": ["情境", "探究", "应用", "总结"]},
            "docx": {"homework": "用原理解释一个生活现象，并写清条件"},
            "pdf": {
                "printable_summary": "核心术语、公式与适用条件、典型易错点",
                "assessment_checklist": ["能说出自变量", "能说出控制变量"],
            },
            "html": {"interaction_ids": [], "allow_retry": True},
        },
        # 互动教具与教案同段产出：学生调排开体积看浮力读数
        "interactive_tools": [
            {
                "tool_id": "tool_001",
                "title": "排开体积与浮力的关系",
                "goal": "学生能通过调节排开体积说明浮力与液体密度、排开体积的关系",
                "engine": "curve",
                "model_note": "F = ρ液 · g · V排",
                "variables": [
                    {"key": "volume", "label": "排开体积", "unit": "L", "min": 0, "max": 2, "default": 0.5},
                ],
                "constants": {"rho": 1000, "g": 9.8},
                "outputs": [
                    {"key": "force", "label": "浮力", "unit": "N", "expression": "rho * g * volume / 1000"},
                ],
                "predict_prompts": ["排开体积翻倍，浮力会怎么变？"],
                "guided_steps": ["先预测", "把体积调到 2L 记下读数", "用 F = ρgV 解释"],
            }
        ],
    }
    if broken_tools:
        # 事故复刻：教具读数引用了未声明的 G、M
        payload["interactive_tools"] = [orbit_tool_payload(broken=True)]
    return payload


def orbit_tool_payload(*, broken: bool):
    """万有引力式的轨道教具；broken=True 是真实事故形态（读数引用未声明的 G、M）。"""
    fixed = not broken
    return {
        "tool_id": "tool_001",
        "title": "卫星轨道参数探究器",
        "goal": "学生能通过改变轨道半径观察线速度与周期的变化",
        "engine": "scene",
        "constants": {"g0": 9.8},
        "variables": [
            {"key": "r", "label": "轨道半径", "unit": "千公里",
             "min": 7000, "max": 42000, "default": 12000},
        ],
        "outputs": [
            {"key": "speed", "label": "线速度", "unit": "km/s",
             "expression": "7.9 * sqrt(6371 / r)" if fixed else "sqrt(G * M / (r * 1000)) / 1000"},
            {"key": "period", "label": "周期", "unit": "min",
             "expression": "2 * pi * sqrt(r**3 / 398600.4418) / 60"
             if fixed else "2 * pi * sqrt((r * 1000)**3 / (G * M)) / 60"},
        ],
        "scene": {
            "entities": [
                {"kind": "circle", "cx": 440, "cy": 170, "r": 16, "fill": "accent"},
                {"kind": "line", "x1": 440, "y1": 170,
                 "x2": "440 + 200 * (r - 7000) / 35000", "y2": 170, "stroke": "steel"},
                {"kind": "circle",
                 "cx": "440 + 200 * (r - 7000) / 35000 * cos(2 * pi * t * 60 / period)",
                 "cy": "170 + 150 * (r - 7000) / 35000 * sin(2 * pi * t * 60 / period)",
                 "r": 8, "fill": "primary"},
                {"kind": "readout", "x": 60, "y": 40, "output": "speed"},
            ],
        },
        "predict_prompts": ["半径变大，线速度往哪变？"],
        "guided_steps": ["先预测", "拖动轨道半径", "用 v=sqrt(GM/r) 解释"],
    }


def slides_payload(orders):
    return {
        "draft_notes": "数据表格由教师口述补充",
        "slides": [
            {
                "order": order,
                "bullets": [f"第 {order} 页完整要点一", f"第 {order} 页完整要点二"],
                "speaker_notes": f"第 {order} 页讲稿：先演示，再追问，预设学生回答并纠正。",
            }
            for order in orders
        ],
    }


def reviewed_payload(count: int = 6):
    def layout_for(order):
        if order == 1:
            return "cover"
        if order == 3:
            return "steps"
        if order == count:
            return "summary"
        return "bullets"

    return {
        "findings": [
            {"page": 3, "issue": "未声明液体相同", "basis": "浮力比较需要控制变量", "action": "已补入题干"}
        ],
        "blueprint": {
            "title": "浮力与阿基米德原理",
            "slides": [
                {
                    "order": index,
                    "layout": layout_for(index),
                    "title": f"浮力探究 {index}",
                    "purpose": f"第 {index} 步：建立浮力认识",
                    "bullets": [f"第 {index} 页完整要点一", f"第 {index} 页完整要点二"],
                    "speaker_notes": f"第 {index} 页讲稿：先演示，再追问，预设学生回答并纠正。",
                }
                for index in range(1, count + 1)
            ],
            "lesson_sections": [
                {
                    "order": order,
                    "title": title,
                    "duration_minutes": minutes,
                    "objective": objective,
                    "teacher_actions": ["演示并提出关键问题原话"],
                    "student_actions": ["记录数据并写出判断依据"],
                    "assessment": "判准：能说出自变量与控制变量",
                }
                for order, title, minutes, objective in (
                    (1, "情境导入", 10, "学生能提出可探究的问题"),
                    (2, "实验探究", 20, "学生能测出浮力并记录数据"),
                    (3, "规律应用", 10, "学生能用原理解释现象"),
                    (4, "总结", 5, "学生能复述原理与条件"),
                )
            ],
            "interactions": [
                {
                    "title": "浮力变化分类",
                    "interaction_type": "classification",
                    "prompt": "把下列现象按“浮力变大/变小/不变”分类（同一液体）",
                    "items": ["船装货", "石块浸入更深", "换用盐水"],
                    "answer_groups": {"变大": ["换用盐水"], "不变": ["石块浸入更深"]},
                    "explanation": "浮力只与液体密度和排开体积有关。",
                }
            ],
            "output_specs": {
                "pptx": {"max_bullets_per_slide": 5},
                "docx": {"homework": "用原理解释一个生活现象，并写清条件"},
                "pdf": {
                    "printable_summary": "核心术语、公式与适用条件、典型易错点",
                    "assessment_checklist": ["能说出自变量", "能说出控制变量"],
                },
                "html": {"interaction_ids": [], "allow_retry": True},
            },
        },
    }


def test_agentic_runs_skeleton_fill_and_review():
    slide_count = 6
    client = fake_client(slide_count)
    stages = []
    result = generate_courseware_spec_agentic(
        brief_content(),
        [
            EvidenceRef(
                evidence_id="evidence-1",
                source_type="material",
                source_name="浮力实验记录",
                quote="浮力与排开体积有关",
            )
        ],
        client=client,
        on_stage=stages.append,
    )

    # 阶段流：搭骨架 → 填教案与成果 → 讲稿填充 → 审校。
    # 讲稿填充是"起始帧 + 每页一帧"：批次并发跑，但每完成一批就报一次进度，
    # 前端才能看到"正在写每页要点与讲稿（4/12 页）"这样的细分文案。
    assert stages == (
        ["skeleton", "fill_teaching"] + ["fill_slides"] * (slide_count + 1) + ["review"]
    )
    assert client.completions_spy.calls == slide_count + 3
    assert result.prompt_version == AGENTIC_PROMPT_VERSION
    spec = result.spec
    # 互动教具从"填教案与成果"那一段一路带进最终蓝图，并由后端派生 HTML 的 tool_ids
    assert [tool.tool_id for tool in spec.interactive_tools] == ["tool_001"]
    assert spec.interactive_tools[0].engine == "curve"
    assert spec.interactive_tools[0].outputs[0].expression == "rho * g * volume / 1000"
    assert spec.output_specs.html.tool_ids == ["tool_001"]
    assert len(spec.slides) == slide_count
    assert sum(section.duration_minutes for section in spec.lesson_sections) == 45
    # 讲稿来自"填充讲稿"这一段
    assert spec.slides[0].speaker_notes == "第 1 页讲稿：先演示，再追问，预设学生回答并纠正。"
    # 教案四项来自"填充教案"这一段
    assert spec.lesson_sections[0].teacher_actions
    assert spec.lesson_sections[0].assessment
    # PDF 学习要点与达成判准被单独要求过
    assert spec.output_specs.pdf.assessment_checklist
    # 思维链只进日志，不进产物
    dumped = spec.model_dump(mode="json")
    assert "analysis" not in dumped
    assert "plan_notes" not in dumped
    assert "findings" not in dumped


def test_agentic_retries_a_truncated_json_step():
    """某一步返回被截断的 JSON 时要重发一次，而不是把整条流水线打死。

    实测故障：填教案那一步模型返回的 JSON 在 15KB 附近断掉（报 "Expecting ',' delimiter"），
    解析异常一路冒到教师面前变成"生成教学蓝图时出错，请重试"，前面几分钟全部作废。
    """
    slide_count = 3
    client = fake_client(slide_count, truncate_teaching_times=1)
    result = generate_courseware_spec_agentic(brief_content(), [], client=client)

    assert len(result.spec.slides) == slide_count
    # 教案来自"重发"那一次，而不是空壳
    assert result.spec.lesson_sections[0].assessment

    teaching_calls = [
        request
        for request in client.completions_spy.requests
        if "第二步" in request["messages"][0]["content"]
    ]
    assert len(teaching_calls) == 2, "截断后应当重发一次"
    # 截断后的重发要求写得紧凑，而不是原样再来一遍（原样通常会在同一处再截断）
    assert "截断" in teaching_calls[1]["messages"][-1]["content"]


def test_agentic_survives_a_step_that_never_parses():
    """两次都解析不出来时，要降级交付，而不是抛 JSON 解析异常。

    降级的落点由各步自己的兜底决定：教案四项回落骨架内容，后面还有拼装修复接手。
    """
    slide_count = 3
    client = fake_client(slide_count, truncate_teaching_times=99)
    result = generate_courseware_spec_agentic(brief_content(), [], client=client)

    assert len(result.spec.slides) == slide_count
    # 环节结构回落到骨架给出的三个标题，仍是一份可交付的蓝图
    assert [item.title for item in result.spec.lesson_sections]
    assert all(slide.speaker_notes.strip() for slide in result.spec.slides)


def test_agentic_uses_a_different_prompt_per_stage():
    client = fake_client(6)
    generate_courseware_spec_agentic(brief_content(), [], client=client)
    prompts = [request["messages"][0]["content"] for request in client.completions_spy.requests]
    # 按段落标记取提示词，而不是按调用序号：讲稿段的调用次数随页数变化，
    # 按下标解包会在任何粒度调整后立刻失效。
    skeleton_prompt = next(prompt for prompt in prompts if "只搭骨架" in prompt)
    teaching_prompt = next(prompt for prompt in prompts if "第二步" in prompt)
    slides_prompt = next(prompt for prompt in prompts if "第三步" in prompt)
    review_prompt = next(prompt for prompt in prompts if "独立的教学内容审校专家" in prompt)
    # 骨架决定 layout，必须带 layout 判据
    assert "layout" in skeleton_prompt
    # 教案段要写全四项 + PDF 要点，不该再去决定版式
    assert "teacher_actions" in teaching_prompt
    assert "printable_summary" in teaching_prompt
    assert "layout 判据" not in teaching_prompt
    # 讲稿段只写正文
    assert "speaker_notes" in slides_prompt
    # 审校段按学科注入易错点（物理 → 浮力）
    assert "浮力" in review_prompt


def test_agentic_prompts_carry_the_teacher_requirements():
    """教师的明确要求必须进入每一段的提示词。

    以前它们只躺在 user message 的 JSON 里，提示词从头到尾不提，模型遵守与否全靠
    运气——教师说的"不要涉及 XX""要有 XX 环节"就是这么丢的。
    """
    brief = brief_content()
    brief["forbidden_content"] = "不要涉及微积分"
    brief["extra_requirements"] = "每个环节都要有板书设计"
    brief["homework_type"] = "设计一份家庭小实验"
    client = fake_client(3)

    generate_courseware_spec_agentic(brief, [], client=client)

    prompts = [request["messages"][0]["content"] for request in client.completions_spy.requests]
    assert prompts
    for prompt in prompts:
        # 去掉否定前缀后的词条本身要出现，模型才知道具体禁的是什么
        assert "微积分" in prompt, prompt[:80]
        assert "每个环节都要有板书设计" in prompt
        assert "设计一份家庭小实验" in prompt


def test_agentic_repairs_an_invalid_skeleton_once():
    slide_count = 6
    client = fake_client(slide_count, invalid_skeleton_first=True)
    stages = []
    result = generate_courseware_spec_agentic(brief_content(), [], client=client, on_stage=stages.append)
    assert stages[0] == "skeleton"
    assert stages[1] == "repair"
    assert len(result.spec.slides) == slide_count
    # 1 次失败骨架 + 1 次重搭 + 1 次教案 + 每页 1 次讲稿 + 1 次审校
    assert client.completions_spy.calls == slide_count + 4


def test_agentic_fills_each_slide_in_its_own_call():
    """每一页的讲稿独占一次调用。

    整册一次写会让每条都被写短（这是"内容单薄"的根因），分批到页级是同一逻辑的
    彻底版本：这一页能拿到完整的输出预算。
    """
    slide_count = 9
    client = fake_client(slide_count)
    result = generate_courseware_spec_agentic(brief_content(), [], client=client)

    fill_requests = [
        json.loads(request["messages"][1]["content"])
        for request in client.completions_spy.requests
        if "第三步" in request["messages"][0]["content"]
    ]
    assert len(fill_requests) == slide_count
    assert all(len(request["slides"]) == 1 for request in fill_requests)
    assert [request["slides"][0]["order"] for request in fill_requests] == list(
        range(1, slide_count + 1)
    )
    assert len(result.spec.slides) == slide_count
    assert all(slide.speaker_notes for slide in result.spec.slides)


def test_agentic_repairs_rejected_tools_instead_of_silently_dropping_them():
    """万有引力事故：教具读数引用了未声明的 G、M，以前整件被静默丢掉、导出 HTML
    退回纯做题页。现在管线会带着中文原因自动修复一轮，教具留在蓝图里。"""
    client = fake_client(3, broken_tools=True)
    stages = []
    result = generate_courseware_spec_agentic(
        brief_content(), [], client=client, on_stage=stages.append
    )

    assert "repair" in stages
    tool = result.spec.interactive_tools[0]
    assert tool.engine == "scene"
    assert len(tool.scene["entities"]) == 4
    assert any("已自动修复" in note for note in result.spec.generation_notes)

    # 修复请求里带着被拒原始教具与逐条中文原因
    repair_requests = []
    for request in client.completions_spy.requests:
        if "第二步" not in str(request["messages"][0].get("content") or ""):
            continue
        payload = json.loads(request["messages"][1]["content"])
        if isinstance(payload, dict) and "offending" in payload:
            repair_requests.append(payload)
    assert len(repair_requests) == 1
    offending = repair_requests[0]["offending"][0]
    assert offending["tool_id"] == "tool_001"
    assert "公式引用了未定义的量" in offending["problem"]
    assert offending["tool"]["outputs"][0]["expression"].startswith("sqrt(G * M")


def test_agentic_tells_the_teacher_when_tools_cannot_be_repaired():
    """修复不动时把结论写进生成说明，而不是假装这一课没有教具可做。"""
    client = fake_client(3, broken_tools=True, repair_returns_fix=False)
    result = generate_courseware_spec_agentic(brief_content(), [], client=client)

    assert result.spec.interactive_tools == []
    assert any(
        "互动教具未通过结构校验" in note for note in result.spec.generation_notes
    )
