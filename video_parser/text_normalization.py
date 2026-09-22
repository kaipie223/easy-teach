from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _converter():
    """返回 OpenCC 转换器；opencc 不可用时返回 None，而不是抛异常。

    返回 None 而不是 raise，`lru_cache` 才能把"不可用"这个结论也缓存下来，
    避免每一段转写都重新尝试 import。
    """
    try:
        from opencc import OpenCC

        return OpenCC("t2s")
    except Exception:  # noqa: BLE001 - opencc 是可选依赖，任何加载失败都降级为"不转换"
        return None


def normalization_available() -> bool:
    """opencc 是否可用；不可用时 to_simplified_chinese 会原样返回文本。"""
    return _converter() is not None


def to_simplified_chinese(text: str) -> str:
    """Normalize Traditional Chinese ASR text to Simplified Chinese.

    opencc 缺失时返回原文（不做繁转简）而不是抛异常：转写往往已经跑完了几分钟的
    Whisper 计算，不该因为一个可选依赖把整段 ASR 结果丢掉。
    """
    if not text:
        return text
    converter = _converter()
    if converter is None:
        return text
    return converter.convert(text)
