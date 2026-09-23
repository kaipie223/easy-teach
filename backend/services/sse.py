"""Server-Sent Events encoding shared by every streaming endpoint.

Keeping the framing in one place means a single client-side parser can consume
chat turns, incremental model text, generation progress and terminal payloads
without each endpoint inventing its own wire format.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

SSE_MEDIA_TYPE = "text/event-stream"

# `X-Accel-Buffering: no` stops nginx from buffering the stream, which would
# otherwise hold every frame until the response completed.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ModelEvent(Protocol):
    """An object exposing the `ChatEvent` shape used by the SSE chat channel."""

    event_type: Any
    content: str
    data: dict[str, Any] | None

    def model_dump_json(self) -> str: ...


def encode_sse(event: str, payload: Any) -> str:
    """Encode one SSE frame.

    `payload` is serialised unless it is already a string, so callers can send a
    JSON object or a pre-rendered body without special-casing.
    """
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"


def encode_model_event(event: ModelEvent) -> str:
    """Encode a pydantic-style event object using its own event type and JSON."""
    return f"event: {event.event_type.value}\ndata: {event.model_dump_json()}\n\n"


def encode_done(event: str = "text") -> str:
    """Encode the terminal frame the frontend client treats as end-of-stream."""
    return encode_sse(event, "[DONE]")


def wants_event_stream(accept_header: str | None) -> bool:
    """True when the caller negotiated an SSE response through `Accept`."""
    return SSE_MEDIA_TYPE in (accept_header or "")


def error_frame(
    message: str,
    *,
    code: str,
    recoverable: bool = True,
    suggested_action: str | None = None,
) -> str:
    """Encode the error payload the frontend SSE client already understands.

    A stream has usually already been committed to HTTP 200 by the time a
    failure surfaces, so errors have to travel as an event rather than a status.
    """
    return encode_sse(
        "error",
        {
            "content": message,
            "data": {
                "code": code,
                "recoverable": recoverable,
                "suggested_action": suggested_action,
            },
        },
    )
