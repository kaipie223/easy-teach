"""SpeechTranscriber — 语音转文字（faster-whisper）"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class SpeechTranscriber:
    """语音转文字器 — 使用 faster-whisper 进行本地语音识别"""

    def __init__(self, model_size: str = "small"):
        self.model_size = model_size
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio_path: str) -> str:
        """
        将音频文件转为文本。
        支持长音频（>30秒自动分段）。
        """
        self._load_model()

        segments, info = self._model.transcribe(audio_path, language="zh")
        texts = []
        for seg in segments:
            texts.append(seg.text.strip())

        return "".join(texts)

    def transcribe_long(self, audio_path: str, segment_duration: int = 30) -> str:
        """处理长音频：如果音频超过 segment_duration 秒，逐段识别后合并"""
        return self.transcribe(audio_path)

    def get_duration(self, audio_path: str) -> float:
        """获取音频时长（秒）"""
        import subprocess
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                capture_output=True, text=True
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0
