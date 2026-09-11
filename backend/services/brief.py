"""TeachingBrief merge, validation and versioning services."""

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
from backend.services.version_allocator import project_version_lock

FIELD_LABELS = {
    "teaching_goal": "教学目标",
    "target_audience": "授课对象",
    "duration_minutes": "课时长度",
    "knowledge_points": "核心知识点",
    "logic_flow": "知识点逻辑顺序",
    "teaching_focus": "教学重点",
    "teaching_difficulties": "教学难点",
    "output_types": "产出类型",
}


def empty_content() -> dict[str, Any]:
    return {
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
    return content


def intent_to_content(result: IntentResult) -> dict[str, Any]:
    content = empty_content()
    content.update(
        {
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
            "confirm_summary": result.confirm_summary,
        }
    )
    return normalize_content(content)


def missing_fields(content: dict[str, Any]) -> list[str]:
    normalized = normalize_content(content)
    missing: list[str] = []
    for field in (
        "teaching_goal",
        "target_audience",
        "knowledge_points",
        "logic_flow",
        "teaching_focus",
        "teaching_difficulties",
        "output_types",
    ):
        if not normalized.get(field):
            missing.append(FIELD_LABELS[field])

    duration = normalized.get("duration_minutes")
    if not isinstance(duration, int) or not 1 <= duration <= 480:
        missing.append(FIELD_LABELS["duration_minutes"])

    for hint in normalized.get("missing_info", []):
        if "对象" in hint and FIELD_LABELS["target_audience"] not in missing:
            missing.append(FIELD_LABELS["target_audience"])
        if ("课时" in hint or "时长" in hint) and FIELD_LABELS["duration_minutes"] not in missing:
            missing.append(FIELD_LABELS["duration_minutes"])
        if ("目标" in hint or "主题" in hint) and FIELD_LABELS["teaching_goal"] not in missing:
            missing.append(FIELD_LABELS["teaching_goal"])
    return missing


def is_complete(content: dict[str, Any]) -> bool:
    return not missing_fields(content)


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
) -> TeachingBrief:
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
            source_refs={},
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
        )
    else:
        draft = latest

    current = normalize_content(draft.content_json)
    incoming = intent_to_content(result)
    ai_fields = (
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
        "confirm_summary",
    )
    source_refs = dict(draft.source_refs or {})
    for field in ai_fields:
        if source_refs.get(field) == "teacher":
            continue
        value = incoming.get(field)
        if value or field in {"duration_minutes", "missing_info"}:
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

    complete = is_complete(current)
    session.intent_data = current
    session.intent_state = state_after(session, complete)
    session.current_brief_id = draft.brief_id
    db.flush()

    normalized_result = brief_to_intent(draft)
    normalized_result.follow_up_question = (
        result.follow_up_question or current.get("follow_up_question")
    )
    normalized_result.confirm_summary = result.confirm_summary or current.get("confirm_summary")
    normalized_result.missing_info = missing_fields(current)
    normalized_result.is_complete = complete
    return draft, normalized_result


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
            session.intent_state = state_after(session, is_complete(content))
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
    missing = missing_fields(content)
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
    return IntentResult(
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
        missing_info=missing_fields(content),
        follow_up_question=content.get("follow_up_question"),
        confirm_summary=content.get("confirm_summary"),
        is_complete=is_complete(content),
    )


def to_info(brief: TeachingBrief) -> TeachingBriefInfo:
    content = normalize_content(brief.content_json)
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
        missing_info=missing_fields(content),
        is_complete=is_complete(content),
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
