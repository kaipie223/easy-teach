"""Bounded streaming helpers for inbound files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from backend.config import settings


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    size_bytes: int
    checksum_sha256: str


class UploadSizeExceeded(ValueError):
    def __init__(self, *, max_bytes: int, actual_bytes: int) -> None:
        super().__init__("upload exceeds configured size limit")
        self.max_bytes = max_bytes
        self.actual_bytes = actual_bytes


def remove_staged_upload(path: Path) -> None:
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def remove_managed_file(path: Path, *, root: Path) -> None:
    """Delete one managed file after verifying it remains inside its storage root."""
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("managed file is outside its configured storage root") from exc
    remove_staged_upload(resolved_path)


def checksum_file(path: Path) -> tuple[str, int]:
    """Hash an existing file incrementally and return (sha256, size_bytes)."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


async def stream_upload_to_path(
    upload: UploadFile,
    path: Path,
    *,
    max_bytes: int,
) -> StoredUpload:
    """Write an upload incrementally while computing its size and checksum."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size_bytes = 0
    chunk_size = max(settings.upload_chunk_size_kb, 64) * 1024
    try:
        with path.open("wb") as target:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadSizeExceeded(
                        max_bytes=max_bytes,
                        actual_bytes=size_bytes,
                    )
                digest.update(chunk)
                target.write(chunk)
    except Exception:
        remove_staged_upload(path)
        raise
    finally:
        await upload.close()

    return StoredUpload(
        path=path,
        size_bytes=size_bytes,
        checksum_sha256=digest.hexdigest(),
    )
