"""Streaming helpers for text model calls.

Two problems are solved here.

1. ``response_format={"type": "json_object"}`` gives the model a strict payload
   contract, so there is no prose to render while it works. ``stream_json_completion``
   forwards the string value of a whitelisted key (for example ``reply``) as soon
   as those bytes arrive, which lets a caller show a sentence while the rest of
   the document is still generating. The extraction is deliberately best-effort:
   when the model orders or escapes the document differently nothing is
   forwarded and the caller falls back to the fully parsed payload.

2. The OpenAI SDK is synchronous. Iterating it from an ``async`` generator would
   block the event loop for the whole response, so ``aiter_blocking_generator``
   drains it on a worker thread and forwards items through an ``asyncio.Queue``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any, AsyncIterator, Callable, Iterator, Sequence

from openai import OpenAI

logger = logging.getLogger(__name__)

_WHITESPACE = " \t\r\n"
_ESCAPE_KINDS = {'"', "\\", "/", "b", "f", "n", "r", "t"}


class PartialJsonFieldReader:
    """Best-effort streaming reader for one string value inside a JSON document.

    ``feed`` is called with each model delta and returns the characters of the
    first matching field's value that became readable. Field values that are not
    string literals (``null``, numbers) are skipped so a nullable field never
    stalls the reader for a later one.
    """

    def __init__(self, fields: Sequence[str]) -> None:
        self._fields = tuple(fields)
        self._buffer = ""
        self._excluded: set[str] = set()
        self._cursor = 0
        self._open = False
        self._finished = False
        self._escape: str | None = None

    @property
    def started(self) -> bool:
        """True once a field value has begun streaming."""
        return self._open

    def feed(self, chunk: str) -> str:
        if self._finished or not chunk:
            return ""
        self._buffer += chunk
        if not self._open and not self._locate_value():
            return ""
        return self._drain()

    def _locate_value(self) -> bool:
        best: tuple[int, int] | None = None
        for field in self._fields:
            if field in self._excluded:
                continue
            start = self._buffer.find(f'"{field}"')
            if start < 0:
                continue
            index = start + len(field) + 2
            while index < len(self._buffer) and self._buffer[index] in _WHITESPACE:
                index += 1
            if index >= len(self._buffer):
                return False
            if self._buffer[index] != ":":
                self._excluded.add(field)
                continue
            index += 1
            while index < len(self._buffer) and self._buffer[index] in _WHITESPACE:
                index += 1
            if index >= len(self._buffer):
                return False
            if self._buffer[index] == '"':
                if best is None or start < best[0]:
                    best = (start, index + 1)
            elif len(self._buffer) - index >= 4:
                # `null`, a number or a boolean: nothing displayable here.
                self._excluded.add(field)
        if best is None:
            return False
        self._cursor = best[1]
        self._open = True
        return True

    def _drain(self) -> str:
        out: list[str] = []
        while self._cursor < len(self._buffer):
            char = self._buffer[self._cursor]
            if self._escape is not None:
                self._escape += char
                if self._escape[1] == "u" and len(self._escape) < 6:
                    self._cursor += 1
                    continue
                out.append(_decode_escape(self._escape))
                self._escape = None
                self._cursor += 1
                continue
            if char == "\\":
                self._escape = "\\"
                self._cursor += 1
                continue
            if char == '"':
                self._finished = True
                self._cursor += 1
                break
            out.append(char)
            self._cursor += 1
        return "".join(out)


def _decode_escape(escape: str) -> str:
    """Decode one JSON string escape such as ``\\n``, ``\\"`` or ``\\u4e2d``."""
    kind = escape[1:2]
    if kind not in _ESCAPE_KINDS and kind != "u":
        return escape
    try:
        return json.loads(f'"{escape}"')
    except (json.JSONDecodeError, ValueError):
        return escape


def stream_json_completion(
    client: OpenAI,
    messages: list[dict[str, str]],
    *,
    peek_fields: Sequence[str],
    **kwargs: Any,
) -> Iterator[tuple[str, str]]:
    """Stream a JSON completion.

    Yields ``("delta", text)`` frames for the first readable `peek_fields` value,
    then exactly one ``("raw", document)`` frame carrying the full model output.
    """
    reader = PartialJsonFieldReader(peek_fields)
    parts: list[str] = []
    stream = client.chat.completions.create(messages=messages, stream=True, **kwargs)
    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        piece = getattr(delta, "content", None) or ""
        if not piece:
            continue
        parts.append(piece)
        visible = reader.feed(piece)
        if visible:
            yield ("delta", visible)
    yield ("raw", "".join(parts))


async def aiter_blocking_generator(source: Iterator[Any]) -> AsyncIterator[Any]:
    """Forward a synchronous generator to an async iterator without blocking.

    The generator is consumed by one worker thread and each item is handed back
    through an unbounded queue, so a slow producer never holds the event loop and
    an abandoned consumer cannot deadlock the producer.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def produce() -> None:
        try:
            for item in source:
                loop.call_soon_threadsafe(queue.put_nowait, ("item", item))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    task = loop.run_in_executor(None, produce)
    try:
        while True:
            kind, value = await queue.get()
            if kind == "item":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        await task


async def aiter_threaded_producer(
    run: Callable[[Callable[[str, Any], None]], None],
) -> AsyncIterator[tuple[str, Any]]:
    """Run a blocking producer on a worker thread and forward its frames.

    Generation happens inside one long synchronous function, so an ``emit``
    callback is the only way it can report progress. Every ``emit`` call becomes a
    frame on the returned async iterator, and a failure raised by the producer is
    re-raised in the consumer.
    """
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            run(lambda kind, payload: events.put((kind, payload)))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer
            events.put(("error", exc))
        finally:
            events.put(None)

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            item = await asyncio.to_thread(events.get)
            if item is None:
                return
            yield item
    finally:
        await task
