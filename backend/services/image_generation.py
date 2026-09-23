"""Text-to-image adapter for the editor's "AI 生成配图" action.

A page that wanted an illustration had no way to get one from the editor: the
teacher had to upload a picture elsewhere first and then pick it. This module is
that missing step — it turns a prompt into picture bytes the ingestion pipeline
can store as an ordinary project image.

Two boundaries are deliberate, mirroring the vision adapter:

1. Generation is opt-in. Without ``ARK_API_KEY`` nothing is sent and the caller
   receives ``IMAGE_GEN_NOT_CONFIGURED``, so the product can say "not
   configured" instead of pretending a picture was produced.
2. A configured provider that fails raises, so a broken call is never recorded
   as a successful generation.

The provider is Volcengine ARK (Seedream). ``/images/generations`` is
OpenAI-compatible, so any compatible base URL works.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# 按魔数判断真实格式：ARK 即使请求 b64_json 也可能返回 JPEG，
# 只按扩展名写盘会让下游按错误的 MIME 类型处理。
_MAGIC_FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF8", "image/gif", ".gif"),
)


class ImageGenerationError(RuntimeError):
    """Raised when image generation is configured but did not produce a picture."""

    def __init__(self, message: str, *, code: str = "IMAGE_GEN_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    extension: str


def image_generation_enabled() -> bool:
    """True when an image-generation key is configured for this deployment."""
    return bool(settings.ark_api_key.strip())


def _sniff_format(data: bytes) -> tuple[str, str]:
    for magic, mime_type, extension in _MAGIC_FORMATS:
        if data.startswith(magic):
            return mime_type, extension
    return "image/png", ".png"


def _endpoint() -> str:
    return f"{settings.ark_image_base_url.rstrip('/')}/images/generations"


def _provider_message(response: httpx.Response) -> str:
    """Pull the provider's own error text out; fall back to the status line."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if payload.get("message"):
            return str(payload["message"])[:300]
    return f"HTTP {response.status_code}"


def _decode_image(payload: dict[str, Any]) -> bytes:
    items = payload.get("data")
    if not isinstance(items, list) or not items:
        raise ImageGenerationError("图像服务没有返回图片", code="IMAGE_GEN_EMPTY")
    first = items[0] if isinstance(items[0], dict) else {}
    encoded = first.get("b64_json")
    if not encoded:
        raise ImageGenerationError("图像服务返回了无法解析的结果", code="IMAGE_GEN_EMPTY")
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise ImageGenerationError("图像数据解码失败", code="IMAGE_GEN_EMPTY") from exc


def generate_image(
    prompt: str,
    *,
    size: str | None = None,
    client: httpx.Client | None = None,
) -> GeneratedImage:
    """Generate one picture from a prompt, or raise ``ImageGenerationError``."""
    text = (prompt or "").strip()
    if not text:
        raise ImageGenerationError("请先描述想要的配图内容", code="IMAGE_PROMPT_REQUIRED")
    if not image_generation_enabled():
        raise ImageGenerationError(
            "尚未配置图像生成服务", code="IMAGE_GEN_NOT_CONFIGURED"
        )

    body = {
        "model": settings.ark_image_model,
        "prompt": text,
        "size": (size or settings.ark_image_size).strip(),
        # b64 直接拿字节：省掉一次下载，也避免把临时 URL 写进数据库。
        "response_format": "b64_json",
        "watermark": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.ark_api_key}",
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=settings.ark_image_timeout_seconds)
    try:
        response = http.post(_endpoint(), headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ImageGenerationError(
            f"图像生成服务连接失败：{type(exc).__name__}", code="IMAGE_GEN_UNREACHABLE"
        ) from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code >= 400:
        raise ImageGenerationError(
            f"图像生成失败：{_provider_message(response)}", code="IMAGE_GEN_PROVIDER_ERROR"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImageGenerationError("图像服务返回了非 JSON 响应", code="IMAGE_GEN_PROVIDER_ERROR") from exc
    if not isinstance(payload, dict):
        raise ImageGenerationError("图像服务返回了非对象响应", code="IMAGE_GEN_PROVIDER_ERROR")

    data = _decode_image(payload)
    if not data:
        raise ImageGenerationError("图像服务返回了空图片", code="IMAGE_GEN_EMPTY")
    mime_type, extension = _sniff_format(data)
    logger.info(
        "Generated slide image with %s: %d bytes (%s)",
        settings.ark_image_model,
        len(data),
        mime_type,
    )
    return GeneratedImage(data=data, mime_type=mime_type, extension=extension)
