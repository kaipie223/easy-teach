from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_sha1(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    cleaned = cleaned.strip("-_").lower()
    return cleaned or "video"


def json_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    return Path(path).resolve().as_posix()


def timecode(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    total_seconds, ms = divmod(total_ms, 1000)
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{ms:03d}"
    return f"{minutes:02d}:{sec:02d}.{ms:03d}"


def compact_timecode(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def sample_timestamps(duration_seconds: float, max_count: int) -> list[float]:
    if max_count <= 0:
        return []
    if duration_seconds <= 0:
        return [0.0]
    count = min(max_count, max(1, math.ceil(duration_seconds / 8)))
    if count == 1:
        return [min(max(0.0, duration_seconds / 2), max(0.0, duration_seconds - 0.05))]
    step = duration_seconds / (count + 1)
    return [min(max(0.0, duration_seconds - 0.05), step * index) for index in range(1, count + 1)]


def shorten(text: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


def overlap_seconds(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))
