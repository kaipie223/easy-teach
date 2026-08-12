"""M4 — 语音转写 API"""

import uuid
import time
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from config import settings
from core.errors import ApiError
from schemas import SpeechResponse
from services.speech import transcribe_audio

router = APIRouter()


@router.post("/transcribe", response_model=SpeechResponse)
async def transcribe(audio: UploadFile = File(...)):
    if not audio.filename:
        raise ApiError("音频文件名为空", code="invalid_audio", status_code=400)

    tmp_dir = settings.upload_dir / "audio_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(audio.filename).suffix or ".wav"
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}{ext}"
    content = await audio.read()
    tmp_path.write_bytes(content)

    try:
        start = time.perf_counter()
        text = await transcribe_audio(str(tmp_path))
        elapsed = round(time.perf_counter() - start, 2)
        return SpeechResponse(text=text, duration_seconds=elapsed)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
