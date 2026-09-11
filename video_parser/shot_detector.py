from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .utils import ensure_dir


class ShotDetectionError(RuntimeError):
    """Raised when OpenCV cannot read or split a video."""


@dataclass(frozen=True)
class ShotDetectionConfig:
    sample_fps: float = 2.0
    threshold: float = 0.34
    min_shot_seconds: float = 1.0
    max_shots: int = 36
    analysis_width: int = 320
    output_width: int = 960


@dataclass(frozen=True)
class ShotSegmentData:
    shot_id: str
    index: int
    start_seconds: float
    end_seconds: float
    representative_timestamp_seconds: float
    representative_frame_path: Path
    change_score: float | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(frozen=True)
class ShotCandidate:
    timestamp_seconds: float
    score: float


def detect_shots(
    video_path: Path,
    output_dir: Path,
    duration_seconds: float,
    config: ShotDetectionConfig | None = None,
) -> list[ShotSegmentData]:
    cv2 = _load_cv2()
    config = config or ShotDetectionConfig()
    if duration_seconds <= 0:
        raise ShotDetectionError("Video duration is empty; cannot split shots.")

    candidates = _detect_boundary_candidates(video_path, duration_seconds, config, cv2)
    boundaries = _select_boundaries(candidates, duration_seconds, config)
    frame_dir = _prepare_frame_dir(output_dir)
    score_by_timestamp = {round(candidate.timestamp_seconds, 3): candidate.score for candidate in candidates}

    shots: list[ShotSegmentData] = []
    start = 0.0
    for boundary in [*boundaries, duration_seconds]:
        end = min(duration_seconds, max(start, boundary))
        if end - start < 0.2 and end < duration_seconds:
            continue

        representative_timestamp = _representative_timestamp(start, end, duration_seconds)
        shot_id = f"shot_{len(shots) + 1:03d}"
        frame_path = frame_dir / f"{shot_id}.jpg"
        _save_representative_frame(video_path, representative_timestamp, frame_path, config, cv2)
        shots.append(
            ShotSegmentData(
                shot_id=shot_id,
                index=len(shots) + 1,
                start_seconds=start,
                end_seconds=end,
                representative_timestamp_seconds=representative_timestamp,
                representative_frame_path=frame_path,
                change_score=score_by_timestamp.get(round(start, 3)),
            )
        )
        start = end

    if not shots:
        shot_id = "shot_001"
        timestamp = _representative_timestamp(0.0, duration_seconds, duration_seconds)
        frame_path = frame_dir / f"{shot_id}.jpg"
        _save_representative_frame(video_path, timestamp, frame_path, config, cv2)
        shots.append(
            ShotSegmentData(
                shot_id=shot_id,
                index=1,
                start_seconds=0.0,
                end_seconds=duration_seconds,
                representative_timestamp_seconds=timestamp,
                representative_frame_path=frame_path,
            )
        )
    return shots


def _detect_boundary_candidates(video_path: Path, duration_seconds: float, config: ShotDetectionConfig, cv2) -> list[ShotCandidate]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ShotDetectionError(f"OpenCV cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    sample_step = max(1, int(round(fps / config.sample_fps)))
    frame_index = 0
    previous_gray = None
    previous_hist = None
    last_boundary_at = 0.0
    candidates: list[ShotCandidate] = []

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % sample_step != 0:
                frame_index += 1
                continue

            timestamp = min(duration_seconds, frame_index / fps)
            prepared = _prepare_frame_for_analysis(frame, config.analysis_width, cv2)
            gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
            hist = _frame_histogram(prepared, cv2)

            if previous_gray is not None and previous_hist is not None:
                diff_score = cv2.absdiff(gray, previous_gray).mean() / 255.0
                hist_similarity = cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL)
                hist_score = max(0.0, min(1.0, 1.0 - hist_similarity))
                score = (diff_score * 0.58) + (hist_score * 0.42)
                if score >= config.threshold and timestamp - last_boundary_at >= config.min_shot_seconds:
                    candidates.append(ShotCandidate(timestamp_seconds=timestamp, score=float(score)))
                    last_boundary_at = timestamp

            previous_gray = gray
            previous_hist = hist
            frame_index += 1
    finally:
        cap.release()

    return candidates


def _select_boundaries(candidates: list[ShotCandidate], duration_seconds: float, config: ShotDetectionConfig) -> list[float]:
    usable = [
        candidate
        for candidate in candidates
        if config.min_shot_seconds <= candidate.timestamp_seconds <= duration_seconds - 0.35
    ]
    if config.max_shots > 0 and len(usable) > config.max_shots - 1:
        usable = sorted(usable, key=lambda candidate: candidate.score, reverse=True)[: config.max_shots - 1]
    return sorted(candidate.timestamp_seconds for candidate in usable)


def _prepare_frame_dir(output_dir: Path) -> Path:
    frame_dir = ensure_dir(output_dir)
    for old_frame in frame_dir.glob("shot_*.jpg"):
        old_frame.unlink()
    return frame_dir


def _save_representative_frame(video_path: Path, timestamp_seconds: float, frame_path: Path, config: ShotDetectionConfig, cv2) -> None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ShotDetectionError(f"OpenCV cannot open video: {video_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp_seconds) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            raise ShotDetectionError(f"Cannot read representative frame from: {video_path}")

        output = _resize_to_width(frame, config.output_width, cv2)
        ok, encoded = cv2.imencode(".jpg", output, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            raise ShotDetectionError(f"Cannot encode representative frame: {frame_path}")
        frame_path.write_bytes(encoded.tobytes())
    finally:
        cap.release()


def _prepare_frame_for_analysis(frame, target_width: int, cv2):
    resized = _resize_to_width(frame, target_width, cv2)
    return cv2.GaussianBlur(resized, (5, 5), 0)


def _resize_to_width(frame, target_width: int, cv2):
    height, width = frame.shape[:2]
    if width <= target_width:
        return frame
    target_height = max(2, int(height * (target_width / width)))
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)


def _frame_histogram(frame, cv2):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _representative_timestamp(start: float, end: float, duration_seconds: float) -> float:
    if end <= start:
        return min(duration_seconds, max(0.0, start))
    midpoint = start + ((end - start) * 0.5)
    return min(max(0.0, midpoint), max(0.0, duration_seconds - 0.05))


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise ShotDetectionError("OpenCV is not installed. Run: python -m pip install -r requirements.txt") from exc
    return cv2
