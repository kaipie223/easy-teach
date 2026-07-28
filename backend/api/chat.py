"""M1 - Chat and intent understanding API."""

from fastapi import APIRouter

from core.errors import ApiError
from models.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/send", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise ApiError("Message cannot be empty.", code="empty_message", status_code=422)

    return ChatResponse(
        session_id=req.session_id,
        reply="已收到消息，正在分析教学意图。",
        intent=None,
    )
