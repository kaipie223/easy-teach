from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .utils import sample_timestamps


class KeyframeStrategyError(RuntimeError):
    """Raised when a profile-specific keyframe analysis cannot run."""


@dataclass(frozen=True)
class KeyframeStrategyConfig:
    sample_fps: float = 1.0
    analysis_width: int = 320
    change_threshold: float = 0.16
    min_gap_seconds: float = 4.0
    max_frames: int = 12


@dataclass(frozen=True)
class PlannedKeyframe:
    timestamp_seconds: float
    reason: str
    score: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _VisualSignal:
    timestamp_seconds: float
    score: float


def plan_keyframes(
    video_path: Path,
    duration_seconds: float,
    video_type: str,
    config: KeyframeStrategyConfig | None = None,
) -> list[PlannedKeyframe]:
    """Plan keyframe timestamps without writing media files.

    The three first-version profiles intentionally share one candidate pipeline:
    temporal coverage is always preserved, while each profile changes which
    visual signal receives priority. OCR and VLM signals can be added later
    without changing the output contract.
    """
    config = config or KeyframeStrategyConfig()
    if duration_seconds <= 0 or config.max_frames <= 0:
        return []
    if video_type not in {"presentation", "whiteboard", "operation"}:
        return _coverage_only(duration_seconds, config.max_frames, "generic_uniform_coverage")

    signals = _read_visual_signals(video_path, duration_seconds, config)
    if video_type == "presentation":
        return _presentation_plan(duration_seconds, signals, config)
    if video_type == "whiteboard":
        return _whiteboard_plan(duration_seconds, signals, config)
    return _operation_plan(duration_seconds, signals, config)


def _presentation_plan(
    duration_seconds: float,
    signals: list[_VisualSignal],
    config: KeyframeStrategyConfig,
) -> list[PlannedKeyframe]:
    coverage = _coverage_only(duration_seconds, config.max_frames, "presentation_uniform_coverage")
    changes = _peak_candidates(
        signals,
        threshold=config.change_threshold,
        min_gap_seconds=config.min_gap_seconds,
        limit=config.max_frames,
        reason="presentation_visual_state_change",
    )
    return _merge_candidates(coverage, changes, duration_seconds, config)


def _whiteboard_plan(
    duration_seconds: float,
    signals: list[_VisualSignal],
    config: KeyframeStrategyConfig,
) -> list[PlannedKeyframe]:
    coverage = _coverage_only(duration_seconds, config.max_frames, "whiteboard_uniform_coverage")
    # Writing is a local, cumulative change and can be weaker than a full slide
    # transition, so the profile deliberately lowers the threshold.
    changes = _peak_candidates(
        signals,
        threshold=max(0.07, config.change_threshold * 0.55),
        min_gap_seconds=max(3.0, config.min_gap_seconds),
        limit=config.max_frames,
        reason="whiteboard_content_state_change",
    )
    return _merge_candidates(coverage, changes, duration_seconds, config)


def _operation_plan(
    duration_seconds: float,
    signals: list[_VisualSignal],
    config: KeyframeStrategyConfig,
) -> list[PlannedKeyframe]:
    boundaries = [
        PlannedKeyframe(timestamp_seconds=0.0, reason="operation_start_boundary", score=None),
        PlannedKeyframe(
            timestamp_seconds=max(0.0, duration_seconds - 0.05),
            reason="operation_end_boundary",
            score=None,
        ),
    ]
    peaks = _peak_candidates(
        signals,
        threshold=max(0.09, config.change_threshold * 0.75),
        min_gap_seconds=max(2.5, config.min_gap_seconds * 0.75),
        limit=config.max_frames,
        reason="operation_motion_or_state_peak",
    )
    return _merge_candidates(boundaries, peaks, duration_seconds, config)


def _coverage_only(duration_seconds: float, max_frames: int, reason: str) -> list[PlannedKeyframe]:
    return [
        PlannedKeyframe(timestamp_seconds=timestamp, reason=reason, score=None)
        for timestamp in sample_timestamps(duration_seconds, max_frames)
    ]


def _peak_candidates(
    signals: list[_VisualSignal],
    threshold: float,
    min_gap_seconds: float,
    limit: int,
    reason: str,
) -> list[PlannedKeyframe]:
    selected: list[_VisualSignal] = []
    for signal in sorted(signals, key=lambda item: item.score, reverse=True):
        if signal.score < threshold:
            break
        if any(abs(signal.timestamp_seconds - item.timestamp_seconds) < min_gap_seconds for item in selected):
            continue
        selected.append(signal)
        if len(selected) >= limit:
            break
    return [
        PlannedKeyframe(
            timestamp_seconds=signal.timestamp_seconds,
            reason=reason,
            score=round(signal.score, 4),
            metadata={"signal": "frame_change"},
        )
        for signal in sorted(selected, key=lambda item: item.timestamp_seconds)
    ]


def _merge_candidates(
    coverage: list[PlannedKeyframe],
    changes: list[PlannedKeyframe],
    duration_seconds: float,
    config: KeyframeStrategyConfig,
) -> list[PlannedKeyframe]:
    candidates = [*changes, *coverage]
    candidates.sort(key=lambda item: (item.score is None, -(item.score or 0.0), item.timestamp_seconds))
    selected: list[PlannedKeyframe] = []
    for candidate in candidates:
        timestamp = min(max(0.0, candidate.timestamp_seconds), max(0.0, duration_seconds - 0.05))
        if any(abs(timestamp - item.timestamp_seconds) < config.min_gap_seconds for item in selected):
            continue
        selected.append(
            PlannedKeyframe(
                timestamp_seconds=round(timestamp, 3),
                reason=candidate.reason,
                score=candidate.score,
                metadata=candidate.metadata,
            )
        )
        if len(selected) >= config.max_frames:
            break

    # A very low-change video still needs temporal coverage. Fill remaining
    # slots from uniform timestamps after prioritizing visual change candidates.
    for candidate in coverage:
        if len(selected) >= config.max_frames:
            break
        if any(abs(candidate.timestamp_seconds - item.timestamp_seconds) < config.min_gap_seconds for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected[: config.max_frames], key=lambda item: item.timestamp_seconds)


def _read_visual_signals(video_path: Path, duration_seconds: float, config: KeyframeStrategyConfig) -> list[_VisualSignal]:
    cv2 = _load_cv2()
    if config.sample_fps <= 0:
        raise KeyframeStrategyError("Keyframe sample_fps must be greater than zero.")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise KeyframeStrategyError(f"OpenCV cannot open video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    # NaN / inf 是真值，会被 `or` 原样保留，随后 int(round(...)) 对 NaN 抛
    # ValueError、对 inf 抛 OverflowError —— 两者都不在调用方的捕获列表里，
    # 非 strict 模式也会整体失败。只接受有限且为正的帧率。
    if not math.isfinite(fps) or fps <= 0:
        fps = 25.0
    sample_step = max(1, int(round(fps / config.sample_fps)))
    frame_index = 0
    previous = None
    signals: list[_VisualSignal] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % sample_step != 0:
                frame_index += 1
                continue

            timestamp = min(duration_seconds, frame_index / fps)
            prepared = _prepare_frame(frame, config.analysis_width, cv2)
            if previous is not None:
                pixel_delta = cv2.absdiff(prepared, previous).mean() / 255.0
                previous_gray = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
                current_gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
                gray_delta = cv2.absdiff(current_gray, previous_gray).mean() / 255.0
                score = (pixel_delta * 0.45) + (gray_delta * 0.55)
                signals.append(_VisualSignal(timestamp_seconds=timestamp, score=float(score)))
            previous = prepared
            frame_index += 1
    finally:
        capture.release()
    return signals


def _prepare_frame(frame, target_width: int, cv2):
    height, width = frame.shape[:2]
    if width > target_width:
        target_height = max(2, int(height * (target_width / width)))
        frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(frame, (5, 5), 0)


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise KeyframeStrategyError("OpenCV is not installed; profile-specific keyframes are unavailable.") from exc
    return cv2
