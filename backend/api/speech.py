"""M4 - Speech transcription API."""

from fastapi import APIRouter, File, UploadFile

from core.errors import ApiError
from models.schemas import SpeechResponse

router = APIRouter()


@router.post("/transcribe", response_model=SpeechResponse)
async def transcribe(audio: UploadFile = File(...)):
    if not audio.filename:
        raise ApiError("Audio filename is missing.", code="invalid_audio", status_code=400)

    return SpeechResponse(text="语音转文字结果尚未接入，当前返回占位文本。", duration_seconds=0.0)
