from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .ffmpeg import FFmpegError, extract_frame
from .ocr import OCRResponse, TencentOCRError
from .schemas import (
    CandidateEvidenceInterval,
    EvidenceItem,
    Keyframe,
    RefinementRecord,
    TimeRange,
    Transcript,
    VideoChapterCandidate,
    VideoUnderstandingResult,
    VisualFrameAnalysis,
)
from .utils import compact_timecode, ensure_dir, overlap_seconds
from .vision import BailianVisionError, BailianVisionResponse


FPS_BY_VIDEO_TYPE = {
    "presentation": 2.0,
    "whiteboard": 3.0,
    "operation": 6.0,
    "auto": 1.0,
}

# 抽帧时间戳不能落在视频末尾：仓库里其它抽帧点都用 duration - 0.05 兜住，
# refine 这一处以前没有 —— 末位采样点 == duration 时 ffmpeg 必然失败。
_END_EPSILON_SECONDS = 0.05


class RefinementError(RuntimeError):
    """Base error for local candidate interval refinement."""


class RefinementBudgetError(RefinementError):
    """Raised when a refinement request cannot satisfy its configured budget."""


@dataclass(frozen=True)
class RefinementConfig:
    buffer_seconds: float = 6.0
    presentation_fps: float = 2.0
    whiteboard_fps: float = 3.0
    operation_fps: float = 6.0
    default_fps: float = 1.0
    max_intervals: int = 24
    max_frames_per_interval: int = 120
    output_width: int = 960
    dedup_distance: int = 5
    run_ocr: bool = True
    run_visual: bool = True
    text_density_threshold: float = 0.015
    strict: bool = False

    def fps_for(self, video_type: str) -> float:
        configured = {
            "presentation": self.presentation_fps,
            "whiteboard": self.whiteboard_fps,
            "operation": self.operation_fps,
        }.get(video_type, self.default_fps)
        return configured if configured > 0 else self.default_fps


@dataclass
class RefinementResult:
    keyframes: list[Keyframe] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    records: list[RefinementRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


OCRClientFactory = Callable[[], Any]
VisionClientFactory = Callable[[], Any]
TextDensityDetector = Callable[[Path], float]


def refine_candidate_intervals(
    video_path: str | Path,
    understanding: VideoUnderstandingResult,
    *,
    duration_seconds: float,
    output_dir: str | Path,
    video_type: str = "auto",
    transcript: Transcript | None = None,
    existing_evidence: Iterable[EvidenceItem] = (),
    config: RefinementConfig | None = None,
    ocr_client: Any | None = None,
    vision_client: Any | None = None,
    ocr_client_factory: OCRClientFactory | None = None,
    vision_client_factory: VisionClientFactory | None = None,
    text_density_detector: TextDensityDetector | None = None,
    progress_callback: Callable[..., None] | None = None,
) -> RefinementResult:
    """Run controlled local evidence collection for model candidate intervals.

    This function never promotes a model candidate to a final segment.  It
    returns local keyframes/evidence and auditable refinement records; the
    parser decides later whether evidence is sufficient for segment/IR use.
    """

    config = config or RefinementConfig()
    video_path = Path(video_path).expanduser().resolve()
    if duration_seconds <= 0:
        raise RefinementError("duration_seconds must be positive")
    if not video_path.is_file():
        raise RefinementError(f"video file does not exist: {video_path}")
    if config.buffer_seconds < 0 or config.max_intervals < 0 or config.max_frames_per_interval <= 0:
        raise RefinementError("invalid refinement budget configuration")

    candidates = _flatten_intervals(understanding)
    selected, skipped = _select_candidate_intervals(candidates, config.max_intervals)
    result = RefinementResult()
    if skipped:
        result.warnings.append(
            "Skipped "
            f"{len(skipped)} candidate interval(s) after confidence ranking, overlap suppression "
            "or the max_intervals budget."
        )

    if config.run_ocr and ocr_client is None and ocr_client_factory is not None:
        try:
            ocr_client = ocr_client_factory()
        except Exception as exc:  # noqa: BLE001 - optional local modality.
            result.warnings.append(f"Local OCR client unavailable; refinement continues: {_safe_error(exc)}")
    if config.run_visual and vision_client is None and vision_client_factory is not None:
        try:
            vision_client = vision_client_factory()
        except Exception as exc:  # noqa: BLE001 - optional local modality.
            result.warnings.append(f"Local visual client unavailable; refinement continues: {_safe_error(exc)}")

    for index, (chapter, interval) in enumerate(selected, start=1):
        record, frames, evidence, warnings = _refine_one_interval(
            video_path,
            chapter,
            interval,
            index=index,
            duration_seconds=duration_seconds,
            output_dir=Path(output_dir),
            video_type=video_type,
            transcript=transcript or Transcript(status="not_requested"),
            existing_evidence=list(existing_evidence),
            config=config,
            ocr_client=ocr_client,
            vision_client=vision_client,
            text_density_detector=text_density_detector or estimate_text_density,
            progress_callback=progress_callback,
        )
        result.records.append(record)
        result.keyframes.extend(frames)
        result.evidence.extend(evidence)
        result.warnings.extend(warnings)

    return result


def _refine_one_interval(
    video_path: Path,
    chapter: VideoChapterCandidate,
    interval: CandidateEvidenceInterval,
    *,
    index: int,
    duration_seconds: float,
    output_dir: Path,
    video_type: str,
    transcript: Transcript,
    existing_evidence: list[EvidenceItem],
    config: RefinementConfig,
    ocr_client: Any | None,
    vision_client: Any | None,
    text_density_detector: TextDensityDetector,
    progress_callback: Callable[..., None] | None,
) -> tuple[RefinementRecord, list[Keyframe], list[EvidenceItem], list[str]]:
    del chapter
    refinement_id = f"refinement_{index:03d}_{interval.interval_id}"
    requested_start = max(0.0, min(interval.start_seconds, duration_seconds))
    requested_end = max(0.0, min(interval.end_seconds, duration_seconds))
    warnings: list[str] = []
    if requested_end <= requested_start:
        record = RefinementRecord(
            refinement_id=refinement_id,
            candidate_id=interval.interval_id,
            requested_start_seconds=requested_start,
            requested_end_seconds=max(requested_start + 0.001, requested_end),
            buffered_start_seconds=requested_start,
            buffered_end_seconds=max(requested_start + 0.001, requested_end),
            fps=config.fps_for(video_type),
            status="failed",
            warnings=["candidate interval has no positive duration"],
            review_required=True,
        )
        return record, [], [], record.warnings

    fps = config.fps_for(video_type)
    buffered_start = max(0.0, requested_start - config.buffer_seconds)
    buffered_end = min(duration_seconds, requested_end + config.buffer_seconds)
    # 末位采样点必须严格早于视频结束，否则 -ss 落在末尾导致 FFmpegError。
    buffered_end = max(buffered_start, min(buffered_end, duration_seconds - _END_EPSILON_SECONDS))
    timestamps = plan_refinement_timestamps(
        buffered_start,
        buffered_end,
        fps=fps,
        max_frames=config.max_frames_per_interval,
    )
    frame_dir = ensure_dir(output_dir / refinement_id / "frames")
    frames: list[Keyframe] = []
    extraction_failures = 0
    for frame_index, timestamp in enumerate(timestamps, start=1):
        path = frame_dir / f"frame_{frame_index:04d}.jpg"
        try:
            extract_frame(video_path, timestamp, path, output_width=config.output_width)
        except (FFmpegError, OSError) as exc:
            extraction_failures += 1
            warnings.append(f"frame extraction failed at {timestamp:.3f}s: {_safe_error(exc)}")
            if config.strict:
                raise RefinementError(warnings[-1]) from exc
            continue
        frames.append(
            Keyframe(
                id=f"kf_{refinement_id}_{frame_index:04d}",
                path=path.as_posix(),
                timestamp_seconds=round(timestamp, 3),
                timecode=compact_timecode(timestamp),
                kind="sample",
                reason="candidate_interval_refinement",
                metadata={
                    "refinement_id": refinement_id,
                    "candidate_id": interval.interval_id,
                    "fps": fps,
                    "video_type": video_type,
                    "requested_range": {"start_seconds": requested_start, "end_seconds": requested_end},
                    "buffered_range": {"start_seconds": buffered_start, "end_seconds": buffered_end},
                },
            )
        )

    deduped_frames = deduplicate_frames(frames, max_distance=config.dedup_distance)
    removed = len(frames) - len(deduped_frames)
    if removed:
        warnings.append(f"Removed {removed} visually similar refinement frame(s).")
    frames = deduped_frames
    evidence: list[EvidenceItem] = []
    for frame in frames:
        evidence.append(_keyframe_evidence(frame, refinement_id, interval.interval_id))

    ocr_frames = _select_ocr_frames(frames, text_density_detector, config.text_density_threshold)
    visual_frames = _select_visual_frames(frames)
    ocr_failures = 0
    visual_failures = 0
    ocr_success = 0
    visual_success = 0
    ocr_texts: dict[str, str] = {}

    for frame in ocr_frames if config.run_ocr and ocr_client is not None else []:
        try:
            response = ocr_client.recognize_file(frame.path)
            text, detections, request_id = _ocr_payload(response)
            frame.metadata["ocr"] = {
                "status": "completed" if text else "completed_no_text",
                "text": text,
                "detection_count": len(detections),
                "detections": detections,
                "request_id": request_id,
            }
            ocr_texts[frame.id] = text
            if text:
                ocr_success += 1
                evidence.append(_ocr_evidence(frame, refinement_id, interval.interval_id, text, detections, request_id))
        except (TencentOCRError, OSError, ValueError) as exc:
            ocr_failures += 1
            frame.metadata["ocr"] = {"status": "failed", "error": _safe_error(exc)}
            warnings.append(f"OCR failed for {frame.id}; frame retained: {_safe_error(exc)}")

    for frame in visual_frames if config.run_visual and vision_client is not None else []:
        context = _transcript_context(transcript, frame.timestamp_seconds)
        ocr_text = str(frame.metadata.get("ocr", {}).get("text", ""))
        try:
            response = vision_client.analyze_file(
                frame.path,
                video_type=video_type,
                timecode=frame.timecode,
                transcript_context=context,
                ocr_text=ocr_text,
            )
            model, request_id, usage, analysis = _visual_payload(response)
            frame.metadata["visual"] = {
                "status": "completed",
                "provider": "aliyun_bailian",
                "model": model,
                "request_id": request_id,
                "usage": usage,
                "analysis": analysis,
            }
            visual_success += 1
            evidence.append(_visual_evidence(frame, refinement_id, interval.interval_id, model, request_id, usage, analysis))
        except (BailianVisionError, OSError, ValueError) as exc:
            visual_failures += 1
            frame.metadata["visual"] = {"status": "failed", "error": _safe_error(exc)}
            warnings.append(f"Local visual verification failed for {frame.id}; frame retained: {_safe_error(exc)}")

    evidence_timestamps = [
        item.time_range.start_seconds
        for item in evidence
        if item.time_range is not None and item.evidence_type in {"ocr", "visual"}
    ]
    # 形参承诺的"与已有证据对齐"必须真的发生：把同一窗口内已存在的证据时间戳
    # 一起纳入 refined range，否则局部二次取证的结果会与全局证据时间轴脱节。
    for item in existing_evidence or []:
        time_range = getattr(item, "time_range", None)
        if time_range is None:
            continue
        start_value = float(time_range.start_seconds)
        if buffered_start <= start_value <= buffered_end:
            evidence_timestamps.append(start_value)
    refined_start, refined_end = _refined_range(
        evidence_timestamps,
        requested_start=requested_start,
        requested_end=requested_end,
        buffered_start=buffered_start,
        buffered_end=buffered_end,
        fps=fps,
    )
    evidence_ids = [item.id for item in evidence if item.evidence_type in {"ocr", "visual"}]
    requested_modalities = int(config.run_ocr and ocr_client is not None) + int(config.run_visual and vision_client is not None)
    if extraction_failures and not frames:
        status = "failed"
    elif requested_modalities == 0:
        status = "partial"
        warnings.append("No local OCR or visual verifier is configured for this candidate interval.")
    elif requested_modalities and not evidence_ids:
        status = "partial"
        warnings.append("No OCR or local visual evidence was produced for this candidate interval.")
    elif ocr_failures or visual_failures:
        status = "partial"
    else:
        status = "completed"
    review_required = bool(
        not evidence_ids
        or extraction_failures
        or ocr_failures
        or visual_failures
        or (refined_start is None or refined_end is None)
    )
    record = RefinementRecord(
        refinement_id=refinement_id,
        candidate_id=interval.interval_id,
        requested_start_seconds=round(requested_start, 3),
        requested_end_seconds=round(requested_end, 3),
        buffered_start_seconds=round(buffered_start, 3),
        buffered_end_seconds=round(buffered_end, 3),
        refined_start_seconds=refined_start,
        refined_end_seconds=refined_end,
        fps=fps,
        frame_count=len(frames),
        ocr_frame_count=len(ocr_frames) if config.run_ocr and ocr_client is not None else 0,
        visual_frame_count=len(visual_frames) if config.run_visual and vision_client is not None else 0,
        evidence_ids=evidence_ids,
        status=status,
        warnings=warnings,
        review_required=review_required,
    )
    _report_progress(
        progress_callback,
        "refinement",
        f"候选区间 {interval.interval_id} 局部取证完成",
        {
            "refinement_id": refinement_id,
            "candidate_id": interval.interval_id,
            "status": status,
            "frame_count": len(frames),
            "ocr_frame_count": record.ocr_frame_count,
            "visual_frame_count": record.visual_frame_count,
            "evidence_count": len(evidence_ids),
            "review_required": review_required,
        },
    )
    return record, frames, evidence, warnings


def plan_refinement_timestamps(
    start_seconds: float,
    end_seconds: float,
    *,
    fps: float,
    max_frames: int,
) -> list[float]:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise RefinementBudgetError("refinement range must have end_seconds > start_seconds")
    if fps <= 0 or max_frames <= 0:
        raise RefinementBudgetError("fps and max_frames must be positive")
    count = max(1, int(math.ceil((end_seconds - start_seconds) * fps)) + 1)
    if count <= max_frames:
        values = [min(end_seconds, start_seconds + index / fps) for index in range(count)]
    else:
        step = (end_seconds - start_seconds) / max(1, max_frames - 1)
        values = [start_seconds + index * step for index in range(max_frames)]
        values[-1] = end_seconds
    result: list[float] = []
    for value in values:
        rounded = round(min(end_seconds, max(start_seconds, value)), 3)
        if not result or abs(rounded - result[-1]) >= 0.001:
            result.append(rounded)
    return result[:max_frames]


def deduplicate_frames(frames: Iterable[Keyframe], *, max_distance: int = 5) -> list[Keyframe]:
    selected: list[Keyframe] = []
    hashes: list[tuple[str, int | None]] = []
    for frame in frames:
        signature = _perceptual_signature(Path(frame.path))
        if any(_signature_distance(signature, existing) <= max_distance for existing in hashes):
            continue
        selected.append(frame)
        hashes.append(signature)
    return selected


def estimate_text_density(path: Path) -> float:
    """Return a small, deterministic text-density heuristic for OCR gating."""

    try:
        from PIL import Image, ImageFilter, ImageOps

        image = ImageOps.grayscale(Image.open(path)).resize((160, 90))
        edges = image.filter(ImageFilter.FIND_EDGES)
        # FIND_EDGES 对图像外部做零填充，会在最外一圈产生假的强边缘：
        # 纯白空白帧因此被算成约 3.4% 的"文本密度"（正好是最外一圈的像素占比），
        # 门槛形同虚设、空白帧全量进入付费 OCR。裁掉这一圈再统计。
        width, height = edges.size
        if width > 2 and height > 2:
            edges = edges.crop((1, 1, width - 1, height - 1))
        pixels = list(edges.getdata())
        if not pixels:
            return 0.0
        high_edges = sum(1 for value in pixels if value >= 80)
        return high_edges / len(pixels)
    except (OSError, ValueError, ImportError):
        return 0.0


def _flatten_intervals(
    understanding: VideoUnderstandingResult,
) -> list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]]:
    return [(chapter, interval) for chapter in understanding.chapters for interval in chapter.candidate_intervals]


def _select_candidate_intervals(
    candidates: list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]],
    max_intervals: int,
) -> tuple[list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]], list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]]]:
    """Select high-value local checks without letting duplicate model ranges consume the budget.

    qwen returns candidates in model order, which is useful for display but not
    a quality ranking.  Refinement is an evidence budget, so prioritize the
    interval confidence first and use the chapter confidence as a tie-breaker.
    Near-identical ranges are refined once; the selected set is returned in
    chronological order so downstream evidence remains easy to inspect.
    """

    if max_intervals <= 0:
        return [], list(candidates)

    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item[1].confidence),
            -float(item[0].confidence),
            float(item[1].start_seconds),
            float(item[1].end_seconds),
            item[1].interval_id,
        ),
    )
    selected: list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]] = []
    skipped: list[tuple[VideoChapterCandidate, CandidateEvidenceInterval]] = []
    for candidate in ranked:
        if any(_ranges_are_redundant(candidate[1], chosen[1]) for chosen in selected):
            skipped.append(candidate)
            continue
        if len(selected) >= max_intervals:
            skipped.append(candidate)
            continue
        selected.append(candidate)

    selected.sort(key=lambda item: (float(item[1].start_seconds), float(item[1].end_seconds), item[1].interval_id))
    return selected, skipped


def _ranges_are_redundant(left: CandidateEvidenceInterval, right: CandidateEvidenceInterval) -> bool:
    left_duration = max(0.001, float(left.end_seconds) - float(left.start_seconds))
    right_duration = max(0.001, float(right.end_seconds) - float(right.start_seconds))
    overlap = overlap_seconds(
        float(left.start_seconds),
        float(left.end_seconds),
        float(right.start_seconds),
        float(right.end_seconds),
    )
    shorter = min(left_duration, right_duration)
    return overlap / shorter >= 0.85


def _select_ocr_frames(frames: list[Keyframe], detector: TextDensityDetector, threshold: float) -> list[Keyframe]:
    if not frames:
        return []
    selected: list[Keyframe] = []
    for index, frame in enumerate(frames):
        density = detector(Path(frame.path))
        frame.metadata["text_density"] = round(float(density), 6)
        if index in {0, len(frames) - 1} or density >= threshold:
            selected.append(frame)
    return selected


def _select_visual_frames(frames: list[Keyframe]) -> list[Keyframe]:
    if len(frames) <= 8:
        return list(frames)
    indexes = {0, len(frames) - 1}
    step = max(1, len(frames) // 6)
    indexes.update(range(0, len(frames), step))
    return [frame for index, frame in enumerate(frames) if index in indexes]


def _refined_range(
    timestamps: list[float],
    *,
    requested_start: float,
    requested_end: float,
    buffered_start: float,
    buffered_end: float,
    fps: float,
) -> tuple[float | None, float | None]:
    if not timestamps:
        return None, None
    half_step = 0.5 / fps
    start = max(buffered_start, min(requested_end, min(timestamps) - half_step))
    end = min(buffered_end, max(requested_start, max(timestamps) + half_step))
    if end <= start:
        return None, None
    return round(start, 3), round(end, 3)


def _keyframe_evidence(frame: Keyframe, refinement_id: str, candidate_id: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_keyframe_{frame.id}",
        evidence_type="keyframe",
        source_id=frame.id,
        source_path=frame.path,
        time_range=TimeRange(
            start_seconds=frame.timestamp_seconds,
            end_seconds=frame.timestamp_seconds,
            start=frame.timecode,
            end=frame.timecode,
        ),
        content=f"局部取证帧 {frame.timecode}",
        metadata={"refinement_id": refinement_id, "candidate_id": candidate_id, "reason": frame.reason},
    )


def _ocr_evidence(
    frame: Keyframe,
    refinement_id: str,
    candidate_id: str,
    text: str,
    detections: list[dict[str, Any]],
    request_id: str | None,
) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_ocr_{frame.id}",
        evidence_type="ocr",
        source_id=frame.id,
        source_path=frame.path,
        time_range=TimeRange(
            start_seconds=frame.timestamp_seconds,
            end_seconds=frame.timestamp_seconds,
            start=frame.timecode,
            end=frame.timecode,
        ),
        content=text,
        metadata={
            "refinement_id": refinement_id,
            "candidate_id": candidate_id,
            "request_id": request_id,
            "detections": detections,
        },
    )


def _visual_evidence(
    frame: Keyframe,
    refinement_id: str,
    candidate_id: str,
    model: str,
    request_id: str | None,
    usage: dict[str, int | float],
    analysis: dict[str, Any],
) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_visual_{frame.id}",
        evidence_type="visual",
        source_id=frame.id,
        source_path=frame.path,
        time_range=TimeRange(
            start_seconds=frame.timestamp_seconds,
            end_seconds=frame.timestamp_seconds,
            start=frame.timecode,
            end=frame.timecode,
        ),
        content=str(analysis.get("summary") or f"局部视觉验证帧 {frame.timecode}"),
        metadata={
            "refinement_id": refinement_id,
            "candidate_id": candidate_id,
            "provider": "aliyun_bailian",
            "model": model,
            "request_id": request_id,
            "usage": usage,
            "analysis": analysis,
        },
    )


def _ocr_payload(response: Any) -> tuple[str, list[dict[str, Any]], str | None]:
    if isinstance(response, OCRResponse):
        return response.text, response.detections, response.request_id
    if isinstance(response, Mapping):
        return str(response.get("text") or ""), list(response.get("detections") or []), response.get("request_id")
    raise ValueError("OCR client returned an unsupported response")


def _visual_payload(response: Any) -> tuple[str, str | None, dict[str, int | float], dict[str, Any]]:
    if isinstance(response, BailianVisionResponse):
        return (
            response.model,
            response.request_id,
            response.usage,
            response.analysis.model_dump(mode="json"),
        )
    if isinstance(response, Mapping):
        analysis = response.get("analysis")
        if isinstance(analysis, VisualFrameAnalysis):
            analysis = analysis.model_dump(mode="json")
        if not isinstance(analysis, Mapping):
            analysis = {}
        return (
            str(response.get("model") or "unknown"),
            response.get("request_id"),
            dict(response.get("usage") or {}),
            dict(analysis),
        )
    raise ValueError("visual client returned an unsupported response")


def _transcript_context(transcript: Transcript, timestamp: float, window_seconds: float = 6.0) -> str:
    return " ".join(
        segment.text
        for segment in transcript.segments
        if segment.start_seconds <= timestamp + window_seconds and segment.end_seconds >= timestamp - window_seconds
    )


def _perceptual_signature(path: Path) -> tuple[str, int | None]:
    try:
        from PIL import Image, ImageOps

        image = ImageOps.grayscale(Image.open(path)).resize((16, 16))
        pixels = list(image.getdata())
        average = sum(pixels) / max(1, len(pixels))
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return bits, None
    except (OSError, ValueError, ImportError):
        return hashlib.sha256(path.read_bytes()).hexdigest(), None


def _signature_distance(left: tuple[str, int | None], right: tuple[str, int | None]) -> int:
    left_bits, _ = left
    right_bits, _ = right
    if len(left_bits) != len(right_bits):
        return 10**9
    return sum(a != b for a, b in zip(left_bits, right_bits))


def _safe_error(exc: Exception) -> str:
    return re.sub(r"(?i)(bearer\s+|sk-[a-z0-9_-]{8,})[^\s,;]*", "[redacted]", str(exc))[:300]


def _report_progress(callback: Callable[..., None] | None, phase: str, message: str, details: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(phase, message, details)
    except TypeError:
        callback(phase, message, 0, details)
