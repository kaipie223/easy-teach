from __future__ import annotations

import re
from pathlib import Path

from .intermediate_schemas import (
    ContentBlock,
    DemoGenerationPlan,
    DemoGenerationRequest,
    InteractiveQuestion,
    InteractiveSpec,
    LessonPlanSection,
    LessonPlanSpec,
    PlanStage,
    SlideDeckSpec,
    SlideSpec,
    SpecElement,
    TeachingUnit,
)
from .package import LoadedTeachingContentPackage


_MAX_STAGE_DISPLAY_BLOCKS = 5
_MAX_DISPLAY_TEXT_CHARS = 180
_VERIFIED_DISPLAY_FLAGS = {
    "locally_verified",
    "model_text_locally_validated",
    "knowledge_point_locally_validated",
}


def build_demo_generation_plan(
    package: LoadedTeachingContentPackage,
    request: DemoGenerationRequest | None = None,
) -> DemoGenerationPlan:
    request = request or DemoGenerationRequest()
    ir = package.ir
    title = request.title or _default_title(package)
    stages: list[PlanStage] = []
    key_points: list[str] = []
    source_refs: list[str] = []
    asset_refs: list[str] = []
    suppressed_raw_asr_units = 0
    for index, unit in enumerate(ir.teaching_units, start=1):
        contents = _stage_content(unit, include_answer_key=request.include_answer_key)
        if _has_suppressed_raw_asr(unit):
            suppressed_raw_asr_units += 1
        stage_status = "suggested" if unit.status == "unresolved" else "observed"
        if unit.review_flags or not _presentation_blocks(unit, include_answer_key=request.include_answer_key):
            stage_status = "suggested"
        stage = PlanStage(
            stage_id=f"stage_{index:03d}",
            unit_id=unit.id,
            title=_presentation_title(unit),
            purpose=_purpose_for_unit(unit),
            content=contents,
            pedagogical_role=unit.pedagogical_roles[0] if unit.pedagogical_roles else "explanation",
            evidence_refs=unit.evidence_refs,
            asset_refs=_unit_assets(unit),
            status=stage_status,  # type: ignore[arg-type]
            student_visible=bool(_presentation_blocks(unit, include_answer_key=request.include_answer_key)),
        )
        stages.append(stage)
        if _presentation_blocks(unit, include_answer_key=request.include_answer_key):
            key_points.append(stage.title)
        source_refs.extend(unit.evidence_refs)
        asset_refs.extend(_unit_assets(unit))

    warnings = list(ir.quality.warnings)
    if ir.unresolved_items:
        warnings.append(f"{len(ir.unresolved_items)} 项内容需要人工复核，未作为确定事实补写。")
    if ir.conflicts:
        warnings.append(f"{len(ir.conflicts)} 项跨模态冲突保留在 IR 中。")
    if suppressed_raw_asr_units:
        warnings.append(
            f"{suppressed_raw_asr_units} 个教学单元的长 ASR 仅保留在 package/Evidence 中，未直接写入生成正文。"
        )
    if ir.course_context.get("semantic_grouping") == "circuit_lesson_v1":
        objectives = [
            "能定义并判断通路、断路、电源短路和用电器短路。",
            "能沿着电流从正极回到负极的路径分析灯泡或 LED 的工作状态。",
            "能解释导线等效低电阻路径为什么会绕过用电器，并说出短路危害。",
            "能把实物图与符号电路图按连接关系对应起来。",
        ]
    else:
        objectives = [f"理解：{point}" for point in key_points[:4]]
    return DemoGenerationPlan(
        plan_id=f"plan_{package.manifest.package_id}_{package.manifest.package_version.replace('.', '_')}",
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        ir_schema_version=ir.schema_version,
        request=request,
        title=title,
        course_context=ir.course_context,
        learning_objectives=objectives,
        key_points=_unique(key_points),
        stages=stages,
        source_refs=_unique(source_refs),
        asset_refs=_unique(asset_refs),
        estimated_minutes=request.estimated_minutes,
        warnings=_unique(warnings),
    )


def build_slide_deck_spec(
    package: LoadedTeachingContentPackage,
    plan: DemoGenerationPlan,
) -> SlideDeckSpec:
    slides: list[SlideSpec] = [
        SlideSpec(
            slide_id="slide_001_title",
            order=1,
            role="title",
            title=plan.title,
            elements=[
                SpecElement(id="element_title", element_type="text", text=plan.title, source_refs=plan.source_refs[:4]),
                SpecElement(id="element_subtitle", element_type="text", text=f"{plan.request.audience} · {plan.estimated_minutes} 分钟", source_refs=plan.source_refs[:2]),
            ],
            source_refs=plan.source_refs[:4],
        ),
        SlideSpec(
            slide_id="slide_002_objectives",
            order=2,
            role="objective",
            title="本节学习目标",
            elements=[
                SpecElement(id=f"objective_{index:03d}", element_type="bullet", text=value, source_refs=plan.source_refs[:3])
                for index, value in enumerate(plan.learning_objectives, start=1)
            ],
            source_refs=plan.source_refs[:6],
        ),
    ]
    next_order = 3
    for stage in plan.stages:
        if len(slides) >= plan.request.max_slides - 1:
            break
        unit = next((item for item in package.ir.teaching_units if item.id == stage.unit_id), None)
        if unit is None:
            continue
        display_blocks = _presentation_blocks(unit, include_answer_key=plan.request.include_answer_key)
        # A unit represented only by long/raw ASR remains auditable in the
        # package and teaching plan, but must not become a blank student slide.
        if not display_blocks and plan.request.student_mode:
            continue
        elements: list[SpecElement] = []
        for block_index, block in enumerate(display_blocks):
            element = _spec_element_from_block(block, stage, block_index, plan.request.student_mode)
            if element is not None:
                elements.append(element)
        if not elements:
            elements.append(
                SpecElement(
                    id=f"{stage.stage_id}_fallback",
                    element_type="text",
                    text="该单元暂无可直接展示的结构化内容，请回看来源关键帧。",
                    source_refs=stage.evidence_refs,
                    visibility="teacher" if plan.request.student_mode else "student",
                )
            )
        slides.append(
            SlideSpec(
                slide_id=f"slide_{next_order:03d}_{stage.stage_id}",
                order=next_order,
                role=_slide_role(stage),
                title=stage.title,
                elements=elements,
                source_refs=stage.evidence_refs,
                asset_refs=stage.asset_refs,
                answer_reveal=False,
                constraints={
                    "max_chars": _MAX_DISPLAY_TEXT_CHARS,
                    "max_elements": _MAX_STAGE_DISPLAY_BLOCKS,
                    "min_font_pt": 18,
                    "max_images": 3,
                },
            )
        )
        next_order += 1
        question_blocks = [block for block in display_blocks if block.block_type == "question"]
        answer_blocks = [block for block in display_blocks if block.block_type == "answer"]
        if question_blocks and len(slides) < plan.request.max_slides - 1:
            question_elements = [
                element
                for index, block in enumerate(question_blocks)
                if (element := _spec_element_from_block(block, stage, index, True)) is not None
            ]
            slides.append(
                SlideSpec(
                    slide_id=f"slide_{next_order:03d}_{stage.stage_id}_question",
                    order=next_order,
                    role="question",
                    title="想一想",
                    elements=question_elements[:_MAX_STAGE_DISPLAY_BLOCKS],
                    source_refs=stage.evidence_refs,
                    answer_reveal=False,
                )
            )
            next_order += 1
        if answer_blocks and plan.request.include_answer_key and len(slides) < plan.request.max_slides - 1:
            answer_elements = [
                element
                for index, block in enumerate(answer_blocks)
                if (element := _spec_element_from_block(block, stage, index, False)) is not None
            ]
            slides.append(
                SlideSpec(
                    slide_id=f"slide_{next_order:03d}_{stage.stage_id}_answer",
                    order=next_order,
                    role="summary",
                    title="参考答案",
                    elements=answer_elements[:_MAX_STAGE_DISPLAY_BLOCKS],
                    source_refs=stage.evidence_refs,
                    answer_reveal=True,
                )
            )
            next_order += 1
    slides.append(
        SlideSpec(
            slide_id=f"slide_{next_order:03d}_sources",
            order=next_order,
            role="source",
            title="来源与复核提示",
            elements=[
                SpecElement(
                    id="source_note",
                    element_type="source",
                    text="视频语音与关键帧证据；术语已规范化，原始 ASR 保留在中间包中。",
                    source_refs=plan.source_refs,
                )
            ],
            source_refs=plan.source_refs,
        )
    )
    return SlideDeckSpec(plan_id=plan.plan_id, slides=slides)


def build_lesson_plan_spec(package: LoadedTeachingContentPackage, plan: DemoGenerationPlan) -> LessonPlanSpec:
    sections: list[LessonPlanSection] = []
    per_stage = max(1, plan.estimated_minutes // max(1, len(plan.stages)))
    for stage in plan.stages:
        sections.append(
            LessonPlanSection(
                section_id=stage.stage_id,
                title=stage.title,
                minutes=per_stage,
                teacher_activity=[f"结合来源证据讲解：{item}" for item in stage.content[:3]] or ["展示对应关键帧并引导观察。"],
                student_activity=["记录要点并用自己的话复述。"],
                assessment=[f"检查学生是否能说明“{stage.title}”的核心含义。"],
                source_refs=stage.evidence_refs,
                unit_id=stage.unit_id,
            )
        )
    difficulties = [
        _presentation_title(unit)
        for unit in package.ir.teaching_units
        if unit.review_flags or unit.status == "unresolved" or any(block.review_flags for block in unit.content_blocks)
    ]
    return LessonPlanSpec(
        plan_id=plan.plan_id,
        title=plan.title,
        audience=plan.request.audience,
        objectives=plan.learning_objectives,
        key_points=plan.key_points,
        difficulties=difficulties,
        preparation=["准备中间包内关键帧资源和投影设备。"],
        sections=sections,
        homework=["根据来源证据复述一个知识点，并标注对应时间点。"],
        reflection=["记录仍需人工复核的公式、图表或视觉内容。"],
        teacher_only_notes=plan.warnings,
        source_refs=plan.source_refs,
    )


def build_interactive_spec(package: LoadedTeachingContentPackage, plan: DemoGenerationPlan) -> InteractiveSpec:
    # 决策 2 / 选项 a：学生版不产出互动判题页。判题是客户端 JS 比对 correct_answer，
    # 答案必须写进页面（看源码就能读到），所以"学生版 + 交互页"本质冲突 ——
    # 与其把答案藏起来，不如直接不生成。
    if not plan.request.include_answer_key:
        return InteractiveSpec(
            plan_id=plan.plan_id,
            title=f"{plan.title} · 互动自检",
            objective="学生版不提供答案，本次未生成互动自检页。",
            questions=[],
            source_refs=plan.source_refs,
        )
    if package.ir.course_context.get("semantic_grouping") == "circuit_lesson_v1":
        return _build_circuit_interactive_spec(package, plan)
    questions: list[InteractiveQuestion] = []
    for stage in plan.stages:
        unit = next((item for item in package.ir.teaching_units if item.id == stage.unit_id), None)
        if unit is None:
            continue
        display_blocks = _presentation_blocks(unit)
        # Do not turn an unverified/raw-ASR-only unit into a student question.
        # It remains available through the package evidence and teacher review
        # plan until a concise locally grounded block exists.
        if not display_blocks:
            continue
        question_blocks = [block for block in display_blocks if block.block_type == "question"]
        answer_blocks = [block for block in display_blocks if block.block_type == "answer"]
        if question_blocks:
            for index, block in enumerate(question_blocks, start=1):
                details = block.metadata.get("details", {}) if isinstance(block.metadata, dict) else {}
                options = [str(item) for item in details.get("options", []) or []]
                answer = str(details.get("correct_answer")) if details.get("correct_answer") else None
                answer_block = answer_blocks[index - 1] if index <= len(answer_blocks) else None
                if answer_block and not answer:
                    answer = answer_block.text.strip() or None
                question_type = "single_choice" if options and answer in options else "open_text"
                questions.append(
                    InteractiveQuestion(
                        question_id=f"question_{len(questions) + 1:03d}",
                        unit_id=unit.id,
                        prompt=block.text or f"请解释：{unit.topic}",
                        question_type=question_type,  # type: ignore[arg-type]
                        options=options,
                        correct_answer=answer if question_type == "single_choice" else None,
                        answer_explanation=answer_block.text if answer_block else "请结合来源关键帧自检。",
                        evidence_refs=block.evidence_refs,
                        source_refs=stage.evidence_refs,
                    )
                )
        elif display_blocks:
            questions.append(
                InteractiveQuestion(
                    question_id=f"question_{len(questions) + 1:03d}",
                    unit_id=unit.id,
                    prompt=f"请用一句话复述“{stage.title}”的核心内容。",
                    question_type="reflection",
                    answer_explanation="这是开放式自检，请对照来源证据检查是否遗漏关键条件。",
                    evidence_refs=stage.evidence_refs,
                    source_refs=stage.evidence_refs,
                )
            )
        if len(questions) >= 8:
            break
    return InteractiveSpec(
        plan_id=plan.plan_id,
        title=f"{plan.title} · 互动自检",
        objective=plan.learning_objectives[0] if plan.learning_objectives else "复习本节核心内容",
        questions=questions,
        source_refs=plan.source_refs,
    )


def _build_circuit_interactive_spec(package: LoadedTeachingContentPackage, plan: DemoGenerationPlan) -> InteractiveSpec:
    """Create content questions from the circuit lesson, not generic reflections."""
    units = {unit.id: unit for unit in package.ir.teaching_units}
    by_key = {
        "path": "unit_0001",
        "open": "unit_0002",
        "source_short": "unit_0003",
        "representation": "unit_0004",
        "appliance_short": "unit_0005",
        "led": "unit_0006",
        "switch_control": "unit_0007",
    }

    def refs(key: str) -> list[str]:
        unit = units.get(by_key[key])
        return unit.evidence_refs if unit else plan.source_refs[:8]

    questions = [
        InteractiveQuestion(
            question_id="question_001",
            unit_id=by_key["path"],
            prompt="下列哪种情况属于通路？",
            question_type="single_choice",
            options=["电流从正极经过开关和灯泡回到负极，灯泡发光", "开关断开，灯泡不亮", "导线直接连接电源正、负极"],
            correct_answer="电流从正极经过开关和灯泡回到负极，灯泡发光",
            answer_explanation="通路要求回路完整，电流能从正极经用电器回到负极。",
            evidence_refs=refs("path"),
            source_refs=refs("path"),
        ),
        InteractiveQuestion(
            question_id="question_002",
            unit_id=by_key["open"],
            prompt="开关已经闭合，但导线中间有一处没有接上，电路应判断为：",
            question_type="single_choice",
            options=["通路，灯泡一定发光", "断路，回路不完整，灯泡不亮", "电源短路，电流特别大"],
            correct_answer="断路，回路不完整，灯泡不亮",
            answer_explanation="判断断路要检查整条回路；只要任一处断开，电流就不能通过。",
            evidence_refs=refs("open"),
            source_refs=refs("open"),
        ),
        InteractiveQuestion(
            question_id="question_003",
            unit_id=by_key["source_short"],
            prompt="为什么不能用导线直接连接电池的正、负极？",
            question_type="single_choice",
            options=["会形成电源短路，电流很大并可能发热、烧坏电源", "会让电流完全消失，但没有安全问题", "会让灯泡变得更亮且一定不会发热"],
            correct_answer="会形成电源短路，电流很大并可能发热、烧坏电源",
            answer_explanation="电源短路的电阻很小，存在发热和损坏风险，不能随意尝试。",
            evidence_refs=refs("source_short"),
            source_refs=refs("source_short"),
        ),
        InteractiveQuestion(
            question_id="question_004",
            unit_id=by_key["appliance_short"],
            prompt="导线并接在 L1 两端，L1 和仍在回路中的 L2 通常会怎样？",
            question_type="single_choice",
            options=["L1 被短路而不工作，L2 仍可能发光", "L1 和 L2 都一定断路", "L1 发光而 L2 一定不发光"],
            correct_answer="L1 被短路而不工作，L2 仍可能发光",
            answer_explanation="导线绕过 L1 后，电流不经过 L1；没有被绕过且仍在完整回路中的 L2 可以工作。",
            evidence_refs=refs("appliance_short"),
            source_refs=refs("appliance_short"),
        ),
        InteractiveQuestion(
            question_id="question_005",
            unit_id=by_key["led"],
            prompt="关于 LED 的接法，哪项正确？",
            question_type="single_choice",
            options=["长脚接正极、短脚接负极，电流方向正确时发光", "短脚接正极、长脚接负极，一定发光", "LED 没有方向，任意接都一样"],
            correct_answer="长脚接正极、短脚接负极，电流方向正确时发光",
            answer_explanation="LED 具有单向导电性，方向接反时不发光。",
            evidence_refs=refs("led"),
            source_refs=refs("led"),
        ),
        InteractiveQuestion(
            question_id="question_006",
            unit_id=by_key["representation"],
            prompt="把同一电路画成实物图和符号电路图时，最重要的是保持什么？",
            question_type="single_choice",
            options=["元件在纸面上的位置完全一样", "连接关系和电流路径一致", "每条导线都必须画成同样长度"],
            correct_answer="连接关系和电流路径一致",
            answer_explanation="同一连接关系可以有不同的画法，不能只凭线条位置判断电路状态。",
            evidence_refs=refs("representation"),
            source_refs=refs("representation"),
        ),
    ]
    return InteractiveSpec(
        plan_id=plan.plan_id,
        title=f"{plan.title} · 互动自检",
        objective="用两条电流走路法则判断通路、断路和短路。",
        questions=questions,
        source_refs=plan.source_refs,
    )


def _stage_content(unit: TeachingUnit, *, include_answer_key: bool = True) -> list[str]:
    values: list[str] = []
    for block in _presentation_blocks(unit, include_answer_key=include_answer_key):
        text = _display_text(block)
        if text:
            prefix = "公式：" if block.block_type == "formula" else ""
            values.append(f"{prefix}{text}")
    if not values:
        values.append("本单元暂未形成可直接展示的已验证摘要，请按来源时间点回看 Evidence。")
    return _unique(values)[:6]


def _presentation_blocks(unit: TeachingUnit, *, include_answer_key: bool = True) -> list[ContentBlock]:
    """Select compact, locally grounded blocks for generated teaching material.

    The IR intentionally keeps raw ASR and every visual observation for audit and
    later review.  That complete evidence set is not a suitable slide body: a
    long transcript chunk or a repeated frame description quickly overwhelms a
    page.  Generation therefore uses only verified candidate text, short
    structured blocks, or blocks backed by a package asset, while retaining all
    original evidence references on the selected elements and stage.
    """

    ranked: list[tuple[int, int, ContentBlock]] = []
    for index, block in enumerate(unit.content_blocks):
        # 学生版（include_answer_key=False）不把 answer 块选进展示内容：
        # 否则答案会直接出现在普通内容页正文里，这个开关就形同虚设。
        if not include_answer_key and block.block_type == "answer":
            continue
        if not _is_presentable_block(block):
            continue
        priority = _presentation_priority(block)
        if priority <= 0:
            continue
        ranked.append((priority, index, block))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[ContentBlock] = []
    seen_text: set[str] = set()
    for _, _, block in ranked:
        normalized = _normalize_display_text(block.text or block.latex or "")
        if not normalized or normalized in seen_text:
            continue
        seen_text.add(normalized)
        selected.append(block)
        if len(selected) >= _MAX_STAGE_DISPLAY_BLOCKS:
            break
    return selected


def _is_presentable_block(block: ContentBlock) -> bool:
    if block.visibility == "hidden":
        return False
    text = _normalize_display_text(block.text or block.latex or "")
    if not text:
        return bool(block.asset_refs)

    metadata = block.metadata if isinstance(block.metadata, dict) else {}
    source = str(metadata.get("source") or "").strip().lower()
    verified = block.status in {"inferred", "corrected"} or bool(
        _VERIFIED_DISPLAY_FLAGS.intersection(block.review_flags)
    )

    # Raw transcript is retained in Evidence, but never copied verbatim into
    # a generated page.  Candidate summaries use a different provenance and
    # are allowed only after the local verification gate has marked them.
    if source == "transcript" and not verified:
        return False
    if verified:
        return True
    if block.asset_refs and block.block_type in {
        "diagram",
        "image",
        "formula",
        "table",
        "chart",
        "text",
    }:
        return True
    if block.block_type in {"question", "options", "solution", "answer", "operation_step"}:
        return len(text) <= _MAX_DISPLAY_TEXT_CHARS
    # Short non-transcript structured text (for example a local summary) is
    # safe to show; long unverified prose is treated as raw evidence.
    return source != "transcript" and len(text) <= _MAX_DISPLAY_TEXT_CHARS


def _presentation_priority(block: ContentBlock) -> int:
    verified = block.status in {"inferred", "corrected"} or bool(
        _VERIFIED_DISPLAY_FLAGS.intersection(block.review_flags)
    )
    if verified:
        return 100
    if block.block_type in {"question", "options", "solution", "answer", "operation_step"}:
        return 90
    if block.block_type in {"formula", "table", "chart", "diagram", "image"} and block.asset_refs:
        return 82
    if block.asset_refs:
        return 76
    return 60


def _display_text(block: ContentBlock) -> str:
    value = block.text or (f"公式：{block.latex}" if block.latex else "")
    return _compact_display_text(value, _MAX_DISPLAY_TEXT_CHARS)


def _compact_display_text(value: str | None, limit: int = _MAX_DISPLAY_TEXT_CHARS) -> str:
    text = _normalize_display_text(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _normalize_display_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _has_suppressed_raw_asr(unit: TeachingUnit) -> bool:
    return any(
        str((block.metadata or {}).get("source") or "").strip().lower() == "transcript"
        and not _is_presentable_block(block)
        for block in unit.content_blocks
    )


def _presentation_title(unit: TeachingUnit) -> str:
    """Keep noisy ASR-like unit titles out of student-facing artifacts."""

    topic = _normalize_display_text(unit.topic)
    context = " ".join(
        [topic]
        + [
            _normalize_display_text(block.text)
            for block in unit.content_blocks[:12]
            if block.text.strip()
        ]
    )
    # 电路概念改名只在上下文里真的出现对应概念时生效；命中"这个 / 然后 / 对吧"
    # 这类中文口语高频词不再触发改名 —— 那会让任何口语化主题都掉进电路专用分支。
    if "通路" in context and "断路" in context and "短路" in context:
        return "通路、断路与短路判断"
    if "短路" in context:
        return "短路判断与安全提醒"
    if "断路" in context:
        return "断路判断"
    if "通路" in context:
        return "通路判断"
    if "LED" in context.upper() or "长脚" in context:
        return "LED 接法与单向导电"
    # 兜底回退到主题原文，不再用"补充说明（待复核）"这类占位符顶替真实标题。
    return _compact_display_text(topic, 28) or "未命名单元"


def _purpose_for_unit(unit: TeachingUnit) -> str:
    if unit.topic.startswith("通路"):
        return "建立完整回路的判断标准，并用灯泡发光现象验证。"
    if unit.topic.startswith("断路"):
        return "对比开关断开、导线断开和灯丝断开，明确回路不完整的结果。"
    if unit.topic.startswith("电源短路"):
        return "说明电源两端被导线直接连接后的电流路径、危险和安全边界。"
    if unit.topic.startswith("实物图"):
        return "把画面中的实物连接与符号电路图按拓扑关系对应起来。"
    if unit.topic.startswith("用电器短路"):
        return "判断导线绕过某个用电器后的亮灭变化，并形成分析步骤。"
    if unit.topic.startswith("LED"):
        return "理解 LED 的单向导电性，并联系用电器短路现象。"
    if unit.topic.startswith("利用短路"):
        return "用 S1、S2 示例说明改变电流路径可以控制用电器亮灭。"
    if unit.topic.startswith("总结"):
        return "把本课内容收束为两条电流走路法则和一套检查顺序。"
    roles = set(unit.pedagogical_roles)
    if "question" in roles:
        return "通过问题检查学生是否理解该知识点。"
    if "example" in roles or "solution" in roles:
        return "用来源中的例题或过程帮助学生建立解题路径。"
    if "operation" in roles:
        return "观察操作前后状态，按证据复述关键步骤。"
    return "从来源证据中提炼并复述核心概念。"


def _unit_assets(unit: TeachingUnit) -> list[str]:
    return _unique([asset for block in unit.content_blocks for asset in block.asset_refs])


def _spec_element_from_block(block: ContentBlock, stage: PlanStage, index: int, student_mode: bool) -> SpecElement | None:
    if block.visibility == "hidden" and student_mode:
        return None
    mapping = {
        "text": "text",
        "formula": "formula",
        "table": "table",
        "chart": "chart",
        "diagram": "image",
        "code": "text",
        "question": "question",
        "options": "question",
        "solution": "text",
        "answer": "answer",
        "operation_step": "bullet",
        "image": "image",
        "unknown_visual": "image",
    }
    element_type = mapping.get(block.block_type)
    if element_type is None:
        return None
    return SpecElement(
        id=f"{stage.stage_id}_{block.id}_{index:03d}",
        element_type=element_type,  # type: ignore[arg-type]
        text=_display_text(block),
        latex=_compact_display_text(block.latex, 120) if block.latex else None,
        asset_refs=block.asset_refs,
        source_refs=block.evidence_refs,
        visibility="teacher" if block.status == "unresolved" and student_mode else block.visibility,
        metadata={"block_type": block.block_type, "review_flags": block.review_flags, "details": block.metadata},
    )


def _slide_role(stage: PlanStage) -> str:
    if stage.pedagogical_role in {"example", "solution"}:
        return "example"
    if stage.pedagogical_role == "question":
        return "question"
    if stage.pedagogical_role in {"summary", "conclusion"}:
        return "summary"
    return "explanation"


def _default_title(package: LoadedTeachingContentPackage) -> str:
    course_title = package.ir.course_context.get("course_title")
    if isinstance(course_title, str) and course_title.strip():
        return course_title.strip()
    name = Path(package.manifest.source_video.get("file_name", "教学视频")).stem
    name = re.sub(r"^\[[^]]+\]", "", name).strip()
    return name or "教学视频学习单"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
