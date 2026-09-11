"""M4 — 语音转写 API"""

import uuid
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session as DBSession

from backend.config import settings
from backend.core.errors import ApiError
from backend.core.ownership import get_session_for_user
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.user import User
from backend.schemas import SpeechResponse
from backend.services.speech import transcribe_audio
from backend.services.uploads import UploadSizeExceeded, stream_upload_to_path

router = APIRouter()
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}


@router.post("/transcribe", response_model=SpeechResponse)
async def transcribe(
    audio: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    db: DBSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not audio.filename:
        raise ApiError("音频文件名为空", code="invalid_audio", status_code=400)
    if session_id:
        get_session_for_user(db, session_id, user)

    tmp_dir = settings.upload_dir / "audio_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(audio.filename).suffix.lower() or ".wav"
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise ApiError(
            "不支持的音频格式",
            code="UNSUPPORTED_AUDIO_FORMAT",
            status_code=422,
            details={"allowed": sorted(ALLOWED_AUDIO_EXTENSIONS)},
        )
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}{ext}"
    try:
        await stream_upload_to_path(
            audio,
            tmp_path,
            max_bytes=settings.speech_max_upload_size_mb * 1024 * 1024,
        )
    except UploadSizeExceeded as exc:
        raise ApiError(
            f"音频超过 {settings.speech_max_upload_size_mb} MB 限制",
            code="AUDIO_TOO_LARGE",
            status_code=413,
            details={"max_bytes": exc.max_bytes, "actual_bytes": exc.actual_bytes},
        ) from exc

    try:
        start = time.perf_counter()
        text = await transcribe_audio(str(tmp_path))
        elapsed = round(time.perf_counter() - start, 2)
        return SpeechResponse(text=text, duration_seconds=elapsed, session_id=session_id)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
