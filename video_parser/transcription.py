from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from .schemas import Transcript, TranscriptSegment
from .text_normalization import normalization_available, to_simplified_chinese
from .utils import compact_timecode, ensure_dir, json_path

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Raised when faster-whisper cannot transcribe audio."""


# 手工文本没有真实时间轴时使用的占位长度（秒）：只用于满足"转写段必须正长度"
# 这一不变量，不代表任何真实时间信息。
MANUAL_TEXT_NOMINAL_SECONDS = 0.001


@lru_cache(maxsize=4)
def load_whisper_model(model_size: str, device: str = "cpu", compute_type: str = "int8"):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("faster-whisper is not installed.") from exc
    try:
        return WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001
        # 模型名非法（ValueError）/ 本地缺失或下载失败（OSError 及其子类，含
        # huggingface_hub 的 LocalEntryNotFoundError）都必须降级为 TranscriptionError，
        # 交给 parser 侧追加 warning 并继续，而不是让整次解析任务崩溃。
        raise TranscriptionError(f"whisper 模型加载失败：{exc}") from exc


def transcribe_audio(
    audio_path: Path,
    output_dir: Path,
    model_size: str = "base",
    language: str | None = "zh",
    beam_size: int = 5,
    device: str = "cpu",
    compute_type: str = "int8",
) -> Transcript:
    ensure_dir(output_dir)
    model = load_whisper_model(model_size, device=device, compute_type=compute_type)
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language or None,
            beam_size=beam_size,
            vad_filter=True,
        )
        segments = []
        for index, segment in enumerate(segments_iter, start=1):
            raw_text = segment.text.strip()
            if not raw_text:
                continue
            try:
                # 逐段收集：单个分段失败只跳过该段并记 warning，
                # 已经跑完的 Whisper 计算不该整段丢弃。
                normalized_text = to_simplified_chinese(raw_text)
                segments.append(
                    TranscriptSegment(
                        id=f"tr_{index:04d}",
                        start_seconds=float(segment.start),
                        end_seconds=float(segment.end),
                        start=compact_timecode(float(segment.start)),
                        end=compact_timecode(float(segment.end)),
                        text=normalized_text,
                        raw_text=raw_text if raw_text != normalized_text else None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("跳过转写分段 tr_%04d：%s", index, exc)
    except Exception as exc:  # noqa: BLE001 - wrapped for parser warnings.
        raise TranscriptionError(str(exc)) from exc

    text = "\n".join(f"[{segment.start}-{segment.end}] {segment.text}" for segment in segments)
    raw_text = "\n".join(
        f"[{segment.start}-{segment.end}] {segment.raw_text or segment.text}"
        for segment in segments
    )
    text_path = output_dir / f"{audio_path.stem}.txt"
    transcript_json_path = output_dir / f"{audio_path.stem}.json"
    text_path.write_text(text, encoding="utf-8")

    transcript = Transcript(
        source_audio_path=json_path(audio_path),
        text_path=json_path(text_path),
        json_path=json_path(transcript_json_path),
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
        duration_seconds=getattr(info, "duration", None),
        text=text,
        raw_text=raw_text,
        text_normalization="simplified_chinese" if normalization_available() else "none",
        segments=segments,
        status="completed",
    )
    transcript_json_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return transcript


def transcript_from_manual_text(text: str, *, duration_seconds: float | None = None) -> Transcript:
    normalized = text.strip()
    if not normalized:
        return Transcript(status="not_requested")
    simplified = to_simplified_chinese(normalized)
    # 手工文本没有真实时间轴，但转写段必须满足"正长度"这一不变量：
    # _normalize_asr_segments 与 schemas 的区间约束都要求 end > start，
    # 主路径（ASR）同样如此；以前这里写死 0.0/0.0，两条路径标准不一致。
    # 调用方若知道真实时长可以传入，否则只给一个最小正长度占位，
    # 不编造一个看起来可信的时间轴。
    end_seconds = (
        float(duration_seconds)
        if duration_seconds is not None and duration_seconds > 0
        else MANUAL_TEXT_NOMINAL_SECONDS
    )
    segment = TranscriptSegment(
        id="tr_0001",
        start_seconds=0.0,
        end_seconds=end_seconds,
        start=compact_timecode(0.0),
        end=compact_timecode(end_seconds),
        text=simplified,
        raw_text=normalized if normalized != simplified else None,
    )
    return Transcript(
        text=simplified,
        raw_text=normalized,
        text_normalization="simplified_chinese" if normalization_available() else "none",
        segments=[segment],
        status="completed",
    )


def load_transcript(json_file: Path) -> Transcript:
    return Transcript.model_validate(json.loads(json_file.read_text(encoding="utf-8")))
