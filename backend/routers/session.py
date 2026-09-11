"""Authenticated, user-isolated session management APIs."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import IntegrityError

from backend.core.ownership import get_project_for_user, get_session_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import ChatMessage, Session
from backend.models.user import User
from backend.schemas import ChatMessageInfo, SessionCreate, SessionInfo
from backend.services.chat import create_chat_stream
from backend.services.limits import consume_model_quota

router = APIRouter()
BOOTSTRAP_STALE_AFTER = timedelta(minutes=2)


def _is_bootstrap_message(message: ChatMessage) -> bool:
    return bool((message.event_data or {}).get("bootstrap"))


def _session_info(session: Session, db: DBSession) -> SessionInfo:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    return SessionInfo(
        session_id=session.session_id,
        teacher_name=session.teacher_name or "",
        subject=session.subject or "",
        user_id=session.user_id,
        project_id=session.project_id,
        status=session.status or "active",
        created_at=session.created_at,
        messages=[
            ChatMessageInfo.model_validate(message)
            for message in messages
            if not _is_bootstrap_message(message)
        ],
        intent_state=session.intent_state,
        brief_id=session.current_brief_id,
    )


@router.post("", response_model=SessionInfo, status_code=201)
def create_session(
    req: SessionCreate,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project: Project | None = None
    if req.project_id:
        project = get_project_for_user(db, req.project_id, user)

    now = datetime.now(timezone.utc)
    session = Session(
        user_id=user.user_id,
        project_id=project.project_id if project else None,
        teacher_name=req.teacher_name,
        subject=req.subject,
        status="active",
        created_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_info(session, db)


@router.get("", response_model=list[SessionInfo])
def list_sessions(
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Session).filter(Session.user_id == user.user_id)
    sessions = query.order_by(Session.created_at.desc()).all()
    return [_session_info(session, db) for session in sessions]


@router.get("/{session_id}", response_model=SessionInfo)
def get_session(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_session_for_user(db, session_id, user)
    return _session_info(session, db)


@router.post("/{session_id}/start")
async def start_session(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = get_session_for_user(db, session_id, user)
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    bootstrap = next((message for message in messages if _is_bootstrap_message(message)), None)
    meaningful_messages = [
        message
        for message in messages
        if not _is_bootstrap_message(message) and message.msg_type != "error"
    ]
    if meaningful_messages:
        return Response(status_code=204)

    now = datetime.now(timezone.utc)
    if bootstrap is not None:
        bootstrap_created_at = bootstrap.created_at
        if bootstrap_created_at.tzinfo is None:
            bootstrap_created_at = bootstrap_created_at.replace(tzinfo=timezone.utc)
        bootstrap_failed = bool((bootstrap.event_data or {}).get("failed")) or any(
            message.msg_type == "error" for message in messages
        )
        if not bootstrap_failed and now - bootstrap_created_at < BOOTSTRAP_STALE_AFTER:
            return Response(status_code=204)

    subject = (session.subject or "新课程").strip()
    bootstrap_message = (
        f"我想设计一节“{subject}”课程。请根据这个主题主动开始需求共创，"
        "先整理已经明确的信息，再追问最必要的教学信息。"
    )
    if bootstrap is None:
        bootstrap = ChatMessage(
            id=f"bootstrap_{session.session_id}",
            user_id=session.user_id,
            project_id=session.project_id,
            session_id=session.session_id,
            role="user",
            content=bootstrap_message,
            msg_type="text",
            event_data={"bootstrap": True, "failed": False},
            created_at=now,
        )
        db.add(bootstrap)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return Response(status_code=204)
    else:
        bootstrap.content = bootstrap_message
        bootstrap.event_data = {"bootstrap": True, "failed": False}
        bootstrap.created_at = now
        db.commit()

    try:
        consume_model_quota(user.user_id)
    except Exception:
        bootstrap.event_data = {"bootstrap": True, "failed": True}
        db.commit()
        raise
    return await create_chat_stream(
        session,
        bootstrap_message,
        db,
        persist_user_message=False,
    )
