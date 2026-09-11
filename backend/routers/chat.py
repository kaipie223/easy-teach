"""Session-oriented M0 chat endpoint backed by the M2 persistent workflow."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from backend.core.errors import ApiError
from backend.core.ownership import get_session_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.user import User
from backend.schemas import ChatRequest
from backend.services.chat import create_chat_stream
from backend.services.limits import consume_model_quota

router = APIRouter()


@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    req: ChatRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    message = req.message.strip()
    if not message:
        raise ApiError("消息不能为空", code="empty_message", status_code=422)
    session = get_session_for_user(db, session_id, user)
    consume_model_quota(user.user_id)
    return await create_chat_stream(session, message, db)
