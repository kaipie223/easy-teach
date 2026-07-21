"""M1 — 对话与意图理解 API"""

from fastapi import APIRouter

from models.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/send", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    """
    发送对话消息，返回 AI 回复 + 结构化意图分析结果。
    由 M1 模块（陈澜 + 姜文杰 + 潘卓然）实现。
    """
    # TODO: 调用意图理解引擎 → 生成回复
    return ChatResponse(
        session_id=req.session_id,
        reply="（M1 占位）收到您的消息，正在分析教学意图...",
        intent=None,
    )
