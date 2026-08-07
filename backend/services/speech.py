"""M4 — 语音识别（faster-whisper）"""

import logging

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_path: str) -> str:
    """使用 faster-whisper 将音频转录为文本。

    Args:
        audio_path: 音频文件路径

    Returns:
        转录文本
    """
    try:
        from faster_whisper import WhisperModel

        model_size = "small"
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, beam_size=5)
        text = " ".join(seg.text for seg in segments)
        return text.strip() or "[未识别到语音内容]"
    except ImportError:
        logger.warning("faster-whisper not installed")
        return "[语音识别需要 faster-whisper 库]"
    except Exception:
        logger.exception("Speech transcription failed")
        return "[语音转写失败，请重试]"
