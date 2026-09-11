from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:  # pragma: no cover - dependency is declared in project requirements.
        raise RuntimeError("opencc-python-reimplemented is not installed.") from exc
    return OpenCC("t2s")


def to_simplified_chinese(text: str) -> str:
    """Normalize Traditional Chinese ASR text to Simplified Chinese."""
    if not text:
        return text
    return _converter().convert(text)
