"""M1 — 对话 SSE API"""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.errors import ApiError
from schemas import ChatRequest
from services.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{session_id}/chat")
async def chat(session_id: str, req: ChatRequest):
    """SSE 流式对话 — 返回 text/question/confirm 事件流。"""
    message = req.message.strip()
    if not message:
        raise ApiError("消息不能为空", code="empty_message", status_code=422)

    orchestrator = get_orchestrator()

    async def event_stream():
        try:
            async for event in orchestrator.chat(session_id, message):
                data = event.model_dump_json()
                yield f"data: {data}\n\n"
        except Exception:
            logger.exception("SSE chat stream error")
            error_event = {"event_type": "text", "content": "抱歉，处理您的消息时出错了，请重试。", "data": None}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
