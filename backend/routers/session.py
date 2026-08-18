"""会话管理 API。已登录请求按用户隔离，匿名请求保留 M0 兼容行为。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.ownership import get_project_for_user, get_session_for_user
from backend.core.security import get_optional_current_user
from backend.core.errors import ApiError
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import ChatMessage, Session
from backend.models.user import User
from backend.schemas import ChatMessageInfo, SessionCreate, SessionInfo

router = APIRouter()


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
        messages=[ChatMessageInfo.model_validate(message) for message in messages],
        intent_state=session.intent_state,
        brief_id=session.current_brief_id,
    )


@router.post("", response_model=SessionInfo, status_code=201)
def create_session(
    req: SessionCreate,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    project: Project | None = None
    if req.project_id:
        if user is None:
            raise ApiError(
                "创建项目会话需要先登录",
                code="AUTH_REQUIRED",
                status_code=401,
                recoverable=False,
            )
        project = get_project_for_user(db, req.project_id, user)

    now = datetime.now(timezone.utc)
    session = Session(
        user_id=user.user_id if user else None,
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
    user: User | None = Depends(get_optional_current_user),
):
    query = db.query(Session)
    if user is None:
        query = query.filter(Session.user_id.is_(None))
    elif user.role != "admin":
        query = query.filter(Session.user_id == user.user_id)
    sessions = query.order_by(Session.created_at.desc()).all()
    return [_session_info(session, db) for session in sessions]


@router.get("/{session_id}", response_model=SessionInfo)
def get_session(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
):
    session = get_session_for_user(db, session_id, user)
    return _session_info(session, db)
