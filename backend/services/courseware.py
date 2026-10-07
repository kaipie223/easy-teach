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
from backend.config import settings
from backend.services.brief import get_confirmed_brief, normalize_content
from backend.services.courseware_ai import (
    CoursewareAIError,
    generate_courseware_spec,
    generate_courseware_spec_agentic,
)
from backend.services.materials import list_project_images
from backend.services.quality import forbidden_terms, require_courseware_quality
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


# brief 里的难度标注 → 分类题的分组名。认不出的值原样使用，不替教师归类。
_DIFFICULTY_LABELS = {
    "basic": "基础理解",
    "easy": "基础理解",
    "intermediate": "理解与应用",
    "medium": "理解与应用",
    "advanced": "综合迁移",
    "hard": "综合迁移",
}


def _difficulty_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "本课核心知识点"
    return _DIFFICULTY_LABELS.get(text.lower(), text)


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
            title="配对练习",
            prompt="请将左侧内容与右侧对应的例子配成一组。",
            items=items,
            answer_groups=answer_groups,
            explanation=(
                "配对依据来自本课知识点的示例：先确认每个知识点的含义，"
                "再判断哪个例子能作为它的具体体现；含义说不清时不要靠猜。"
            ),
            evidence_refs=_refs_for(0, evidence_refs),
        )

    if any(keyword in profile for keyword in ("排序", "时序", "报文", "三次握手")):
        first_point = knowledge_points[0]
        items = list(first_point.key_points) or [point.title for point in knowledge_points]
        return InteractionSpec(
            interaction_id="interaction_001",
            interaction_type="ordering",
            title=f"{first_point.title}顺序练习",
            prompt=f"请按正确顺序排列“{first_point.title}”的下列步骤。",
            items=items,
            answer_groups={"正确顺序": items},
            explanation=(
                "顺序类题目只有在前后的先后关系客观确定时才有唯一答案：排列时先判断"
                "每一步是谁在做、依赖哪一步的结果，再回头检查整条链条有没有断点。"
            ),
            evidence_refs=_refs_for(0, evidence_refs),
        )

    # 分类题按 brief 已有的难度标注分组：不编造内容，但比"全部归为一组"更有区分度。
    groups: dict[str, list[str]] = {}
    for point in knowledge_points:
        groups.setdefault(_difficulty_label(point.difficulty), []).append(point.title)
    if len(groups) < 2:
        # 需求单没有区分难度时保持单组：分组是为学生服务的，硬拆只会制造没有依据的答案。
        groups = {"本课核心知识点": [point.title for point in knowledge_points]}
    labels = "、".join(groups)
    items = [point.title for point in knowledge_points]
    return InteractionSpec(
        interaction_id="interaction_001",
        interaction_type="classification",
        title="知识点归类练习",
        # 题干必须列出全部分组标签：只说"归类"而答案里有两组，学生无从作答。
        prompt=f"请把下面的知识点归入对应的一组，可选分组：{labels}。",
        items=items,
        answer_groups=groups,
        explanation=(
            "分组依据来自本课对每个知识点的难度定位："
            + "；".join(f"「{label}」含 {len(values)} 项" for label, values in groups.items())
            + "。判断时先回想该知识点在课上属于「先建立理解」还是「后续综合运用」，再确定归组。"
        ),
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


# ── 模板兜底路径的内容生成 ──────────────────────────────
#
# 模板是纯后端编译：AI 不可用、或教师明确选择"基础模板"时走它。它以前只把 brief
# 字段拼成短句（讲稿 20 余字、每个环节师生活动各 1 条、打印要点一句话），质量检查
# 会如实报"内容单薄"。
#
# 这里把它补到"教师能直接照着上"的程度。原则不变：**不编造学科内容**，只用 brief
# 里已经有的信息组织出讲解思路、关键提问、预设回答、典型错误与评价判准。


def _point_titles(points: list[KnowledgePoint], limit: int = 5) -> list[str]:
    return [point.title for point in points if str(point.title).strip()][:limit]


def _titles_text(points: list[KnowledgePoint], limit: int = 3) -> str:
    titles = _point_titles(points, limit)
    return "、".join(titles) if titles else "本课核心内容"


def _point_details(point: KnowledgePoint) -> str:
    """一个知识点的要点摘要；缺要点时给可执行的观察任务，而不是编造内容。"""
    details = [str(item).strip() for item in point.key_points if str(item).strip()]
    if point.examples:
        examples = "；".join(str(item).strip() for item in point.examples if str(item).strip())
        if examples:
            details.append(f"示例：{examples}")
    return "；".join(details)


def _slide_notes(
    *,
    slide_title: str,
    purpose: str,
    bullets: list[str],
    focus: str,
    difficulty: str,
    point: KnowledgePoint | None = None,
) -> str:
    """按"讲解思路 → 关键提问 → 预设回答 → 易错提醒"写讲稿。

    speaker_notes 是教师真正照着讲的东西，一句话的"讲稿"等于没有。
    """
    detail = _point_details(point) if point is not None else ""
    if detail:
        approach = f"先请学生观察或回忆与“{point.title}”有关的经验，再逐步引出{detail}。"
    else:
        digest = "；".join(str(item) for item in bullets if str(item))[:120]
        approach = f"先请学生观察或回忆与“{slide_title}”有关的经验，再逐步展开{digest}。"
    return "".join(
        [
            f"本页目标是{purpose}。",
            approach,
            f"关键提问：“{slide_title}”最关键的一点是什么？请说明你的判断依据。",
            "预设回答：学生很可能只给结论而忽略条件——需要追问“这个结论在什么条件下成立”，"
            "并把条件写到板书上；能说出条件的回答才算到位。",
            f"本页重点落在{focus}，学生最容易卡在{difficulty}；"
            "发现卡顿先让他们复述已知条件，再给下一步提示，不要直接把答案说出来。",
            "收尾用一句话把本页与下一页衔接起来，并留出学生提问的时间。",
        ]
    )


def _section_objective(stage: str, targets: str) -> str:
    return (
        f"学生能在“{stage}”环节结束时，用自己的话说明{targets}，并写出至少一步判断依据，"
        "达到“能独立复述且理由正确”的标准"
    )


def _section_teacher_actions(
    stage: str, minutes: int, targets: str, focus: str
) -> list[str]:
    first = max(1, minutes // 3)
    second = max(first + 1, minutes * 2 // 3)
    return [
        f"0-{first} 分钟：用与“{focus}”有关的问题导入{stage}，提问原话：“关于{focus}，"
        "你已经知道什么？还有哪里说不清？”请 2 名学生回答并板书记录。",
        f"{first}-{second} 分钟：示范或引导学生梳理{targets}，把关键步骤与适用条件写在"
        "板书上；每写一步停下来请学生复述一次，确认跟得上再继续。",
        f"{second}-{minutes} 分钟：巡视并针对典型错误个别点拨，最后用一句话说明本环节与"
        "下一环节的衔接，并留出提问时间。",
    ]


def _section_student_actions(minutes: int, targets: str) -> list[str]:
    return [
        f"独立完成本环节的观察或练习任务，并在记录单上写下与{targets}有关的判断依据"
        "（只写结论不算完成）。",
        "与同桌互相检查答案与理由，指出对方缺失的条件或步骤，并各自修改一处。",
        f"用一句话向全班汇报本组结论并回答教师追问；汇报与互评合计约 "
        f"{max(1, minutes // 5)} 分钟。",
    ]


def _section_assessment(targets: str, difficulty: str) -> str:
    return (
        f"判准：能说出{targets}并给出依据，表述完整、无自相矛盾即达标；"
        f"典型错误：把{difficulty}与表面现象混为一谈，或只给结论不给条件——"
        "出现时回到题干条件重新提问，让学生自己补全理由后再记录。"
    )


def _printable_summary(
    *,
    points: list[KnowledgePoint],
    focus: str,
    difficulty: str,
    title: str,
) -> str:
    """一页可打印的学习要点：关键术语 → 方法及适用条件 → 易错点 → 代表题与思路。"""
    term_lines = []
    for index, point in enumerate(points[:5], start=1):
        detail = _point_details(point) or f"本课围绕“{point.title}”展开，需要能说明它的含义与用法"
        term_lines.append(f"{index}. {point.title}：{detail}")
    if not term_lines:
        term_lines.append(f"1. {title}：本课的核心概念与用法")

    first_title = _point_titles(points, 1)
    representative = first_title[0] if first_title else title
    return "\n".join(
        [
            "一、关键术语",
            *term_lines,
            "",
            "二、核心方法与适用条件",
            f"本课的核心方法是围绕“{focus}”展开的判断与说明。使用它之前必须先明确题干"
            "给出的条件；条件一旦变化，结论可能随之改变，不能把在特定条件下成立的说法"
            f"当成普遍规律。需要特别留意的是{difficulty}。",
            "",
            "三、典型易错点",
            f"1. 只记结论、忽略它成立的条件；",
            f"2. 把{difficulty}与表面现象混为一谈；",
            "3. 表述中使用“总是”“一定”这类绝对化说法，超出材料能支持的范围。",
            "",
            "四、代表题与思路提示",
            f"题目：结合本课内容，说明“{representative}”在给定条件下会如何变化，并写出判断依据。",
            "思路提示：先抄下题目给出的条件 → 定位到对应的概念或方法 → 写出推理步骤 → "
            "最后回头检查结论有没有超出题目给定的条件。",
        ]
    )


def _assessment_checklist(
    points: list[KnowledgePoint], focus: str, difficulty: str
) -> list[str]:
    """每条都能用"是/否"勾选判定，并对应本课目标。"""
    checklist = [
        f"能说出“{title}”的含义并给出依据" for title in _point_titles(points)
    ]
    checklist.extend(
        [
            f"能说明“{focus}”的适用条件，并指出条件变化时结论会怎样改变",
            f"能指出{difficulty}这一类典型错误，并说出纠正方法",
            "能在新情境中迁移本课方法，并写出判断依据",
        ]
    )
    return list(dict.fromkeys(checklist))


def _homework_text(content: dict, focus: str, difficulty: str) -> str:
    base = _usable_field(content["homework_type"], min_length=4)
    if base:
        return (
            f"{base}。完成标准：写清判断依据与适用条件，并指出一种可能出错的情况；"
            "提交形式：下节课前交纸质或电子稿。"
        )
    scenario = _usable_field(content["scenario_extensions"]) or _usable_field(
        content["case_preference"]
    )
    target = scenario or f"与“{focus}”有关的日常情境"
    return (
        f"选择一个新情境（例如{target}），说明“{focus}”在其中如何体现。"
        f"完成标准：写出判断依据与适用条件，并说明哪里容易与{difficulty}混淆；"
        "提交形式：下节课前交纸质或电子稿。"
    )


def _teacher_preparation(content: dict, focus: str) -> list[str]:
    """教具学具要贴住本课重点，教具之外还要显式回应教师提出的约束。"""
    preparation = [
        "检查课件、互动练习与课堂展示设备（投影、音频、计时器）",
        f"准备与“{focus}”对应的示例、材料或实验器材，并按小组数打印学习任务单",
        "准备分层任务卡：一份含步骤提示，一份含开放迁移任务",
    ]
    extra = _usable_field(content["extra_requirements"], min_length=4)
    if extra:
        preparation.append(f"按需求约定落实：{extra}")
    forbidden = forbidden_terms(content.get("forbidden_content"))
    if forbidden:
        preparation.append("本课不涉及（按需求约定）：" + "、".join(forbidden))
    return preparation


def _differentiation(content: dict, focus: str) -> list[str]:
    existing = _usable_field(content["existing_knowledge"], min_length=4)
    base = (
        f"起点判断：学生已有的基础是{existing}，课堂提问从这里往上搭，"
        "不重复他们已经会的内容。"
        if existing
        else "起点判断：先用一道口答题确认学生是否具备本课所需的前置概念。"
    )
    return [
        base,
        f"需要帮扶：把“{focus}”拆成三步，给出步骤提示卡，允许先照提示完成一遍再独立重做。",
        "学有余力：追加一道变式任务，要求说明结论成立的条件，并解释为什么条件不能去掉。",
        "课堂巡视时优先检查帮扶组的记录单，发现卡顿先让他们复述已知条件。",
    ]


def _reflection_prompts(focus: str, difficulty: str) -> list[str]:
    return [
        f"哪些学生在说明“{focus}”时只给了结论、没有给出条件？下次怎样提前铺垫？",
        f"“{difficulty}”这个难点在本节课被突破了吗？依据是哪几个学生的哪句话？",
        "哪一个环节的时间分配与实际不符？下次要压缩还是加长，具体到几分钟？",
    ]


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
    # 重点／难点为空、纯数字或"待确认"时退回通用表述，避免"教学重点：1"进成品。
    # 它们在封面之前就要算出来：每一页的讲稿都会引用它们。
    focus = _usable_field(content["teaching_focus"]) or "抓住核心概念之间的关系"
    difficulty = _usable_field(content["teaching_difficulties"]) or "把概念应用到新情境"
    targets = _titles_text(knowledge_points)

    cover_purpose = "建立课程主题和学习预期"
    cover_bullets = [f"课程时长：{content['duration_minutes']} 分钟", "学习目标：" + title]
    if audience:
        cover_bullets.insert(0, f"授课对象：{audience}")
    add_slide(
        title,
        cover_purpose,
        cover_bullets,
        _slide_notes(
            slide_title=title,
            purpose=cover_purpose,
            bullets=cover_bullets,
            focus=focus,
            difficulty=difficulty,
        ),
        "cover",
    )
    agenda_purpose = "展示课程逻辑顺序"
    add_slide(
        "学习路径",
        agenda_purpose,
        [f"{index + 1}. {step}" for index, step in enumerate(logic_flow)],
        _slide_notes(
            slide_title="学习路径",
            purpose=agenda_purpose,
            bullets=list(logic_flow),
            focus=focus,
            difficulty=difficulty,
        ),
        "agenda",
    )

    if not low_stimulation:
        overview_title = "先建立整体理解，再拆解关键步骤"
        overview_purpose = "概览课程核心概念"
        overview_bullets = [
            "核心知识点：" + targets,
            "教学重点：" + focus,
            "学习难点：" + difficulty,
        ]
        add_slide(
            overview_title,
            overview_purpose,
            overview_bullets,
            _slide_notes(
                slide_title=overview_title,
                purpose=overview_purpose,
                bullets=overview_bullets,
                focus=focus,
                difficulty=difficulty,
            ),
            "overview",
        )

    reserved_tail = 3 if low_stimulation else 2
    max_point_groups = max(1, target_count - len(slides) - reserved_tail)
    for group in _group_points(knowledge_points, max_point_groups):
        if len(group) == 1:
            point_title = group[0].title
            slide_point: KnowledgePoint | None = group[0]
        else:
            point_title = "核心知识点：" + "、".join(point.title for point in group)
            slide_point = None
        point_purpose = "讲解核心知识点并连接课堂练习"
        point_bullets = _point_bullets(group)
        add_slide(
            point_title,
            point_purpose,
            point_bullets,
            _slide_notes(
                slide_title=point_title,
                purpose=point_purpose,
                bullets=point_bullets,
                focus=focus,
                difficulty=difficulty,
                point=slide_point,
            ),
            "knowledge",
        )

    interaction = _make_interaction(content, knowledge_points, evidence_refs)
    # 教师给的案例与情境要真的进课堂，而不是只躺在需求单里
    case_hint = _usable_field(content["case_preference"])
    scenario = _usable_field(content["scenario_extensions"])
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
                    "案例：" + (case_hint or "本课知识点对应的典型情境"),
                    "先描述观察到的现象",
                    "再按学习路径解释关键步骤",
                    "最后用一个新情境检查迁移",
                ],
                "",
                "example",
            ),
            (
                "常见误区与纠正",
                [
                    "容易混淆：" + difficulty,
                    "纠正方法：先回到题干条件，再逐步说明每一步的作用",
                    "自查方法：写完结论后回头确认条件是否被改变",
                ],
                "",
                "misconception",
            ),
            (
                "流程演练",
                [
                    "按“" + " → ".join(logic_flow) + "”复述本课流程",
                    "在每个阶段说出一个关键动作",
                    "指出每一步最容易出错的地方",
                ],
                "",
                "process",
            ),
        ]

    optional_count = max(0, target_count - len(slides) - 2)
    for slide_title, bullets, notes, layout in optional_slides[:optional_count]:
        purpose = "将知识连接到课堂活动"
        # 版式自带的提示语是"补充"，不能顶替整段讲稿：以前直接把它当 speaker_notes，
        # 于是一页讲稿只剩 24 字（就是"把颜色认知迁移到生活物品中"那一句）。
        generated = _slide_notes(
            slide_title=slide_title,
            purpose=purpose,
            bullets=bullets,
            focus=focus,
            difficulty=difficulty,
        )
        add_slide(
            slide_title,
            purpose,
            bullets,
            f"{generated}{notes}" if notes else generated,
            layout,
        )

    interaction_purpose = "通过互动练习检查理解"
    add_slide(
        interaction.title,
        interaction_purpose,
        [interaction.prompt, "操作项目：" + "；".join(interaction.items)],
        _slide_notes(
            slide_title=interaction.title,
            purpose=interaction_purpose,
            bullets=[interaction.prompt, *interaction.items],
            focus=focus,
            difficulty=difficulty,
        )
        + "本页留出独立作答时间，先不公布答案；收齐后再请学生说明理由，"
        "对照典型错误逐条点评。",
        "interaction",
    )
    summary_purpose = "检查学习结果并布置迁移任务"
    summary_bullets = [
        f"回顾本课关键知识点：{targets}",
        f"用一个新情境解释“{focus}”并说明成立条件",
        "完成互动练习并说明理由",
    ]
    if scenario:
        summary_bullets.insert(1, f"迁移情境：{scenario}")
    add_slide(
        "回顾与迁移",
        summary_purpose,
        summary_bullets,
        _slide_notes(
            slide_title="回顾与迁移",
            purpose=summary_purpose,
            bullets=summary_bullets,
            focus=focus,
            difficulty=difficulty,
        ),
        "summary",
    )

    # Keep the budget exact if a future brief contains an unusual number of points.
    if len(slides) > target_count:
        slides = slides[: target_count - 1] + [slides[-1]]
    while len(slides) < target_count:
        filler_title = "课堂小结"
        filler_purpose = "巩固关键理解"
        filler_bullets = [
            f"用一句话说出本课最重要的内容（围绕{targets}）",
            "举出一个与生活或专业相关的例子",
        ]
        add_slide(
            filler_title,
            filler_purpose,
            filler_bullets,
            _slide_notes(
                slide_title=filler_title,
                purpose=filler_purpose,
                bullets=filler_bullets,
                focus=focus,
                difficulty=difficulty,
            ),
            "summary",
        )
    for order, slide in enumerate(slides, start=1):
        slide.order = order
        slide.slide_id = f"slide_{order:03d}"

    sections: list[LessonPlanSectionSpec] = []
    for index, (stage, duration) in enumerate(zip(logic_flow, durations), start=1):
        point = knowledge_points[(index - 1) % len(knowledge_points)]
        section_targets = point.title or targets
        sections.append(
            LessonPlanSectionSpec(
                section_id=f"section_{index:03d}",
                order=index,
                title=stage,
                duration_minutes=duration,
                objective=_section_objective(stage, section_targets),
                teacher_actions=_section_teacher_actions(
                    stage, duration, section_targets, focus
                ),
                student_actions=_section_student_actions(duration, section_targets),
                assessment=_section_assessment(section_targets, difficulty),
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
                teacher_preparation=_teacher_preparation(content, focus),
                differentiation=_differentiation(content, focus),
                homework=_homework_text(content, focus, difficulty),
                reflection_prompts=_reflection_prompts(focus, difficulty),
            ),
            pdf=PdfContentSpec(
                printable_summary=_printable_summary(
                    points=knowledge_points,
                    focus=focus,
                    difficulty=difficulty,
                    title=title,
                ),
                assessment_checklist=_assessment_checklist(
                    knowledge_points, focus, difficulty
                ),
            ),
            html=HtmlContentSpec(
                interaction_ids=[interaction.interaction_id],
                completion_message=(
                    "练习完成。请对照解析回顾本课的判断依据，"
                    f"并说明“{focus}”成立的条件。"
                ),
                accessibility_notes=[
                    "支持键盘操作",
                    "反馈不只依赖颜色表达",
                    "题干与选项可被读屏软件朗读",
                ],
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
    # 带上需求单：只有它知道教师要求过"不要出现什么"。手工改蓝图的路径同样要核对，
    # 否则教师改完保存反而绕过了要求检查。
    brief = get_confirmed_brief(db, project_id=project.project_id)
    require_courseware_quality(
        revised_spec,
        brief_content=brief.content_json if brief is not None else None,
    )
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
    deep_thinking: bool | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> CoursewarePlan:
    """Build and persist a teaching blueprint.

    `on_stage` receives coarse phase names so a streaming caller can render live
    progress. The persisted plan is identical whether or not it is supplied, so a
    synchronous caller keeps the exact previous behaviour.

    `deep_thinking` 是教师在界面上勾的"深度思考"开关：True/False 为明确指定，
    None 表示按服务端配置。它只影响 AI 生成，模板模式忽略。
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
        # 明确勾了"深度思考"就不再复用：教师勾这个开关要的是"重新认真生成一遍"，
        # 直接返回缓存会让他以为开关没生效。
        and not deep_thinking
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
            # 流水线模式：先搭骨架再分段填充。一次调用塞不下整册内容，模型只能把
            # 每条都写短（教案四项与 PDF 学习要点尤其明显），所以默认走分段生成。
            generator = (
                generate_courseware_spec_agentic
                if settings.courseware_pipeline == "agentic"
                else generate_courseware_spec
            )
            ai_result = generator(
                brief.content_json or {},
                refs,
                # The teacher's uploaded pictures, described by the vision pass.
                # This is both the model's menu and the allowlist for slide pictures.
                available_images=list_project_images(db, project.project_id),
                on_stage=notify_stage,
                thinking=deep_thinking,
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
