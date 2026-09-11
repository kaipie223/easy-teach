"""Project-oriented M2 chat endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_project_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.project import Project
from backend.models.session import Session
from backend.models.user import User
from backend.schemas import ChatRequest
from backend.services.chat import create_chat_stream
from backend.services.limits import consume_model_quota

router = APIRouter()


def _latest_session(db: DBSession, project: Project) -> Session | None:
    return (
        db.query(Session)
        .filter(Session.project_id == project.project_id)
        .order_by(Session.created_at.desc())
        .first()
    )


@router.post("/{project_id}/messages")
async def project_chat(
    project_id: str,
    req: ChatRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    message = req.message.strip()
    if not message:
        raise ApiError("消息不能为空", code="empty_message", status_code=422)

    project = get_project_for_user(db, project_id, user)
    consume_model_quota(user.user_id)
    session = _latest_session(db, project)
    if session is None:
        from datetime import datetime, timezone

        session = Session(
            user_id=project.owner_id,
            project_id=project.project_id,
            subject=project.title,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    return await create_chat_stream(session, message, db)
