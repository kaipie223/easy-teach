"""M4 — 语音转文字 API"""

from fastapi import APIRouter, UploadFile, File

from models.schemas import SpeechResponse

router = APIRouter()


@router.post("/transcribe", response_model=SpeechResponse)
async def transcribe(audio: UploadFile = File(...)):
    """
    上传音频文件 → faster-whisper 转录 → 返回文本。
    由 M4 模块（陈澜 + 姜文杰 + 潘卓然）实现。
    """
    # TODO: 保存音频 → faster-whisper 推理 → 返回文本
    return SpeechResponse(text="（M4 占位）语音转文字结果", duration_seconds=0.0)
