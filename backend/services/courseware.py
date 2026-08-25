"""M4 courseware-plan compiler and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.models.brief import TeachingBrief
from backend.models.courseware import CoursewarePlan
from backend.models.knowledge import KnowledgeDocument
from backend.models.material import EvidenceChunk, Material
from backend.models.project import Project
from backend.models.session import gen_id
from backend.schemas import (
    CoursewarePlanInfo,
    CoursewarePlanSpec,
    EvidenceRef,
    InteractionSpec,
    KnowledgePoint,
    LessonPlanSectionSpec,
    RAGDocument,
    SlideSpec,
)
from backend.services.brief import get_latest_brief, normalize_content


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
    evidence_query = db.query(EvidenceChunk).filter(EvidenceChunk.is_valid.is_(True))
    if material_ids:
        evidence_query = evidence_query.filter(EvidenceChunk.material_id.in_(material_ids))
    else:
        evidence_query = evidence_query.filter(EvidenceChunk.material_id.is_(None))
    chunks = evidence_query.order_by(EvidenceChunk.created_at.asc()).limit(12).all()

    document_ids = {chunk.knowledge_document_id for chunk in chunks if chunk.knowledge_document_id}
    document_names = {
        item.document_id: item.title
        for item in db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.document_id.in_(document_ids))
        .all()
    } if document_ids else {}

    refs: list[EvidenceRef] = [
        _evidence_ref_from_chunk(
            chunk,
            material_names=materials,
            document_names=document_names,
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
    bullets: list[str] = []
    for point in points:
        details = list(point.key_points)
        if point.examples:
            details.append("示例：" + "；".join(point.examples))
        if len(points) == 1:
            bullets.extend(details or [point.title])
        else:
            bullets.append(f"{point.title}：" + "；".join(details or [point.title]))
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

    add_slide(
        title,
        "建立课程主题和学习预期",
        [
            f"授课对象：{content['target_audience']}",
            f"课程时长：{content['duration_minutes']} 分钟",
            "学习目标：" + title,
        ],
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

    if not low_stimulation:
        add_slide(
            "先建立整体理解，再拆解关键步骤",
            "概览课程核心概念",
            [
                "核心知识点：" + "、".join(point.title for point in knowledge_points),
                "教学重点：" + (content["teaching_focus"] or "抓住核心概念之间的关系"),
                "学习难点：" + (content["teaching_difficulties"] or "把概念应用到新情境"),
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
                    "容易混淆：" + (content["teaching_difficulties"] or "知识点之间的作用"),
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
        generation_notes=[
            "蓝图基于已确认 TeachingBrief 编译生成。",
            "每个成果都消费同一份 SlideSpec、LessonPlanSectionSpec 和 InteractionSpec。",
        ],
    )


def build_courseware_plan(
    db: DBSession,
    project: Project,
    *,
    rag_docs: Iterable[RAGDocument] = (),
    force_rebuild: bool = False,
) -> CoursewarePlan:
    brief = get_latest_brief(db, project_id=project.project_id)
    if brief is None or brief.status != "confirmed":
        raise ApiError(
            "请先确认 TeachingBrief 再生成教学蓝图",
            code="BRIEF_NOT_CONFIRMED",
            status_code=409,
            suggested_action="补充需求确认单并点击确认后再试",
        )
    latest = get_latest_plan(db, project.project_id)
    if latest is not None and latest.brief_id == brief.brief_id and not force_rebuild:
        return latest

    refs = _build_evidence_refs(db, project.project_id, rag_docs)
    content = compile_plan_content(brief, refs)
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
        notes="",
        created_at=now,
        updated_at=now,
    )
    db.add(plan)
    db.flush()
    return plan


def to_info(plan: CoursewarePlan) -> CoursewarePlanInfo:
    content = CoursewarePlanSpec.model_validate(plan.plan_json or {})
    refs = [EvidenceRef.model_validate(item) for item in (plan.source_refs or [])]
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
        notes=plan.notes or "",
        created_at=plan.created_at,
        updated_at=plan.updated_at or plan.created_at,
    )
