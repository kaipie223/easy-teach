"""Vision adapter that turns an uploaded picture into text the text model can use.

The blueprint model is blind to pictures, so an uploaded image stays opaque until
something describes it. This module is that step: it asks a vision model what a
picture shows and returns a short description, which the ingestion pipeline then
stores as ordinary evidence and the blueprint prompt can match against topics.

Two boundaries are deliberate:

1. Vision is opt-in. With no model configured nothing is sent and the caller
   receives ``None``; the product then reports "image understanding unavailable"
   rather than guessing at the picture's content.
2. A configured provider that fails raises instead of returning empty text, so a
   broken call cannot be recorded as a successful reading.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from backend.config import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "image-vision-v1"
# Long-edge cap applied before the picture is sent. Token cost grows with
# resolution while a slide picture is never read at camera size, so downscaling
# is almost pure savings.
MAX_EDGE = 1024
DESCRIBE_PROMPT = """你是教学素材理解助手。请看懂这张图片，并只返回 JSON：
{"description": "一句话画面描述，不超过 60 字", "keywords": ["3-6 个知识点关键词"], "suggested_use": "适合用在什么样的教学页面，不超过 30 字"}

要求：只描述你确实看到的内容，不要猜测；照片说明拍了什么，图表或示意图说明它表达的结构或关系。"""


class ImageVisionError(RuntimeError):
    """Raised when vision is configured but the picture could not be read."""


def vision_enabled() -> bool:
    """True when a vision model is configured for this deployment."""
    return bool(settings.deepseek_vision_model.strip())


def _data_url(path: Path) -> str:
    with Image.open(path) as image:
        picture = image.convert("RGB")
        picture.thumbnail((MAX_EDGE, MAX_EDGE))
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_payload(text: str) -> dict[str, Any]:
    """Read the description out of a model reply, tolerating prose replies."""
    raw = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A model that answered in prose instead of JSON is still usable.
        return {"description": raw}
    if not isinstance(parsed, dict):
        return {"description": str(parsed)}
    return parsed


def describe_image(path: Path, *, client: Any | None = None) -> dict[str, Any] | None:
    """Return what a picture shows, or ``None`` when vision is not configured.

    Raises ``ImageVisionError`` when a configured provider fails, so ingestion
    can record an honest "image understanding unavailable" instead of claiming
    the picture was read.
    """
    if client is None:
        if not vision_enabled():
            return None
        from openai import OpenAI

        client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    try:
        response = client.chat.completions.create(
            model=settings.deepseek_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DESCRIBE_PROMPT},
                        {"type": "image_url", "image_url": {"url": _data_url(path)}},
                    ],
                }
            ],
            max_tokens=600,
            timeout=90,
        )
        payload = _parse_payload(response.choices[0].message.content or "")
    except ImageVisionError:
        raise
    except Exception as exc:
        raise ImageVisionError(f"图片理解调用失败：{type(exc).__name__}") from exc

    description = _as_text(payload.get("description"))
    if not description:
        raise ImageVisionError("视觉模型没有返回可用的图片描述")
    keywords = [str(item).strip() for item in (payload.get("keywords") or []) if str(item).strip()]
    return {
        "description": description,
        "keywords": keywords[:8],
        "suggested_use": _as_text(payload.get("suggested_use")),
        "model_name": settings.deepseek_vision_model,
        "prompt_version": PROMPT_VERSION,
    }


def description_text(description: dict[str, Any]) -> str:
    """Flatten a description into the single text blob stored as evidence."""
    lines = [str(description.get("description") or "").strip()]
    keywords = description.get("keywords") or []
    if keywords:
        lines.append("图片关键词：" + "、".join(str(item) for item in keywords))
    suggested_use = str(description.get("suggested_use") or "").strip()
    if suggested_use:
        lines.append(f"建议用途：{suggested_use}")
    return "\n".join(line for line in lines if line)
