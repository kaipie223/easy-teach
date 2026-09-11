from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .schemas import VideoUnderstandingResult


class VideoCacheError(RuntimeError):
    """Raised when a video-understanding cache cannot be read or written."""


def make_video_cache_key(
    *,
    video_sha1: str,
    asr_hash: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    fps: float,
    chunk_start_seconds: float,
    chunk_end_seconds: float,
) -> str:
    """Return a stable, secret-free cache key for one model request.

    Only digests and explicit non-sensitive configuration are persisted.  The
    original path, URL, Authorization header and API key are intentionally not
    part of the serialized cache record.
    """

    canonical = {
        "video_sha1": video_sha1,
        "asr_hash": asr_hash,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "fps": round(float(fps), 6),
        "chunk_start_seconds": round(float(chunk_start_seconds), 6),
        "chunk_end_seconds": round(float(chunk_end_seconds), 6),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class VideoUnderstandingCache:
    """Small file cache for validated model results.

    Cache files contain only a validated ``VideoUnderstandingResult`` and a
    cache key.  Request URLs and credentials never enter the cache.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, cache_key: str) -> Path:
        if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
            raise VideoCacheError("cache key must be a lowercase SHA-256 digest")
        return self.root / f"{cache_key}.json"

    def get(self, cache_key: str) -> VideoUnderstandingResult | None:
        path = self.path_for(cache_key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("cache_key") != cache_key:
                raise VideoCacheError("cache key mismatch")
            return VideoUnderstandingResult.model_validate(payload["result"])
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            # A corrupt cache entry is a miss.  It must never make a parser
            # task fail when the model can be called again.
            raise VideoCacheError(f"invalid video-understanding cache entry: {path.name}") from exc

    def put(self, cache_key: str, result: VideoUnderstandingResult) -> Path:
        path = self.path_for(cache_key)
        payload: dict[str, Any] = {"cache_key": cache_key, "result": result.model_dump(mode="json")}
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise VideoCacheError(f"unable to write video-understanding cache entry: {path.name}") from exc
        return path
