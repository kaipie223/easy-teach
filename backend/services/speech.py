"""M4 — 语音识别（faster-whisper）"""

import asyncio
from ai.speech.transcriber import SpeechTranscriber

_transcriber: SpeechTranscriber | None = None


def _get_transcriber() -> SpeechTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = SpeechTranscriber(model_size="small")
    return _transcriber


async def transcribe_audio(audio_path: str) -> str:
    t = _get_transcriber()
    return await asyncio.to_thread(t.transcribe, audio_path)
