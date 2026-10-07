"""TeachingBrief merge, validation and versioning services."""

import re
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from ai.intent.state import IntentStateMachine, State
from backend.core.errors import ApiError
from backend.models.brief import TeachingBrief
from backend.models.project import Project
from backend.models.session import Session
from backend.schemas import (
    IntentResult,
    KnowledgePoint,
    TeachingBriefInfo,
    TeachingBriefUpdate,
)
from backend.services.prompt_library import _SUBJECT_ALIASES as _SUBJECT_NAMES
from backend.services.prompt_library import SUBJECT_PROFILES
from backend.services.version_allocator import project_version_lock

# 学科答案的识别词：与提示词库共用同一份学科名与别名，避免两处各写一套
_SUBJECT_HINTS = tuple(SUBJECT_PROFILES) + tuple(_SUBJECT_NAMES)

FIELD_LABELS = {
    "course_name": "课程名称",
    "subject": "学科",
    "grade": "年级／学段",
    "teaching_goal": "教学目标",
    "target_audience": "授课对象",
    "duration_minutes": "课时长度",
    "knowledge_points": "核心知识点",
    "logic_flow": "知识点逻辑顺序",
    "teaching_focus": "教学重点",
    "teaching_difficulties": "教学难点",
    "output_types": "产出类型",
}

# 核心字段的必问顺序，也是"缺什么"的唯一事实来源。
# 学段/学科放在最前面：它们决定后面整份设计的组织方式（学段适配、学科易错点），
# 也决定课时与产出的合理默认值。以前这里既没有学科/学段，也没有顺序，
# 于是"这一轮问课时、下一轮问学情"全看模型当轮心情。
CORE_FIELD_ORDER: tuple[str, ...] = (
    "grade",
    "subject",
    "target_audience",
    "duration_minutes",
    "teaching_goal",
    "knowledge_points",
    "teaching_focus",
    "teaching_difficulties",
    "output_types",
    "logic_flow",
)

# 必须由教师亲自确认的核心字段：模型可以替它们给出"提议值"（追问时会作为候选答案
# 摆在教师面前，点一下即确认），但不能替教师拍板。来源标 ai 的提议不算已确认 ——
# 否则模型一旦顺手把目标、重难点、产出填满，待问清单当场清空，对话直接跳到确认
# 面板（"问几轮就出方案"）。学段、学科、授课对象、课时是事实性信息，能从对话里
# 抽到即可用，不在此列。
TEACHER_CONFIRM_FIELDS: tuple[str, ...] = (
    "teaching_goal",
    "knowledge_points",
    "teaching_focus",
    "teaching_difficulties",
    "output_types",
    "logic_flow",
)


def empty_content() -> dict[str, Any]:
    return {
        "course_name": "",
        "subject": "",
        "grade": "",
        "teaching_goal": "",
        "target_audience": "",
        "duration_minutes": 45,
        "knowledge_points": [],
        "logic_flow": [],
        "teaching_focus": "",
        "teaching_difficulties": "",
        "output_types": [],
        "interaction_ideas": "",
        "style_preference": "",
        "existing_knowledge": "",
        "case_preference": "",
        "homework_type": "",
        "forbidden_content": "",
        "scenario_extensions": "",
        "extra_requirements": "",
        "missing_info": [],
        "follow_up_question": None,
        "confirm_summary": None,
    }


def normalize_content(raw: dict[str, Any] | None) -> dict[str, Any]:
    content = empty_content()
    if raw:
        content.update(deepcopy(raw))

    try:
        content["duration_minutes"] = int(content.get("duration_minutes") or 45)
    except (TypeError, ValueError):
        content["duration_minutes"] = 45

    normalized_points = []
    for index, item in enumerate(content.get("knowledge_points") or []):
        if not isinstance(item, dict):
            continue
        point = KnowledgePoint.model_validate(item)
        if not point.point_id:
            identity = f"{index}:{point.order}:{point.title}".encode("utf-8")
            point.point_id = f"kp_{sha256(identity).hexdigest()[:16]}"
        normalized_points.append(point.model_dump())
    content["knowledge_points"] = normalized_points
    content["logic_flow"] = _clean_string_list(content.get("logic_flow"))
    content["output_types"] = _clean_string_list(content.get("output_types"))
    for field in (
        "teaching_goal",
        "target_audience",
        "teaching_focus",
        "teaching_difficulties",
        "interaction_ideas",
        "style_preference",
        "existing_knowledge",
        "case_preference",
        "homework_type",
        "forbidden_content",
        "scenario_extensions",
        "extra_requirements",
    ):
        content[field] = str(content.get(field) or "").strip()
    content["missing_info"] = _clean_string_list(content.get("missing_info"))
    # 追问的候选答案：统一成字符串列表，前端据此渲染按钮
    content["options"] = _clean_string_list(content.get("options"))
    return content


def intent_to_content(result: IntentResult) -> dict[str, Any]:
    content = empty_content()
    content.update(
        {
            # 学科与学段决定后面整份设计的组织方式（学段适配、学科易错点），
            # 以前这两项没有落点：模型问出来了也存不住，下一轮又问一遍，
            # 生成时 brief.subject 还是空、学科规则永远注入不进来。
            "course_name": result.course_name,
            "subject": result.subject,
            "grade": result.grade,
            "teaching_goal": result.teaching_goal,
            "target_audience": result.target_audience,
            "duration_minutes": result.duration_minutes,
            "knowledge_points": [item.model_dump() for item in result.knowledge_points],
            "logic_flow": result.logic_flow,
            "teaching_focus": result.teaching_focus,
            "teaching_difficulties": result.teaching_difficulties,
            "output_types": result.output_types,
            "interaction_ideas": result.interaction_ideas,
            "style_preference": result.style_preference,
            "existing_knowledge": result.existing_knowledge,
            "case_preference": result.case_preference,
            "homework_type": result.homework_type,
            "forbidden_content": result.forbidden_content,
            "scenario_extensions": result.scenario_extensions,
            "extra_requirements": result.extra_requirements,
            "missing_info": result.missing_info,
            "follow_up_question": result.follow_up_question,
            # 本轮追问的候选答案：和 follow_up_question 一样是"这一轮"的信息，
            # 但必须穿过需求单，因为它要在追问卡片上渲染成按钮。
            "options": result.options,
            "confirm_summary": result.confirm_summary,
        }
    )
    return normalize_content(content)


def pending_core_fields(
    content: dict[str, Any],
    source_refs: dict[str, Any] | None = None,
) -> list[str]:
    """按必问顺序返回仍未确认的核心字段（字段名，不是标签）。

    ``duration_minutes`` 有默认值 45，所以"有值"不等于"已确认"：只有 source_refs
    里标了 teacher / ai / default 才算真被定过，否则仍是待确认项 —— 这修掉了"默认
    45 分钟被当成已确认、于是从来不问课时"的老问题。

    ``TEACHER_CONFIRM_FIELDS`` 里的设计取向字段更严：模型自己填的值（source=ai）
    只是提议，仍算待确认 —— 必须由教师选过一次（teacher）或明确跳过（default）。

    来源标为 ``default`` 表示教师主动跳过、按默认处理：这类字段不再算待确认，否则
    跳过之后进度永远清不掉、确认按钮也点不动。
    """
    normalized = normalize_content(content)
    refs = source_refs or {}
    pending: list[str] = []
    for field in CORE_FIELD_ORDER:
        value = normalized.get(field)
        source = str(refs.get(field) or "").lower()
        if source == "default":
            continue
        if field == "duration_minutes":
            confirmed = source in {"teacher", "ai"}
            duration_ok = isinstance(value, int) and 1 <= value <= 480
            if not confirmed or not duration_ok:
                pending.append(field)
            continue
        if field in TEACHER_CONFIRM_FIELDS:
            if source != "teacher" or not is_filled(value):
                pending.append(field)
            continue
        if not value:
            pending.append(field)
    return pending


def missing_fields(
    content: dict[str, Any],
    source_refs: dict[str, Any] | None = None,
) -> list[str]:
    """缺失核心字段的中文标签（界面与错误提示用），顺序与必问顺序一致。"""
    normalized = normalize_content(content)
    labels = [FIELD_LABELS[field] for field in pending_core_fields(normalized, source_refs)]

    # 模型额外指出的缺失项也并进来（例如"具体篇目""实验器材"这类非核心项）
    for hint in normalized.get("missing_info", []):
        text = str(hint)
        if "对象" in text and FIELD_LABELS["target_audience"] not in labels:
            labels.append(FIELD_LABELS["target_audience"])
        if ("课时" in text or "时长" in text) and FIELD_LABELS["duration_minutes"] not in labels:
            labels.append(FIELD_LABELS["duration_minutes"])
        if ("目标" in text or "主题" in text) and FIELD_LABELS["teaching_goal"] not in labels:
            labels.append(FIELD_LABELS["teaching_goal"])
    return labels


def is_complete(content: dict[str, Any], source_refs: dict[str, Any] | None = None) -> bool:
    return not missing_fields(content, source_refs)


# 每个核心字段的"确定性问题 + 默认候选答案"。
#
# 为什么后端要自己写死问题：实测 deepseek-v4-flash 会偶尔完全无视 JSON 模式，
# 返回一句纯文本，那一轮就什么都抽不到 —— 之后模型又会把同一个字段再问一遍，
# 教师看到的就是"同一个问题问好几遍"。所以"问什么"必须由后端的状态决定，
# 模型只负责抽取字段和把话说明白；模型整轮失败时后端也能照常推进。
FIELD_QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "grade": (
        "这节课是给哪个学段的学生上？",
        ["小学", "初中", "高中", "大学"],
    ),
    "subject": (
        "这属于哪个学科？",
        [
            "语文",
            "数学",
            "英语",
            "物理",
            "化学",
            "生物",
            "历史",
            "地理",
            "道德与法治",
            "信息技术",
            "音乐",
            "体育",
            "美术",
            "其他",
        ],
    ),
    "target_audience": (
        "这个班的基础和特点大概是怎样的？",
        ["基础较好，可以直接上新内容", "基础一般，需要先复习铺垫", "基础较弱，需要放慢节奏"],
    ),
    "duration_minutes": (
        "这节课安排多长时间？",
        ["40 分钟", "45 分钟", "90 分钟（两课时连堂）", "由你按内容定"],
    ),
    "teaching_goal": (
        "这节课最想让学生达成什么？",
        ["理解核心概念", "能解释生活中的现象", "能独立完成典型题", "能迁移到新情境"],
    ),
    "knowledge_points": (
        "这节课要覆盖哪些知识点？",
        ["按教材章节顺序", "按难度递进组织", "用一条案例主线串起来"],
    ),
    "teaching_focus": (
        "这节课的重点放在哪一块？",
        ["概念理解", "方法与步骤", "实验与探究", "应用与迁移"],
    ),
    "teaching_difficulties": (
        "你觉得学生最容易卡在哪里？",
        ["概念本身容易混淆", "步骤与书写不规范", "公式或计算容易出错", "不会迁移到新情境"],
    ),
    "output_types": (
        "这次需要哪些成果？",
        ["只要 PPT", "PPT + 教案", "PPT + 教案 + 打印版 + 互动"],
    ),
    "logic_flow": (
        "这节课的推进顺序你倾向哪种？",
        ["情境导入 → 讲解 → 练习 → 小结", "问题链推进", "任务驱动（做中学）"],
    ),
}


def proposal_option(content: dict[str, Any], field: str) -> str:
    """把该字段已有的（模型提议的）值整理成一个候选答案。

    设计类字段即使被模型填过也要请教师确认，追问时把提议值放进候选答案里，
    教师点一下就算确认，不必重新打字。放不进按钮的长文本返回空串 —— 那种
    情况交给模型在 options 里自己改写。
    """
    value = normalize_content(content).get(field)
    if field == "duration_minutes":
        text = f"{value} 分钟" if value else ""
    elif isinstance(value, list):
        titles = [
            str(item.get("title") if isinstance(item, dict) else item).strip()
            for item in value
        ]
        text = "、".join(title for title in titles if title)
    else:
        text = str(value or "").strip()
    return text if 2 <= len(text) <= 40 else ""


def next_question(
    content: dict[str, Any],
    source_refs: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]] | None:
    """下一个要问的字段，以及后端准备好的问题与候选答案；都齐了就返回 None。

    同一个字段最多问 ``MAX_ASKS_PER_FIELD`` 次。实测出现过教师回答了"大学"、
    但模型只把信息写进 target_audience、grade 字段始终为空的情况 —— 那时待问清单
    永远是 ['grade', ...]，教师看到的就是"不管说什么都在问学段"。问够次数还拿不到
    就跳过它，宁可少问一项也不能把对话卡死。
    """
    pending = pending_core_fields(content, source_refs)
    if not pending:
        return None
    counts = content.get("ask_counts") or {}
    for field in pending:
        if int(counts.get(field, 0)) >= MAX_ASKS_PER_FIELD:
            continue
        question, options = FIELD_QUESTIONS[field]
        return field, question, options
    return None


def remember_asked_field(
    db: DBSession,
    brief: TeachingBrief | None,
    field: str | None,
) -> None:
    """记下本轮追问的是哪个字段，并累计它被问过的次数。

    ``asking_field`` 供模型整轮失败的兜底使用（把教师的回答补进这一项）；
    ``ask_counts`` 供 ``next_question`` 判断"这一项是不是问太多次了"。
    """
    if brief is None:
        return
    content = normalize_content(brief.content_json)
    content["asking_field"] = field or ""
    if field:
        counts = dict(content.get("ask_counts") or {})
        counts[field] = int(counts.get(field, 0)) + 1
        content["ask_counts"] = counts
    brief.content_json = content
    db.flush()


# 同一个字段最多追问几次（超过就跳过，避免"永远在问学段"）
MAX_ASKS_PER_FIELD = 2

# 从上下文推断学段：顺序敏感（"大三"要在"高中"之前匹配，研究生优先于大学）
_GRADE_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("研究生", "硕士", "博士"), "研究生"),
    (("大学", "本科", "高校", "大一", "大二", "大三", "大四"), "大学"),
    (("高中", "高一", "高二", "高三"), "高中"),
    (("初中", "初一", "初二", "初三"), "初中"),
    (
        (
            "小学",
            "一年级",
            "二年级",
            "三年级",
            "四年级",
            "五年级",
            "六年级",
        ),
        "小学",
    ),
    (("中职", "中专", "技校", "职高"), "中职"),
    (("幼儿园", "学前"), "学前"),
)

# 哪些字段里可能写着学段线索
_GRADE_TEXT_FIELDS = (
    "target_audience",
    "teaching_goal",
    "course_name",
    "existing_knowledge",
    "extra_requirements",
    "teaching_focus",
)


def infer_grade(content: dict[str, Any]) -> str:
    """从已确认的上下文里推断学段。

    模型经常把"大学"这类信息写进 target_audience 而不填 grade 字段，
    于是 grade 永远为空、待问清单永远第一项是 grade。这里补上推断。
    """
    for field in _GRADE_TEXT_FIELDS:
        text = str(content.get(field) or "")
        if not text:
            continue
        for keywords, grade in _GRADE_PATTERNS:
            if any(keyword in text for keyword in keywords):
                return grade
    return ""


def capture_teacher_answer(
    content: dict[str, Any],
    source_refs: dict[str, Any],
    field: str,
    message: str,
) -> bool:
    """教师已经回答过、但模型没写进该字段时，由后端补上。

    只在字段仍为空且回答形状对得上时写入；返回是否真的落了值。
    """
    if not field or str(content.get(field) or "").strip():
        return False
    before = str(content.get(field) or "")
    apply_teacher_answer(content, source_refs, field, message)
    return str(content.get(field) or "") != before


_GRADE_HINTS = ("小", "初", "高", "年级", "大学", "中职", "大一", "大二", "大三", "大四", "研究生")
_OUTPUT_HINTS = ("ppt", "课件", "教案", "docx", "pdf", "打印", "讲义", "互动", "html", "都要", "全套")

# 列表字段不能以"整句话"的形式直接落到字段上：normalize_content 会把非列表值清成
# []，但 source_refs 已经标成 teacher —— 之后模型想再填也会被"教师已答"挡住
# （persist_intent_result 跳过 teacher 来源的字段），字段永远空着、永远问不完。
# 列表的结构化解析交给模型，这里只做兜底，形状对不上就不写、下一轮再问。
_LIST_FIELDS = frozenset({"knowledge_points", "logic_flow", "output_types"})


def _answer_fits(field: str, text: str) -> bool:
    """回答是否像是这个字段的答案。

    兜底路径是按"上一轮问的是哪个字段"写入的，万一教师答的是别的事（临时换话题、
    补一句新要求），直接写进去就会污染需求单 —— 所以按字段做个形状校验：
    拿不准就不写，宁可下一轮再问一次。
    """
    lowered = text.lower()
    if field == "grade":
        return any(hint in text for hint in _GRADE_HINTS)
    if field == "subject":
        return any(hint in text for hint in _SUBJECT_HINTS) or len(text) <= 6
    if field == "output_types":
        return any(hint in lowered for hint in _OUTPUT_HINTS)
    if field == "target_audience":
        return len(text) >= 2
    return True


def apply_teacher_answer(
    content: dict[str, Any],
    source_refs: dict[str, Any],
    field: str,
    value: str,
) -> None:
    """把教师的回答写进需求单（用于模型整轮失败的兜底，避免答案被丢掉）。"""
    text = str(value or "").strip()
    if field not in FIELD_LABELS or not text:
        return
    if field in _LIST_FIELDS:
        # 列表字段需要结构化内容，兜底路径没法可靠地切分/归一化；写进去必被清掉，
        # 只留下"教师已答"的假来源标记，反而把后面真正能填的模型挡在门外。
        return
    if field == "duration_minutes":
        match = re.search(r"\d+", text)
        if match:
            minutes = int(match.group(0))
            if 1 <= minutes <= 480:
                content[field] = minutes
                source_refs[field] = "teacher"
        return
    if not _answer_fits(field, text):
        return
    content[field] = text
    source_refs[field] = "teacher"


def is_filled(value: Any) -> bool:
    """字段是否有可展示的值（空串、空列表、空字典都算没有）。"""
    return value not in (None, "", [], {})


def _value_text(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item.get("title") if isinstance(item, dict) else item) for item in value]
        return "、".join(item for item in items if item) or "（空）"
    return str(value)


def brief_context_block(brief: "TeachingBrief | None") -> str:
    """把"已经确认了什么、还缺什么"整理成给模型看的上下文。

    模型以前只看得到聊天历史：对话一长就漏问、重问，先问课时还是先问学段全看当轮
    发挥。把需求单的当前状态和按优先级排好的待问清单直接交给它，追问才会稳定推进，
    而且"缺什么"只有这一份事实来源（与确认校验共用同一套判定）。

    "问哪一项"由后端定死；"怎么问、给什么选项"交给模型 —— 只有它看得到对话里的
    具体课题，才能拟出贴合本课的候选答案（后端写死的四选一像在填表）。
    """
    content = normalize_content(brief.content_json if brief is not None else None)
    refs = (brief.source_refs if brief is not None else {}) or {}

    pending_fields = pending_core_fields(content, refs)

    def _pending_note(field: str) -> str:
        if field not in pending_fields:
            return ""
        if str(refs.get(field) or "").lower() == "ai":
            return "（模型的提议，尚未经教师确认）"
        # 像课时这种有默认值的字段必须标出来：否则模型看到"45"会以为已定，
        # 而它其实只是默认值，仍应追问。
        if field == "duration_minutes":
            return "（默认值，尚未确认）"
        return "（尚未确认）"

    known = [
        f"- {FIELD_LABELS[field]}：{_value_text(content.get(field))}{_pending_note(field)}"
        for field in ("course_name", *CORE_FIELD_ORDER)
        if is_filled(content.get(field))
    ]
    pending = [FIELD_LABELS[field] for field in pending_fields]

    lines = [
        "当前需求单状态（唯一事实来源：不要重复追问这里已经确认的信息）：",
        *(known or ["- （还没有任何已确认信息）"]),
    ]
    target = next_question(content, refs)
    if target is not None:
        field, question, options = target
        lines.append("尚未确认的核心字段（按此顺序）：" + "、".join(pending))
        lines.append(
            f"本轮只问这一项：{FIELD_LABELS[field]}（字段名 {field}）。"
            "请结合本课课题，用你自己的话把它问成一个具体的问题；"
            f"参考问法（可改写得更贴合课题，意思要落在这项上）：{question}"
        )
        lines.append(
            "并给出 3-5 个能直接回答这个问题的候选答案，填进 options 字段；"
            "选项要具体到本课的内容（知识点、活动、做法或取向），不要用放之四海皆准的空话。"
            f"参考候选（优先自己按课题拟更贴切的）：{'；'.join(options)}"
        )
        proposal = proposal_option(content, field)
        if proposal:
            lines.append(
                f"该项目前有一个待确认的提议值：{proposal}。"
                "请把它作为第一个选项（如与课题不符，可改成更贴切的），"
                "再补 2-4 个层次或取向不同的备选。"
            )
        lines.append(f"同时把教师的回答尽力抽取进字段 {field}。")
    else:
        lines.append("核心字段已齐全：不要再追问核心字段，直接给出确认总结。")
    return "\n".join(lines)


def state_after(session: Session, complete: bool, *, locked: bool = False) -> str:
    if locked:
        return State.LOCKED.value

    machine = IntentStateMachine()
    try:
        machine.state = State(session.intent_state or State.INIT.value)
    except ValueError:
        machine.state = State.INIT
    if machine.state == State.LOCKED:
        machine.reset()
    return machine.transition(complete).value


def get_latest_brief(
    db: DBSession,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
) -> TeachingBrief | None:
    query = db.query(TeachingBrief)
    if project_id:
        query = query.filter(TeachingBrief.project_id == project_id)
    elif session_id:
        query = query.filter(TeachingBrief.session_id == session_id)
    else:
        return None
    return query.order_by(TeachingBrief.version.desc(), TeachingBrief.created_at.desc()).first()


def get_confirmed_brief(
    db: DBSession,
    *,
    project_id: str | None = None,
) -> TeachingBrief | None:
    """Return the newest confirmed brief, ignoring drafts opened after it.

    Confirming locks one brief, but any later chat turn or inline edit opens a
    new draft version on top of it. Consumers that need an approved requirement
    sheet must look for the confirmed row instead of whichever row is newest,
    otherwise a single follow-up message silently invalidates the confirmation.
    """
    if not project_id:
        return None
    return (
        db.query(TeachingBrief)
        .filter(
            TeachingBrief.project_id == project_id,
            TeachingBrief.status == "confirmed",
        )
        .order_by(TeachingBrief.version.desc(), TeachingBrief.created_at.desc())
        .first()
    )


def _next_version(db: DBSession, project_id: str | None, session_id: str | None) -> int:
    query = db.query(func.max(TeachingBrief.version))
    if project_id:
        query = query.filter(TeachingBrief.project_id == project_id)
    elif session_id:
        query = query.filter(TeachingBrief.session_id == session_id)
    return (query.scalar() or 0) + 1


def _new_draft(
    db: DBSession,
    *,
    user_id: str | None,
    project_id: str | None,
    session_id: str | None,
    seed: dict[str, Any] | None = None,
    seed_refs: dict[str, Any] | None = None,
) -> TeachingBrief:
    """``seed_refs`` 必须跟着 ``seed`` 一起搬：设计类字段只有 source=teacher 才算
    确认过（见 ``pending_core_fields``）。只搬值不搬来源，新草稿会把教师确认过的
    目标/重难点又当成"模型的提议"，确认单上永远差几项、确认按钮点不动。
    """
    lock = project_version_lock(db, project_id) if project_id else nullcontext()
    with lock:
        now = datetime.now(timezone.utc)
        brief = TeachingBrief(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            version=_next_version(db, project_id, session_id),
            status="draft",
            content_json=normalize_content(seed),
            source_refs=dict(seed_refs or {}),
            confidence={},
            created_at=now,
            updated_at=now,
        )
        db.add(brief)
        db.flush()
    return brief


def persist_intent_result(
    db: DBSession,
    session: Session,
    result: IntentResult,
) -> tuple[TeachingBrief, IntentResult]:
    """Merge one analyzer result into a durable draft and state machine."""
    latest = get_latest_brief(db, project_id=session.project_id, session_id=session.session_id)
    if latest is None:
        draft = _new_draft(
            db,
            user_id=session.user_id,
            project_id=session.project_id,
            session_id=session.session_id,
        )
    elif latest.status == "confirmed":
        draft = _new_draft(
            db,
            user_id=session.user_id,
            project_id=session.project_id,
            session_id=session.session_id,
            seed=latest.content_json,
            seed_refs=latest.source_refs,
        )
    else:
        draft = latest

    current = normalize_content(draft.content_json)
    incoming = intent_to_content(result)
    ai_fields = (
        "course_name",
        "subject",
        "grade",
        "teaching_goal",
        "target_audience",
        "duration_minutes",
        "knowledge_points",
        "logic_flow",
        "teaching_focus",
        "teaching_difficulties",
        "output_types",
        "interaction_ideas",
        "style_preference",
        "existing_knowledge",
        "case_preference",
        "homework_type",
        "forbidden_content",
        "scenario_extensions",
        "extra_requirements",
        "missing_info",
        "follow_up_question",
        "options",
        "confirm_summary",
    )
    source_refs = dict(draft.source_refs or {})
    for field in ai_fields:
        if source_refs.get(field) == "teacher":
            continue
        value = incoming.get(field)
        # options 必须无条件覆盖：本轮没有候选答案时要清空上一轮残留的按钮，
        # 否则界面会拿旧问题的一堆选项去回答新问题。
        if value or field in {"duration_minutes", "missing_info", "options"}:
            current[field] = value
            source_refs[field] = "ai"
    current = normalize_content(current)
    draft.content_json = current
    draft.source_refs = {**source_refs, "conversation": "ai"}
    draft.confidence = {
        **(draft.confidence or {}),
        "teaching_goal": 0.7 if current.get("teaching_goal") else 0.0,
        "target_audience": 0.7 if current.get("target_audience") else 0.0,
        "knowledge_points": 0.7 if current.get("knowledge_points") else 0.0,
    }
    draft.updated_at = datetime.now(timezone.utc)

    complete = is_complete(current, source_refs)
    session.intent_data = current
    session.intent_state = state_after(session, complete)
    session.current_brief_id = draft.brief_id
    db.flush()

    normalized_result = brief_to_intent(draft)
    normalized_result.follow_up_question = (
        result.follow_up_question or current.get("follow_up_question")
    )
    normalized_result.confirm_summary = result.confirm_summary or current.get("confirm_summary")
    normalized_result.missing_info = missing_fields(current, source_refs)
    normalized_result.is_complete = complete
    return draft, normalized_result


def ensure_draft(
    db: DBSession,
    *,
    user_id: str | None,
    project_id: str,
    session_id: str | None,
) -> TeachingBrief:
    """拿到当前草稿；没有（或上一份已确认）就新建一份。

    模型整轮失败时的兜底也要能把状态落下来 —— 新会话的第一轮如果模型抽风，
    没有草稿就等于什么都没记住，同一个问题又会被问一次。
    """
    latest = get_latest_brief(db, project_id=project_id, session_id=session_id)
    if latest is not None and latest.status != "confirmed":
        return latest
    if latest is None:
        latest = get_latest_brief(db, project_id=project_id)
    return _new_draft(
        db,
        user_id=user_id,
        project_id=project_id,
        session_id=session_id or (latest.session_id if latest is not None else None),
        seed=latest.content_json if latest is not None else None,
        seed_refs=latest.source_refs if latest is not None else None,
    )


def update_draft(
    db: DBSession,
    *,
    user_id: str | None,
    project_id: str,
    session_id: str | None,
    changes: TeachingBriefUpdate,
) -> TeachingBrief:
    latest = get_latest_brief(db, project_id=project_id)
    if latest is None:
        draft = _new_draft(
            db,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
        )
    elif latest.status == "confirmed":
        draft = _new_draft(
            db,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id or latest.session_id,
            seed=latest.content_json,
            seed_refs=latest.source_refs,
        )
    else:
        draft = latest

    content = normalize_content(draft.content_json)
    changed_fields = changes.model_dump(exclude_unset=True)
    for field, value in changed_fields.items():
        if value is not None:
            content[field] = value
    content["missing_info"] = []
    content = normalize_content(content)
    draft.content_json = content
    draft.source_refs = {
        **(draft.source_refs or {}),
        **{field: "teacher" for field in changed_fields},
    }
    draft.confidence = {
        **(draft.confidence or {}),
        **{field: 1.0 for field in changed_fields},
    }
    draft.updated_at = datetime.now(timezone.utc)
    if session_id:
        session = db.query(Session).filter(Session.session_id == session_id).first()
        if session:
            session.intent_data = content
            session.intent_state = state_after(session, is_complete(content, draft.source_refs))
            session.current_brief_id = draft.brief_id
    db.flush()
    return draft


def confirm_draft(
    db: DBSession,
    *,
    project: Project,
    expected_version: int | None = None,
) -> TeachingBrief:
    latest = get_latest_brief(db, project_id=project.project_id)
    if latest is None:
        raise ApiError("需求确认单尚未生成", code="BRIEF_NOT_FOUND", status_code=404)
    if expected_version is not None and latest.version != expected_version:
        raise ApiError(
            "需求确认单已发生变化，请刷新后重试",
            code="BRIEF_VERSION_CONFLICT",
            status_code=409,
            details={"expected_version": expected_version, "current_version": latest.version},
        )
    if latest.status == "confirmed":
        return latest

    content = normalize_content(latest.content_json)
    missing = missing_fields(content, latest.source_refs)
    if missing:
        raise ApiError(
            "需求确认单仍缺少必要信息",
            code="BRIEF_INCOMPLETE",
            status_code=422,
            details={"missing_fields": missing},
            suggested_action="补充缺失字段后再确认",
        )

    now = datetime.now(timezone.utc)
    latest.status = "confirmed"
    latest.confirmed_at = now
    latest.updated_at = now
    if latest.session_id:
        session = db.query(Session).filter(Session.session_id == latest.session_id).first()
        if session:
            session.intent_state = State.LOCKED.value
            session.intent_data = content
            session.current_brief_id = latest.brief_id
    db.flush()
    return latest


def brief_to_intent(brief: TeachingBrief) -> IntentResult:
    content = normalize_content(brief.content_json)
    refs = brief.source_refs or {}
    return IntentResult(
        course_name=content.get("course_name", ""),
        subject=content.get("subject", ""),
        grade=content.get("grade", ""),
        teaching_goal=content.get("teaching_goal", ""),
        target_audience=content.get("target_audience", ""),
        duration_minutes=content.get("duration_minutes", 45),
        knowledge_points=[KnowledgePoint.model_validate(item) for item in content.get("knowledge_points", [])],
        logic_flow=content.get("logic_flow", []),
        teaching_focus=content.get("teaching_focus", ""),
        teaching_difficulties=content.get("teaching_difficulties", ""),
        output_types=content.get("output_types", []),
        interaction_ideas=content.get("interaction_ideas", ""),
        style_preference=content.get("style_preference", ""),
        existing_knowledge=content.get("existing_knowledge", ""),
        case_preference=content.get("case_preference", ""),
        homework_type=content.get("homework_type", ""),
        forbidden_content=content.get("forbidden_content", ""),
        scenario_extensions=content.get("scenario_extensions", ""),
        extra_requirements=content.get("extra_requirements", ""),
        missing_info=missing_fields(content, refs),
        follow_up_question=content.get("follow_up_question"),
        options=[str(item) for item in (content.get("options") or []) if str(item).strip()],
        confirm_summary=content.get("confirm_summary"),
        # 是否完整按"字段 + 来源"判定：课时有默认值 45，只有 teacher/ai 定过才算数
        is_complete=is_complete(content, refs),
    )


def to_info(brief: TeachingBrief) -> TeachingBriefInfo:
    content = normalize_content(brief.content_json)
    refs = brief.source_refs or {}
    return TeachingBriefInfo(
        brief_id=brief.brief_id,
        user_id=brief.user_id,
        project_id=brief.project_id,
        session_id=brief.session_id,
        version=brief.version,
        status=brief.status,
        content=content,
        teaching_goal=content.get("teaching_goal", ""),
        target_audience=content.get("target_audience", ""),
        duration_minutes=content.get("duration_minutes", 45),
        knowledge_points=[KnowledgePoint.model_validate(item) for item in content.get("knowledge_points", [])],
        logic_flow=content.get("logic_flow", []),
        teaching_focus=content.get("teaching_focus", ""),
        teaching_difficulties=content.get("teaching_difficulties", ""),
        output_types=content.get("output_types", []),
        interaction_ideas=content.get("interaction_ideas", ""),
        style_preference=content.get("style_preference", ""),
        missing_info=missing_fields(content, refs),
        # 待确认字段（字段名）与总数：界面据此显示"已确认 x/y"并把待确认项高亮。
        # 不交给前端自己判断 —— 课时有默认值 45，只有 teacher/ai 定过才算数。
        pending_fields=pending_core_fields(content, refs),
        core_total=len(CORE_FIELD_ORDER),
        is_complete=is_complete(content, refs),
        source_refs=brief.source_refs or {},
        confidence=brief.confidence or {},
        created_at=brief.created_at,
        updated_at=brief.updated_at or brief.created_at,
        confirmed_at=brief.confirmed_at,
    )


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
