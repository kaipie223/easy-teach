"""Deterministic quality checks for generated courseware snapshots."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from backend.schemas import RENDERED_TOOL_ENGINES, CoursewarePlanSpec

# 翔实度下限。
#
# 生成端已经不再限制内容量（上限只会让内容变薄），于是"够不够扎实"必须有另一套
# 判断。这些阈值刻意只报 warning：它们是质量判断，不该把一份能用的蓝图卡死；
# 真正会让教师白干的（缺页、缺标题、答案对不上）才报 error。
MIN_BULLETS_PER_SLIDE = 2
MIN_SPEAKER_NOTES_CHARS = 80
MIN_SECTION_ACTIONS = 2
MIN_PREPARATION_ITEMS = 2
MIN_HOMEWORK_CHARS = 10
MIN_PRINTABLE_SUMMARY_CHARS = 120
MIN_CHECKLIST_ITEMS = 4

# 禁忌内容的分词与否定前缀。教师的写法很随意（"不要涉及微积分"、"微积分、极限"），
# 这里只做保守切分：宁可漏报，也不要把无关词当命中。
_FORBIDDEN_SPLIT = re.compile(r"[、,，;；。\n]+")
# 否定词要按长度倒序匹配，"不涉及"必须先于"不"这类短词被消费掉。
_FORBIDDEN_MARKERS = (
    "不要",
    "禁止",
    "严禁",
    "不得",
    "避免",
    "不涉及",
    "不含",
    "不能出现",
    "不许",
    "无需",
)
# 否定词后面往往还跟一个动词（"不要涉及微积分"）。只剥否定词会得到"涉及微积分"，
# 这个词在产物里永远匹配不到——必须再剥一层动词，剩下的才是真正的禁忌词。
_FORBIDDEN_VERBS = ("涉及", "出现", "包含", "使用", "提到", "引入", "讲解", "设计")
_MIN_FORBIDDEN_TERM_CHARS = 2


def _issue(code: str, message: str, *, path: str | None = None) -> dict[str, str]:
    item = {"code": code, "message": message}
    if path:
        item["path"] = path
    return item


def forbidden_terms(value: object) -> list[str]:
    """把"禁止出现的内容"拆成可搜索的词条。

    两层剥离：先去掉否定词（"不要 / 禁止 / 不得"），再去掉紧跟的动词
    （"涉及 / 出现 / 使用"）。只剥一层会得到"涉及微积分"这类词——它在产物里
    永远匹配不到，检查看起来在跑，实际全是漏报。

    公开而不是私有：模板兜底路径也要用它把约束原样写进"本课不涉及"，两处必须
    用同一套切分规则，否则教师看到的约束和实际检查的约束会对不上。
    """
    terms: list[str] = []
    for chunk in _FORBIDDEN_SPLIT.split(str(value or "")):
        term = chunk.strip().strip("：:（）()【】[]“”\"'")
        for marker in _FORBIDDEN_MARKERS:
            if term.startswith(marker):
                term = term[len(marker) :].strip("：: ")
                break
        for verb in _FORBIDDEN_VERBS:
            if term.startswith(verb) and len(term) > len(verb):
                term = term[len(verb) :].strip("：: ")
                break
        if len(term) >= _MIN_FORBIDDEN_TERM_CHARS:
            terms.append(term)
    return terms


def _requirement_issues(
    spec: CoursewarePlanSpec,
    brief_content: dict[str, Any] | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """核对需求单里的明确要求有没有被落实。

    只做"能确定性判断"的那一部分——禁忌内容是否出现在产物里。语义层面的匹配
    （风格、案例是否合意）一律不猜：猜错会把一份能用的蓝图判成废品，比漏检更糟。
    """
    if not brief_content:
        return [], []
    terms = forbidden_terms(brief_content.get("forbidden_content"))
    if not terms:
        return [], []
    haystack = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False)
    hits = sorted({term for term in terms if term in haystack})
    if not hits:
        return [], []
    # 分词是启发式的，所以这里只报 warning 并写清命中了哪个词：教师要求"不要出现
    # 微积分"而产物里真的有，需要有人看一眼，但不该由一次字符串匹配来决定成败。
    return [], [
        _issue(
            "FORBIDDEN_CONTENT_PRESENT",
            "产物中出现了需求单要求禁止的内容：" + "、".join(hits),
            path="brief.forbidden_content",
        )
    ]


def inspect_courseware(
    snapshot: CoursewarePlanSpec | dict[str, Any],
    *,
    brief_content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe report without changing the source snapshot.

    Schema validation catches malformed values. These checks cover the
    cross-item invariants that are easy to break during local revisions.

    ``brief_content`` is optional and only used for "教师要求有没有被落实"这类检查。
    拿不到需求单时这些检查直接跳过，而不是猜。
    """
    has_explicit_output_specs = isinstance(snapshot, CoursewarePlanSpec) or (
        isinstance(snapshot, dict) and "output_specs" in snapshot
    )
    try:
        spec = (
            snapshot
            if isinstance(snapshot, CoursewarePlanSpec)
            else CoursewarePlanSpec.model_validate(snapshot)
        )
    except Exception as exc:
        return {
            "status": "failed",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "errors": [_issue("SCHEMA_INVALID", "成果快照未通过 CoursewarePlan 校验")],
            "warnings": [],
            "metrics": {},
            "details": str(exc),
        }

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    collections = {
        "slides": spec.slides,
        "lesson_sections": spec.lesson_sections,
        "interactions": spec.interactions,
    }
    if not spec.slides:
        errors.append(_issue("NO_SLIDES", "成果至少需要包含一页幻灯片", path="slides"))

    for name, items in collections.items():
        ids = [
            item.slide_id
            if name == "slides"
            else item.section_id
            if name == "lesson_sections"
            else item.interaction_id
            for item in items
        ]
        if len(ids) != len(set(ids)):
            errors.append(_issue("DUPLICATE_STABLE_ID", f"{name} 包含重复的稳定 ID", path=name))

        if name != "interactions":
            orders = [item.order for item in items]
            if orders != list(range(1, len(items) + 1)):
                errors.append(
                    _issue("ORDER_NOT_CONTIGUOUS", f"{name} 的 order 必须从 1 连续递增", path=name)
                )

    for index, slide in enumerate(spec.slides):
        if not slide.title.strip():
            errors.append(_issue("EMPTY_TITLE", "幻灯片标题不能为空", path=f"slides[{index}].title"))
        if not slide.bullets:
            warnings.append(_issue("EMPTY_BULLETS", "幻灯片没有要点内容", path=f"slides[{index}].bullets"))
        elif len(slide.bullets) < MIN_BULLETS_PER_SLIDE:
            warnings.append(
                _issue(
                    "THIN_SLIDE_BULLETS",
                    f"第 {slide.order} 页只有 {len(slide.bullets)} 条要点，信息量偏少",
                    path=f"slides[{index}].bullets",
                )
            )
        if (
            has_explicit_output_specs
            and spec.output_specs.pptx.speaker_notes_required
            and not slide.speaker_notes.strip()
        ):
            errors.append(
                _issue(
                    "PPTX_MISSING_SPEAKER_NOTES",
                    "PPT 规范要求每页包含讲稿",
                    path=f"slides[{index}].speaker_notes",
                )
            )
        elif 0 < len(slide.speaker_notes.strip()) < MIN_SPEAKER_NOTES_CHARS:
            # 讲稿只有一句"讲解本页内容"时，教师拿到的是一份没法照着讲的课件。
            warnings.append(
                _issue(
                    "THIN_SPEAKER_NOTES",
                    f"第 {slide.order} 页讲稿只有 {len(slide.speaker_notes.strip())} 字，"
                    "不足以支撑授课",
                    path=f"slides[{index}].speaker_notes",
                )
            )

    for index, section in enumerate(spec.lesson_sections):
        if not section.title.strip() or not section.objective.strip():
            errors.append(
                _issue(
                    "INCOMPLETE_LESSON_SECTION",
                    "教案章节必须包含标题和教学目标",
                    path=f"lesson_sections[{index}]",
                )
            )
        # 教案是教师真正照着上的东西：师生活动各只有一条时，这一节基本没法执行。
        for field_name, label in (("teacher_actions", "教师活动"), ("student_actions", "学生活动")):
            actions = getattr(section, field_name)
            if 0 < len(actions) < MIN_SECTION_ACTIONS:
                warnings.append(
                    _issue(
                        "THIN_SECTION_ACTIONS",
                        f"“{section.title}”的{label}只有 {len(actions)} 条，偏少",
                        path=f"lesson_sections[{index}].{field_name}",
                    )
                )

    for index, interaction in enumerate(spec.interactions):
        if not interaction.prompt.strip():
            errors.append(
                _issue("EMPTY_INTERACTION_PROMPT", "互动题目必须包含提示语", path=f"interactions[{index}].prompt")
            )
        if not interaction.items:
            warnings.append(
                _issue("EMPTY_INTERACTION_ITEMS", "互动内容没有可操作的项目", path=f"interactions[{index}].items")
            )

    section_minutes = sum(section.duration_minutes for section in spec.lesson_sections)
    if section_minutes and abs(section_minutes - spec.duration_minutes) > max(5, round(spec.duration_minutes * 0.2)):
        warnings.append(
            _issue(
                "DURATION_MISMATCH",
                f"教案章节总时长 {section_minutes} 分钟与课程时长 {spec.duration_minutes} 分钟差异较大",
                path="lesson_sections",
            )
        )
    if not spec.evidence_refs:
        warnings.append(_issue("NO_EVIDENCE_REFS", "成果没有关联可回溯的证据来源", path="evidence_refs"))

    docx_spec = spec.output_specs.docx
    if not docx_spec.teacher_preparation:
        warnings.append(_issue("DOCX_NO_PREPARATION", "教案缺少课前准备", path="output_specs.docx"))
    elif len(docx_spec.teacher_preparation) < MIN_PREPARATION_ITEMS:
        warnings.append(
            _issue(
                "DOCX_THIN_PREPARATION",
                f"课前准备只有 {len(docx_spec.teacher_preparation)} 条，偏少",
                path="output_specs.docx.teacher_preparation",
            )
        )
    if not docx_spec.differentiation:
        warnings.append(_issue("DOCX_NO_DIFFERENTIATION", "教案缺少分层支持", path="output_specs.docx"))
    if not docx_spec.homework.strip():
        warnings.append(_issue("DOCX_NO_HOMEWORK", "教案缺少课后任务", path="output_specs.docx.homework"))
    elif len(docx_spec.homework.strip()) < MIN_HOMEWORK_CHARS:
        warnings.append(
            _issue(
                "DOCX_THIN_HOMEWORK",
                "课后任务过于简略，缺少完成标准与提交形式",
                path="output_specs.docx.homework",
            )
        )
    if not docx_spec.reflection_prompts:
        warnings.append(_issue("DOCX_NO_REFLECTION", "教案缺少教学反思问题", path="output_specs.docx"))

    pdf_spec = spec.output_specs.pdf
    if not pdf_spec.printable_summary.strip():
        warnings.append(_issue("PDF_NO_SUMMARY", "打印版缺少课程摘要", path="output_specs.pdf"))
    elif len(pdf_spec.printable_summary.strip()) < MIN_PRINTABLE_SUMMARY_CHARS:
        warnings.append(
            _issue(
                "PDF_THIN_SUMMARY",
                f"打印版学习要点只有 {len(pdf_spec.printable_summary.strip())} 字，"
                "不足以作为可打印的学习材料",
                path="output_specs.pdf.printable_summary",
            )
        )
    if not pdf_spec.assessment_checklist:
        warnings.append(_issue("PDF_NO_CHECKLIST", "打印版缺少评价清单", path="output_specs.pdf"))
    elif len(pdf_spec.assessment_checklist) < MIN_CHECKLIST_ITEMS:
        warnings.append(
            _issue(
                "PDF_THIN_CHECKLIST",
                f"评价清单只有 {len(pdf_spec.assessment_checklist)} 条，覆盖不到本课目标",
                path="output_specs.pdf.assessment_checklist",
            )
        )

    valid_interaction_ids = {item.interaction_id for item in spec.interactions}
    html_ids = spec.output_specs.html.interaction_ids
    if not html_ids:
        warnings.append(_issue("HTML_NO_INTERACTIONS", "互动网页未指定互动内容", path="output_specs.html"))
    elif not set(html_ids).issubset(valid_interaction_ids):
        errors.append(
            _issue(
                "HTML_UNKNOWN_INTERACTION",
                "互动网页引用了蓝图中不存在的互动 ID",
                path="output_specs.html.interaction_ids",
            )
        )

    # 互动教具：全部记为 warning。
    # 教具是"锦上添花"的一层——归一层任何一条不合格的教具，不能让整册校验失败，
    # 否则教师会因为一个滑块没写好而拿不到课件。
    valid_tool_ids = {item.tool_id for item in spec.interactive_tools}
    tool_ids = spec.output_specs.html.tool_ids
    if tool_ids and not set(tool_ids).issubset(valid_tool_ids):
        warnings.append(
            _issue(
                "HTML_UNKNOWN_TOOL",
                "互动网页引用了蓝图中不存在的教具 ID",
                path="output_specs.html.tool_ids",
            )
        )
    for tool in spec.interactive_tools:
        if tool.engine not in RENDERED_TOOL_ENGINES:
            warnings.append(
                _issue(
                    "TOOL_ENGINE_FALLBACK",
                    f"教具「{tool.title}」的引擎 {tool.engine} 还没有专门实现，"
                    "网页会按曲线引擎渲染",
                    path=f"interactive_tools.{tool.tool_id}.engine",
                )
            )
        # 手工编辑或导入的蓝图不经过教具归一层，这里再兜一次底：声明了 scene
        # 却没有图元的教具在页面上就是一块空白画布。
        if tool.engine == "scene" and not (
            isinstance(tool.scene, dict) and tool.scene.get("entities")
        ):
            warnings.append(
                _issue(
                    "TOOL_SCENE_EMPTY",
                    f"教具「{tool.title}」声明了场景引擎但没有可绘制的图元，"
                    "网页里只会是一块空白画布",
                    path=f"interactive_tools.{tool.tool_id}.scene",
                )
            )
        if len(tool.guided_steps) < 3:
            warnings.append(
                _issue(
                    "TOOL_THIN_STEPS",
                    f"教具「{tool.title}」只有 {len(tool.guided_steps)} 步引导，"
                    "学生容易乱调参数而看不出结论",
                    path=f"interactive_tools.{tool.tool_id}.guided_steps",
                )
            )
        if not tool.predict_prompts:
            warnings.append(
                _issue(
                    "TOOL_NO_PREDICTION",
                    f"教具「{tool.title}」没有先预测环节，学生会变成先看现象再补解释",
                    path=f"interactive_tools.{tool.tool_id}.predict_prompts",
                )
            )
        if not tool.goal.strip():
            warnings.append(
                _issue(
                    "TOOL_NO_GOAL",
                    f"教具「{tool.title}」没有写清可观测目标",
                    path=f"interactive_tools.{tool.tool_id}.goal",
                )
            )

    requirement_errors, requirement_warnings = _requirement_issues(spec, brief_content)
    errors.extend(requirement_errors)
    warnings.extend(requirement_warnings)

    notes_lengths = [len(slide.speaker_notes.strip()) for slide in spec.slides]
    status = "failed" if errors else "warning" if warnings else "passed"
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "slide_count": len(spec.slides),
            "lesson_section_count": len(spec.lesson_sections),
            "interaction_count": len(spec.interactions),
            "evidence_ref_count": len(spec.evidence_refs),
            "lesson_duration_minutes": section_minutes,
            "pptx_max_bullets": spec.output_specs.pptx.max_bullets_per_slide,
            "html_interaction_count": len(html_ids),
            # 翔实度指标：只做观测，不参与判定。有它们才看得出"内容变厚了没有"，
            # 否则每次调整提示词都只能凭感觉。
            "bullets_per_slide_avg": (
                round(sum(len(slide.bullets) for slide in spec.slides) / len(spec.slides), 2)
                if spec.slides
                else 0
            ),
            "speaker_notes_chars_avg": (
                round(sum(notes_lengths) / len(notes_lengths), 1) if notes_lengths else 0
            ),
            "speaker_notes_chars_min": min(notes_lengths) if notes_lengths else 0,
            "printable_summary_chars": len(spec.output_specs.pdf.printable_summary.strip()),
            "checklist_items": len(spec.output_specs.pdf.assessment_checklist),
        },
    }


def require_courseware_quality(
    snapshot: CoursewarePlanSpec | dict[str, Any],
    *,
    brief_content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Raise for blocking quality errors and return the report otherwise."""
    report = inspect_courseware(snapshot, brief_content=brief_content)
    if report["status"] == "failed":
        messages = "; ".join(item["message"] for item in report["errors"])
        raise RuntimeError(f"成果质量检查失败：{messages}")
    return report
