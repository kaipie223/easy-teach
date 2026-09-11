from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .schemas import AudioStreamMetadata, VideoMetadata, VideoStreamMetadata
from .utils import ensure_dir, sample_timestamps


class FFmpegError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot process a video."""


def probe_video(video_path: Path) -> VideoMetadata:
    payload = _run_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
    )
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})

    video_streams = [
        _video_stream_metadata(stream)
        for stream in streams
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        _audio_stream_metadata(stream)
        for stream in streams
        if stream.get("codec_type") == "audio"
    ]
    duration = (
        _as_float(format_info.get("duration"))
        or next((stream.duration_seconds for stream in video_streams if stream.duration_seconds), None)
        or 0.0
    )
    return VideoMetadata(
        format_name=format_info.get("format_name"),
        format_long_name=format_info.get("format_long_name"),
        duration_seconds=duration,
        size_bytes=_as_int(format_info.get("size")) or (video_path.stat().st_size if video_path.exists() else None),
        bit_rate=_as_int(format_info.get("bit_rate")),
        video_streams=video_streams,
        audio_streams=audio_streams,
        raw_format_tags={str(k): str(v) for k, v in (format_info.get("tags") or {}).items()},
    )


def extract_audio(video_path: Path, output_dir: Path) -> Path:
    ensure_dir(output_dir)
    audio_path = output_dir / f"{video_path.stem}.wav"
    _run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    if not audio_path.exists():
        raise FFmpegError("Audio extraction did not create a wav file.")
    return audio_path


def extract_video_segment(
    video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> Path:
    """Create an independently readable local video segment.

    Chunked video-understanding requests must receive the actual local chunk,
    not only a prompt-level timestamp offset.  Re-encoding at the boundary is
    intentional: stream-copy seeking can start at an earlier keyframe and
    would make the model's local timestamps disagree with the requested
    interval.  The original source is never modified.
    """

    if start_seconds < 0 or end_seconds <= start_seconds:
        raise FFmpegError("video segment must have a positive, non-negative range")
    if not video_path.is_file():
        raise FFmpegError(f"Video segment source does not exist: {video_path}")

    ensure_dir(output_path.parent)
    duration_seconds = end_seconds - start_seconds
    requested_duration = duration_seconds
    for attempt in range(3):
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                # Trim both streams after the accurate input seek.  The
                # explicit filters avoid stream-copy keyframe drift; the
                # duration probe below catches codec/container tail padding.
                "-vf",
                f"trim=duration={duration_seconds:.3f},setpts=PTS-STARTPTS",
                "-af",
                f"atrim=duration={duration_seconds:.3f},asetpts=PTS-STARTPTS",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                str(output_path),
            ]
        )
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise FFmpegError(f"Video segment extraction did not create {output_path}.")
        output_metadata = probe_video(output_path)
        actual_duration = output_metadata.duration_seconds
        if not output_metadata.video_streams or actual_duration <= 0:
            raise FFmpegError(f"Video segment has no usable video stream: {output_path}")
        if actual_duration <= requested_duration + 0.01:
            break
        # AAC/container priming can add a small tail after the requested
        # window.  Tighten the next encode target by the observed excess and
        # a small muxing margin; never let a chunk exceed its requested range.
        duration_seconds = max(0.05, duration_seconds - (actual_duration - requested_duration) - 0.02)
    else:
        # Some codecs quantize the final packet to a fixed time base.  A
        # final sub-window retry keeps the hard upper bound while still
        # producing a useful, independently readable segment.
        final_duration = max(0.05, duration_seconds - 0.05)
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{final_duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                str(output_path),
            ]
        )
        output_metadata = probe_video(output_path)
        actual_duration = output_metadata.duration_seconds
        if not output_metadata.video_streams or actual_duration <= 0:
            raise FFmpegError(f"Video segment has no usable video stream: {output_path}")
        if actual_duration > requested_duration + 0.01:
            raise FFmpegError(
                f"Video segment duration exceeded requested range: {actual_duration:.3f}s > {requested_duration:.3f}s"
            )
    return output_path


def extract_sample_keyframes(
    video_path: Path,
    output_dir: Path,
    duration_seconds: float,
    max_frames: int,
    output_width: int = 960,
) -> list[tuple[float, Path]]:
    ensure_dir(output_dir)
    for old_frame in output_dir.glob("sample_*.jpg"):
        old_frame.unlink()

    frames: list[tuple[float, Path]] = []
    for index, timestamp in enumerate(sample_timestamps(duration_seconds, max_frames), start=1):
        frame_path = output_dir / f"sample_{index:03d}.jpg"
        extract_frame(video_path, timestamp, frame_path, output_width=output_width)
        if frame_path.exists():
            frames.append((timestamp, frame_path))
    return frames


def extract_frame(video_path: Path, timestamp_seconds: float, frame_path: Path, output_width: int = 960) -> Path:
    ensure_dir(frame_path.parent)
    _run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, timestamp_seconds):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-vf",
            f"scale={output_width}:-2",
            str(frame_path),
        ]
    )
    if not frame_path.exists():
        raise FFmpegError(f"Frame extraction did not create {frame_path}.")
    return frame_path


def _video_stream_metadata(stream: dict[str, Any]) -> VideoStreamMetadata:
    return VideoStreamMetadata(
        index=_as_int(stream.get("index")),
        codec_name=stream.get("codec_name"),
        width=_as_int(stream.get("width")),
        height=_as_int(stream.get("height")),
        fps=_parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        pix_fmt=stream.get("pix_fmt"),
        bit_rate=_as_int(stream.get("bit_rate")),
        duration_seconds=_as_float(stream.get("duration")),
    )


def _audio_stream_metadata(stream: dict[str, Any]) -> AudioStreamMetadata:
    tags = stream.get("tags") or {}
    return AudioStreamMetadata(
        index=_as_int(stream.get("index")),
        codec_name=stream.get("codec_name"),
        sample_rate=_as_int(stream.get("sample_rate")),
        channels=_as_int(stream.get("channels")),
        bit_rate=_as_int(stream.get("bit_rate")),
        duration_seconds=_as_float(stream.get("duration")),
        language=tags.get("language"),
    )


def _run_json(command: list[str]) -> dict[str, Any]:
    completed = _run_command(command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON.") from exc


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise FFmpegError("ffmpeg/ffprobe is not available on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "ffmpeg command failed.").strip()
        raise FFmpegError(message) from exc


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_fps(value: object) -> float | None:
    if not value:
        return None
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None
