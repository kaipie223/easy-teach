"""M4 courseware-plan compiler and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any, Callable, Iterable, Iterator

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.models.brief import TeachingBrief
from backend.models.courseware import CoursewarePlan
from backend.models.material import EvidenceChunk, Material
from backend.models.project import Project
from backend.models.session import gen_id
from backend.schemas import (
    CoursewarePlanInfo,
    CoursewarePlanSpec,
    DocxContentSpec,
    EvidenceRef,
    HtmlContentSpec,
    InteractionSpec,
    KnowledgePoint,
    LessonPlanSectionSpec,
    OutputContentSpecs,
    PdfContentSpec,
    PptxContentSpec,
    RAGDocument,
    SlideSpec,
)
from backend.services.brief import get_confirmed_brief, normalize_content
from backend.services.courseware_ai import CoursewareAIError, generate_courseware_spec
from backend.services.materials import list_project_images
from backend.services.quality import require_courseware_quality
from backend.services.version_allocator import project_version_lock


def get_latest_plan(db: DBSession, project_id: str) -> CoursewarePlan | None:
    return (
        db.query(CoursewarePlan)
        .filter(CoursewarePlan.project_id == project_id)
        .order_by(CoursewarePlan.version.desc(), CoursewarePlan.created_at.desc())
        .first()
    )


def get_plan_for_project(
    db: DBSession,
    project_id: str,
    plan_id: str | None = None,
) -> CoursewarePlan | None:
    query = db.query(CoursewarePlan).filter(CoursewarePlan.project_id == project_id)
    if plan_id:
        query = query.filter(CoursewarePlan.plan_id == plan_id)
    else:
        query = query.order_by(CoursewarePlan.version.desc(), CoursewarePlan.created_at.desc())
    return query.first()


def _allocate_durations(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base, remainder = divmod(max(total, count), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _evidence_ref_from_chunk(
    chunk: EvidenceChunk,
    *,
    material_names: dict[str, str],
    document_names: dict[str, str],
) -> EvidenceRef:
    if chunk.material_id:
        source_name = material_names.get(chunk.material_id, "上传资料")
    else:
        source_name = document_names.get(chunk.knowledge_document_id or "", "知识库资料")
    return EvidenceRef(
        evidence_id=chunk.evidence_id,
        document_id=chunk.knowledge_document_id,
        source_type=chunk.source_type,
        source_name=source_name,
        locator=chunk.locator_json or {},
        quote=(chunk.text or "")[:280],
    )


def _build_evidence_refs(
    db: DBSession,
    project_id: str,
    rag_docs: Iterable[RAGDocument],
) -> list[EvidenceRef]:
    materials = {
        item.material_id: item.original_name
        for item in db.query(Material)
        .filter(Material.project_id == project_id, Material.deleted_at.is_(None))
        .all()
    }
    material_ids = list(materials)
    chunks = []
    if material_ids:
        chunks = (
            db.query(EvidenceChunk)
            .filter(
                EvidenceChunk.is_valid.is_(True),
                EvidenceChunk.material_id.in_(material_ids),
            )
            .order_by(EvidenceChunk.created_at.asc())
            .limit(12)
            .all()
        )

    refs: list[EvidenceRef] = [
        _evidence_ref_from_chunk(
            chunk,
            material_names=materials,
            document_names={},
        )
        for chunk in chunks
    ]
    seen = {ref.evidence_id or f"{ref.source_name}:{ref.quote}" for ref in refs}
    for raw_doc in rag_docs:
        doc = RAGDocument.model_validate(raw_doc)
        key = doc.evidence_id or f"rag:{doc.document_id}:{doc.source}:{doc.locator}"
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            EvidenceRef(
                evidence_id=doc.evidence_id,
                document_id=doc.document_id,
                source_type="rag",
                source_name=doc.source,
                locator=doc.locator,
                quote=doc.content[:280],
                score=doc.score,
            )
        )
    return refs[:20]


def _refs_for(index: int, refs: list[EvidenceRef]) -> list[EvidenceRef]:
    if not refs:
        return []
    start = index % len(refs)
    selected = [refs[start]]
    if len(refs) > 1:
        selected.append(refs[(start + 1) % len(refs)])
    return selected


def _is_low_stimulation(content: dict) -> bool:
    """Select the restrained teaching sequence from audience/style signals."""
    profile = " ".join(
        str(content.get(field) or "")
        for field in ("target_audience", "style_preference", "extra_requirements")
    )
    return any(keyword in profile for keyword in ("低刺激", "特需", "特殊教育"))


def _target_slide_count(content: dict) -> int:
    """Return a bounded page budget while allowing future brief-level overrides."""
    requested = content.get("slide_count")
    try:
        if requested is not None and 6 <= int(requested) <= 16:
            return int(requested)
    except (TypeError, ValueError):
        pass
    return 8 if _is_low_stimulation(content) else 10


def _group_points(points: list[KnowledgePoint], max_groups: int) -> list[list[KnowledgePoint]]:
    if not points:
        return [[]]
    group_size = max(1, ceil(len(points) / max_groups))
    return [points[start : start + group_size] for start in range(0, len(points), group_size)]


def _point_bullets(points: list[KnowledgePoint]) -> list[str]:
    """Build the bullets for one knowledge-point slide.

    知识点既没有要点也没有示例时，不能把标题原样当作要点——那会让幻灯片变成"标题与
    唯一要点完全相同"的空壳。改为给出与该知识点相关的学习任务：它是可执行的指引，
    而不是凭空编造的知识点内容。
    """
    bullets: list[str] = []
    for point in points:
        details = list(point.key_points)
        if point.examples:
            details.append("示例：" + "；".join(point.examples))
        if details:
            if len(points) == 1:
                bullets.extend(details)
            else:
                bullets.append(f"{point.title}：" + "；".join(details))
            continue
        fallback = [
            f"用自己的话解释“{point.title}”",
            f"举一个与“{point.title}”相关的例子",
        ]
        if len(points) == 1:
            bullets.extend(fallback)
        else:
            bullets.append(f"{point.title}：" + "；".join(fallback))
    return bullets[:6]


def _make_interaction(
    content: dict,
    knowledge_points: list[KnowledgePoint],
    evidence_refs: list[EvidenceRef],
) -> InteractionSpec:
    profile = " ".join(
        [
            str(content.get("teaching_goal") or ""),
            str(content.get("interaction_ideas") or ""),
            " ".join(content.get("logic_flow") or []),
        ]
    )
    if _is_low_stimulation(content) or "配对" in profile:
        items: list[str] = []
        answer_groups: dict[str, list[str]] = {}
        for point in knowledge_points:
            example = point.examples[0] if point.examples else "生活中的同色物品"
            items.append(f"{point.title} -> {example}")
            answer_groups[point.title] = [example]
        return InteractionSpec(
            interaction_id="interaction_001",
            interaction_type="matching",
            title="颜色配对练习",
            prompt="请将每种颜色与一个同色物品配成一组。",
            items=items,
            answer_groups=answer_groups,
            evidence_refs=_refs_for(0, evidence_refs),
        )

    if any(keyword in profile for keyword in ("排序", "时序", "报文", "三次握手")):
        first_point = knowledge_points[0]
        items = list(first_point.key_points) or [point.title for point in knowledge_points]
        return InteractionSpec(
            interaction_id="interaction_001",
            interaction_type="ordering",
            title="连接建立顺序练习",
            prompt="请按连接建立的正确时序选择下面的步骤。",
            items=items,
            answer_groups={"正确顺序": items},
            evidence_refs=_refs_for(0, evidence_refs),
        )

    items = [point.title for point in knowledge_points]
    return InteractionSpec(
        interaction_id="interaction_001",
        interaction_type="classification",
        title="知识点分类练习",
        prompt="请将下面的知识点按本课学习路径进行归类或排序。",
        items=items,
        answer_groups={"本课核心知识点": items},
        evidence_refs=_refs_for(0, evidence_refs),
    )


# 这些值说明"信息还没定下来"，来自需求确认提示词让模型把缺失项写成"待确认"。
# 模板兜底路径会把 brief 字段直接拼进面向学生的幻灯片，所以必须先过滤掉。
_UNRESOLVED_HINTS = ("待确认", "待定", "tbd", "n/a")


def _usable_field(value: Any, *, min_length: int = 2) -> str:
    """Return a brief field only when it carries real content.

    模板路径没有模型去润色，字段原样进成品。空值、单个字符、纯数字和"待确认"这类
    未定标记都必须挡在这里，否则幻灯片上会出现"教学重点：1"这种坏数据。
    """
    text = str(value or "").strip()
    if len(text) < min_length:
        return ""
    if text.isdigit():
        return ""
    lowered = text.lower()
    if any(hint in lowered for hint in _UNRESOLVED_HINTS):
        return ""
    return text


def compile_plan_content(
    brief: TeachingBrief,
    evidence_refs: list[EvidenceRef],
) -> CoursewarePlanSpec:
    content = normalize_content(brief.content_json)
    knowledge_points = [KnowledgePoint.model_validate(item) for item in content["knowledge_points"]]
    if not knowledge_points:
        knowledge_points = [
            KnowledgePoint(order=1, title=content["teaching_goal"] or "核心概念")
        ]
    logic_flow = content["logic_flow"] or ["导入", "讲解", "练习", "总结"]
    durations = _allocate_durations(content["duration_minutes"], len(logic_flow))
    title = content["teaching_goal"] or "未命名教学课程"

    target_count = _target_slide_count(content)
    low_stimulation = _is_low_stimulation(content)
    slides: list[SlideSpec] = []

    def add_slide(
        slide_title: str,
        purpose: str,
        bullets: list[str],
        notes: str,
        layout: str,
    ) -> None:
        order = len(slides) + 1
        slides.append(
            SlideSpec(
                slide_id=f"slide_{order:03d}",
                order=order,
                title=slide_title,
                purpose=purpose,
                layout=layout,
                bullets=[item for item in bullets if item],
                speaker_notes=notes,
                evidence_refs=_refs_for(order - 1, evidence_refs),
            )
        )

    # 授课对象缺失或被标成"待确认"时宁可不写这一条，也不能把占位符投影给学生
    audience = _usable_field(content["target_audience"])
    cover_bullets = [f"课程时长：{content['duration_minutes']} 分钟", "学习目标：" + title]
    if audience:
        cover_bullets.insert(0, f"授课对象：{audience}")
    add_slide(
        title,
        "建立课程主题和学习预期",
        cover_bullets,
        "开场说明课程目标，并邀请学生联系已有经验。",
        "cover",
    )
    add_slide(
        "学习路径",
        "展示课程逻辑顺序",
        [f"{index + 1}. {step}" for index, step in enumerate(logic_flow)],
        "说明每个阶段的任务和衔接关系。",
        "agenda",
    )

    # 重点／难点为空、纯数字或"待确认"时退回通用表述，避免"教学重点：1"进成品
    focus = _usable_field(content["teaching_focus"]) or "抓住核心概念之间的关系"
    difficulty = _usable_field(content["teaching_difficulties"]) or "把概念应用到新情境"

    if not low_stimulation:
        add_slide(
            "先建立整体理解，再拆解关键步骤",
            "概览课程核心概念",
            [
                "核心知识点：" + "、".join(point.title for point in knowledge_points),
                "教学重点：" + focus,
                "学习难点：" + difficulty,
            ],
            "先让学生看到整体结构，再逐页展开关键知识点。",
            "overview",
        )

    reserved_tail = 3 if low_stimulation else 2
    max_point_groups = max(1, target_count - len(slides) - reserved_tail)
    for group in _group_points(knowledge_points, max_point_groups):
        if len(group) == 1:
            point_title = group[0].title
            notes = f"围绕“{point_title}”讲解，先检查学生理解，再进入练习。"
        else:
            point_title = "核心知识点：" + "、".join(point.title for point in group)
            notes = "将相关知识点放在一起比较，帮助学生建立概念之间的联系。"
        add_slide(
            point_title,
            "讲解核心知识点并连接课堂练习",
            _point_bullets(group),
            notes,
            "knowledge",
        )

    interaction = _make_interaction(content, knowledge_points, evidence_refs)
    if low_stimulation:
        optional_slides = [
            (
                "生活中的颜色",
                [
                    "从熟悉物品开始观察颜色",
                    "一次只比较一种颜色差异",
                    "把颜色卡片与物品配对",
                ],
                "把颜色认知迁移到熟悉的生活物品中，减少无关刺激。",
                "application",
            )
        ]
    else:
        optional_slides = [
            (
                "课堂示例：从问题走向结论",
                [
                    "先描述观察到的现象",
                    "再按学习路径解释关键步骤",
                    "最后用一个新情境检查迁移",
                ],
                "用一个完整例子串联知识点，避免只记住零散术语。",
                "example",
            ),
            (
                "常见误区与纠正",
                [
                    "容易混淆：" + difficulty,
                    "纠正方法：先说顺序，再说明每一步的作用",
                ],
                "请学生先指出容易混淆的地方，再用自己的话解释纠正方法。",
                "misconception",
            ),
            (
                "流程演练",
                [
                    "按“" + " → ".join(logic_flow) + "”复述本课流程",
                    "在每个阶段说出一个关键动作",
                ],
                "让学生沿着课程路径复述关键动作，为互动练习做准备。",
                "process",
            ),
        ]

    optional_count = max(0, target_count - len(slides) - 2)
    for slide_title, bullets, notes, layout in optional_slides[:optional_count]:
        add_slide(slide_title, "将知识连接到课堂活动", bullets, notes, layout)

    add_slide(
        interaction.title,
        "通过互动练习检查理解",
        [interaction.prompt, "操作项目：" + "；".join(interaction.items)],
        "观察学生的操作顺序或配对理由，并针对错误给予即时提示。",
        "interaction",
    )
    add_slide(
        "回顾与迁移",
        "检查学习结果并布置迁移任务",
        [
            "回顾本课关键知识点",
            "用一个新情境解释所学内容",
            "完成互动练习并说明理由",
        ],
        "请学生用自己的话复述关键概念，并记录仍需澄清的问题。",
        "summary",
    )

    # Keep the budget exact if a future brief contains an unusual number of points.
    if len(slides) > target_count:
        slides = slides[: target_count - 1] + [slides[-1]]
    while len(slides) < target_count:
        add_slide(
            "课堂小结",
            "巩固关键理解",
            ["用一句话说出本课最重要的内容", "举出一个与生活或专业相关的例子"],
            "请学生用自己的语言完成最后一次复述。",
            "summary",
        )
    for order, slide in enumerate(slides, start=1):
        slide.order = order
        slide.slide_id = f"slide_{order:03d}"

    sections: list[LessonPlanSectionSpec] = []
    for index, (stage, duration) in enumerate(zip(logic_flow, durations), start=1):
        point = knowledge_points[(index - 1) % len(knowledge_points)]
        sections.append(
            LessonPlanSectionSpec(
                section_id=f"section_{index:03d}",
                order=index,
                title=stage,
                duration_minutes=duration,
                objective=f"围绕“{point.title}”完成{stage}阶段目标",
                teacher_actions=[f"教师引导学生进入{stage}，聚焦{point.title}"],
                student_actions=["学生完成观察、表达或练习，并记录疑问"],
                assessment=f"学生能够用自己的话说明{point.title}",
                evidence_refs=_refs_for(index - 1, evidence_refs),
            )
        )

    return CoursewarePlanSpec(
        title=title,
        target_audience=content["target_audience"],
        duration_minutes=content["duration_minutes"],
        teaching_goal=title,
        knowledge_points=knowledge_points,
        logic_flow=logic_flow,
        teaching_focus=content["teaching_focus"],
        teaching_difficulties=content["teaching_difficulties"],
        style_preference=content["style_preference"],
        slides=slides,
        lesson_sections=sections,
        interactions=[interaction],
        evidence_refs=evidence_refs,
        output_specs=OutputContentSpecs(
            pptx=PptxContentSpec(
                narrative_arc=logic_flow,
                visual_direction=content["style_preference"] or "清晰、克制、便于课堂投影",
                max_bullets_per_slide=5,
            ),
            docx=DocxContentSpec(
                teacher_preparation=[
                    "检查课件、互动练习和课堂展示设备",
                    "准备与核心知识点对应的示例或材料",
                ],
                differentiation=[
                    "为基础薄弱学生提供步骤提示",
                    "为进阶学生增加迁移解释任务",
                ],
                homework=content["homework_type"] or "选择一个新情境解释本课核心知识点。",
                reflection_prompts=[
                    "哪些环节最能暴露学生的真实理解？",
                    "下次教学需要调整哪一项活动或时间分配？",
                ],
            ),
            pdf=PdfContentSpec(
                printable_summary=f"围绕“{title}”完成学习、练习与迁移。",
                assessment_checklist=[
                    f"能够说明：{point.title}" for point in knowledge_points
                ],
            ),
            html=HtmlContentSpec(
                interaction_ids=[interaction.interaction_id],
                accessibility_notes=["支持键盘操作", "反馈不只依赖颜色表达"],
            ),
        ),
        generation_notes=[
            "蓝图基于已确认 TeachingBrief 编译生成。",
            "每个成果都消费同一份 SlideSpec、LessonPlanSectionSpec 和 InteractionSpec。",
        ],
    )


def revise_courseware_plan(
    db: DBSession,
    project: Project,
    base: CoursewarePlan,
    requested: CoursewarePlanSpec,
    *,
    summary: str,
) -> CoursewarePlan:
    """Create an immutable manual plan revision while preserving trusted identities and refs."""
    latest = get_latest_plan(db, project.project_id)
    if latest is None or latest.plan_id != base.plan_id:
        raise ApiError(
            "教学蓝图已发生变化，请刷新后再保存",
            code="PLAN_VERSION_CONFLICT",
            status_code=409,
        )

    base_data = CoursewarePlanSpec.model_validate(base.plan_json or {}).model_dump(mode="json")
    candidate = requested.model_dump(mode="json")
    for field in (
        "target_audience",
        "duration_minutes",
        "teaching_goal",
        "knowledge_points",
        "logic_flow",
        "teaching_focus",
        "teaching_difficulties",
        "evidence_refs",
    ):
        candidate[field] = base_data[field]

    for collection, id_field in (
        ("slides", "slide_id"),
        ("lesson_sections", "section_id"),
        ("interactions", "interaction_id"),
    ):
        base_items = base_data[collection]
        requested_by_id = {item[id_field]: item for item in candidate[collection]}
        if set(requested_by_id) != {item[id_field] for item in base_items}:
            raise ApiError(
                "当前编辑只能修改已有蓝图条目",
                code="PLAN_STRUCTURE_CHANGE_NOT_ALLOWED",
                status_code=422,
                details={"collection": collection},
            )
        merged_items = []
        for base_item in base_items:
            item = requested_by_id[base_item[id_field]]
            item[id_field] = base_item[id_field]
            item["order"] = base_item.get("order", item.get("order"))
            item["evidence_refs"] = base_item.get("evidence_refs", [])
            if collection == "interactions":
                item["interaction_type"] = base_item["interaction_type"]
            merged_items.append(item)
        candidate[collection] = merged_items

    revised_spec = CoursewarePlanSpec.model_validate(candidate)
    section_total = sum(item.duration_minutes for item in revised_spec.lesson_sections)
    if section_total != revised_spec.duration_minutes:
        raise ApiError(
            "教学流程时长总和必须等于课程总时长",
            code="PLAN_DURATION_MISMATCH",
            status_code=422,
            details={
                "expected_minutes": revised_spec.duration_minutes,
                "actual_minutes": section_total,
            },
        )
    require_courseware_quality(revised_spec)
    with project_version_lock(db, project.project_id):
        version = (
            db.query(func.max(CoursewarePlan.version))
            .filter(CoursewarePlan.project_id == project.project_id)
            .scalar()
            or 0
        ) + 1
        now = datetime.now(timezone.utc)
        plan = CoursewarePlan(
            plan_id=gen_id("plan"),
            user_id=project.owner_id,
            project_id=project.project_id,
            brief_id=base.brief_id,
            version=version,
            status="ready",
            title=revised_spec.title,
            duration_minutes=revised_spec.duration_minutes,
            plan_json=revised_spec.model_dump(mode="json"),
            source_refs=base.source_refs or [],
            generation_mode="manual",
            model_name=None,
            prompt_version="manual-plan-v1",
            usage_json={},
            notes=summary.strip() or "教师编辑教学蓝图",
            created_at=now,
            updated_at=now,
        )
        db.add(plan)
        db.flush()
    return plan


def _ignore_stage(_stage: str) -> None:
    """Default progress sink for callers that do not stream."""
    return None


def build_courseware_plan(
    db: DBSession,
    project: Project,
    *,
    rag_docs: Iterable[RAGDocument] = (),
    force_rebuild: bool = False,
    generation_mode: str = "ai",
    allow_template_fallback: bool = False,
    on_stage: Callable[[str], None] | None = None,
) -> CoursewarePlan:
    """Build and persist a teaching blueprint.

    `on_stage` receives coarse phase names so a streaming caller can render live
    progress. The persisted plan is identical whether or not it is supplied, so a
    synchronous caller keeps the exact previous behaviour.
    """
    notify_stage = on_stage if on_stage is not None else _ignore_stage
    notify_stage("brief")

    brief = get_confirmed_brief(db, project_id=project.project_id)
    if brief is None:
        raise ApiError(
            "请先确认 TeachingBrief 再生成教学蓝图",
            code="BRIEF_NOT_CONFIRMED",
            status_code=409,
            suggested_action="补充需求确认单并点击确认后再试",
        )
    latest = get_latest_plan(db, project.project_id)
    if (
        latest is not None
        and latest.brief_id == brief.brief_id
        and latest.generation_mode == generation_mode
        and not force_rebuild
    ):
        notify_stage("reused")
        return latest

    notify_stage("evidence")
    refs = _build_evidence_refs(db, project.project_id, rag_docs)
    model_name = None
    prompt_version = None
    usage: dict = {}
    notes = ""
    resolved_mode = generation_mode
    if generation_mode == "template":
        notify_stage("template")
        content = compile_plan_content(brief, refs)
        notes = "教师明确选择基础模板生成。"
    elif generation_mode == "ai":
        try:
            ai_result = generate_courseware_spec(
                brief.content_json or {},
                refs,
                # The teacher's uploaded pictures, described by the vision pass.
                # This is both the model's menu and the allowlist for slide pictures.
                available_images=list_project_images(db, project.project_id),
                on_stage=notify_stage,
            )
            content = ai_result.spec
            model_name = ai_result.model_name
            prompt_version = ai_result.prompt_version
            usage = ai_result.usage
        except CoursewareAIError as exc:
            if not allow_template_fallback:
                raise ApiError(
                    str(exc),
                    code=exc.code,
                    status_code=503,
                    recoverable=exc.recoverable,
                    suggested_action="重试 AI 生成，或明确选择使用基础模板",
                ) from exc
            resolved_mode = "template"
            content = compile_plan_content(brief, refs)
            content.generation_notes.append(f"AI 生成失败后使用基础模板：{exc.code}")
            notes = f"AI 生成失败后由教师允许降级：{exc.code}"
    else:
        raise ApiError(
            "不支持的蓝图生成模式",
            code="PLAN_GENERATION_MODE_INVALID",
            status_code=422,
        )
    notify_stage("persist")
    with project_version_lock(db, project.project_id):
        version = (
            db.query(func.max(CoursewarePlan.version))
            .filter(CoursewarePlan.project_id == project.project_id)
            .scalar()
            or 0
        ) + 1
        now = datetime.now(timezone.utc)
        plan = CoursewarePlan(
            plan_id=gen_id("plan"),
            user_id=project.owner_id,
            project_id=project.project_id,
            brief_id=brief.brief_id,
            version=version,
            status="ready",
            title=content.title,
            duration_minutes=content.duration_minutes,
            plan_json=content.model_dump(mode="json"),
            source_refs=[item.model_dump(mode="json") for item in refs],
            generation_mode=resolved_mode,
            model_name=model_name,
            prompt_version=prompt_version,
            usage_json=usage,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        db.add(plan)
        db.flush()
    return plan


def to_info(plan: CoursewarePlan) -> CoursewarePlanInfo:
    try:
        content = CoursewarePlanSpec.model_validate(plan.plan_json or {})
        refs = [EvidenceRef.model_validate(item) for item in (plan.source_refs or [])]
    except Exception as exc:
        raise ApiError(
            "教学蓝图数据无法读取，可能由历史数据或版本升级导致",
            code="PLAN_SNAPSHOT_INVALID",
            status_code=500,
            recoverable=False,
            suggested_action="请重新生成教学蓝图",
        ) from exc
    return CoursewarePlanInfo(
        plan_id=plan.plan_id,
        user_id=plan.user_id,
        project_id=plan.project_id,
        brief_id=plan.brief_id,
        version=plan.version,
        status=plan.status,
        title=plan.title,
        duration_minutes=plan.duration_minutes,
        content=content,
        source_refs=refs,
        generation_mode=plan.generation_mode or "template",
        model_name=plan.model_name,
        prompt_version=plan.prompt_version,
        usage=plan.usage_json or {},
        notes=plan.notes or "",
        created_at=plan.created_at,
        updated_at=plan.updated_at or plan.created_at,
    )
