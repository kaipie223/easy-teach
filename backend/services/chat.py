"""Shared persistent SSE chat workflow for session and project routes."""

import json
import logging
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from backend.models.project import Project
from backend.models.session import ChatMessage, Session
from backend.services.intent import IntentServiceError
from backend.services.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)


async def create_chat_stream(
    session: Session,
    message: str,
    db: DBSession,
    *,
    persist_user_message: bool = True,
) -> StreamingResponse:
    previous_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .all()
    )
    history = [
        {"role": item.role, "content": item.content}
        for item in previous_messages
        if not (item.event_data or {}).get("bootstrap") and item.msg_type != "error"
    ]
    if persist_user_message:
        history.append({"role": "user", "content": message})
    session_user_id = session.user_id
    session_project_id = session.project_id
    session_key = session.session_id

    if persist_user_message:
        now = datetime.now(timezone.utc)
        db.add(
            ChatMessage(
                user_id=session_user_id,
                project_id=session_project_id,
                session_id=session_key,
                role="user",
                content=message,
                msg_type="text",
                created_at=now,
            )
        )
        if session_project_id:
            project = db.query(Project).filter(Project.project_id == session_project_id).first()
            if project is not None:
                project.updated_at = now
        db.commit()

    orchestrator = get_orchestrator()

    async def event_stream():
        try:
            # The request handler commits before the streaming body is consumed. The
            # original ORM instance may therefore be expired or detached; reload it
            # inside the active stream before the orchestrator mutates session state.
            current_session = (
                db.query(Session)
                .filter(Session.session_id == session_key)
                .first()
            )
            if current_session is None:
                raise RuntimeError(f"Session {session_key} no longer exists")

            async for event in orchestrator.chat(
                session_key,
                message,
                history=history,
                db=db,
                session=current_session,
            ):
                db.add(
                    ChatMessage(
                        user_id=session_user_id,
                        project_id=session_project_id,
                        session_id=session_key,
                        role="assistant",
                        content=event.content,
                        msg_type=event.event_type.value,
                        event_data=event.data,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                db.commit()
                data = event.model_dump_json()
                yield f"event: {event.event_type.value}\ndata: {data}\n\n"
        except IntentServiceError as exc:
            error_event = {
                "event_type": "error",
                "content": str(exc),
                "data": {
                    "code": exc.code,
                    "recoverable": exc.recoverable,
                    "suggested_action": exc.suggested_action,
                },
            }
            db.add(
                ChatMessage(
                    user_id=session_user_id,
                    project_id=session_project_id,
                    session_id=session_key,
                    role="assistant",
                    content=str(exc),
                    msg_type="error",
                    event_data=error_event["data"],
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("SSE chat stream error")
            error_content = "抱歉，处理您的消息时出错了，请重试。"
            db.add(
                ChatMessage(
                    user_id=session_user_id,
                    project_id=session_project_id,
                    session_id=session_key,
                    role="assistant",
                    content=error_content,
                    msg_type="error",
                    event_data={
                        "code": "CHAT_PROCESSING_FAILED",
                        "recoverable": True,
                        "suggested_action": "请重试",
                    },
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
            error_event = {
                "event_type": "error",
                "content": error_content,
                "data": {
                    "code": "CHAT_PROCESSING_FAILED",
                    "recoverable": True,
                    "suggested_action": "请重试",
                },
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
