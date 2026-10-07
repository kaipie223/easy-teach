"""M4 — 语音识别（faster-whisper）"""

import asyncio
from ai.speech.transcriber import SpeechTranscriber

_transcriber: SpeechTranscriber | None = None

# WhisperModel 是进程内单例：并发调用不保证线程安全，且每次推理各自开满 CPU
# 线程互相抢占。锁只包住"取模型 + 转写"，等待期间不占线程池线程；顺带堵住
# 惰性初始化的双重加载。注意这是进程内的锁，多 worker 部署时按 worker 各自串行。
_transcribe_lock = asyncio.Lock()


def _get_transcriber() -> SpeechTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = SpeechTranscriber(model_size="small")
    return _transcriber


async def transcribe_audio(audio_path: str) -> str:
    async with _transcribe_lock:
        t = _get_transcriber()
        return await asyncio.to_thread(t.transcribe, audio_path)
