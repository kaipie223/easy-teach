"""Server-side resource watching that backs the progress SSE endpoints.

Job progress is written to the database by whichever process owns the job: the
API request itself when `TASK_QUEUE_EAGER` is enabled, or a separate Celery
worker in production. A push channel would therefore have to cross a process
boundary, and the only bridge that works in every mode is the database. This
module reads that single source of truth on the server and forwards changes to
the client, which replaces one HTTP request per client per tick with one shared
read per watcher.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable

from backend.core.errors import ApiError

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed"})

# Frames are only emitted when the snapshot actually changes, so the interval
# trades a little latency for far fewer database round trips than client polling.
WATCH_INTERVAL_SECONDS = 0.75
# A watcher must never outlive the work it observes; the client can reconnect.
WATCH_MAX_SECONDS = 30 * 60


def status_is_terminal(data: dict[str, Any]) -> bool:
    """Default predicate: the observed row reports a final status."""
    return data.get("status") in TERMINAL_STATUSES


async def watch_snapshot(
    snapshot: Callable[[], dict[str, Any] | None],
    *,
    is_terminal: Callable[[dict[str, Any]], bool] = status_is_terminal,
    interval: float = WATCH_INTERVAL_SECONDS,
    max_seconds: float = WATCH_MAX_SECONDS,
) -> AsyncIterator[tuple[str, Any]]:
    """Emit a frame whenever ``snapshot()`` changes.

    Frames are ``("progress", payload)`` for intermediate states and
    ``("result", payload)`` once ``is_terminal`` reports the work is finished,
    after which the iterator stops. ``snapshot`` runs in a worker thread and is
    expected to open its own short-lived database session, so repeated reads never
    serve a stale identity map.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_seconds
    previous: str | None = None

    while True:
        data = await asyncio.to_thread(snapshot)
        if data is None:
            raise ApiError(
                "观察对象不存在或已被删除",
                code="WATCH_TARGET_MISSING",
                status_code=404,
            )

        fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint != previous:
            previous = fingerprint
            finished = is_terminal(data)
            yield ("result" if finished else "progress", data)
            if finished:
                return

        if loop.time() >= deadline:
            logger.info("Closing progress watch after %.0f seconds", max_seconds)
            return
        await asyncio.sleep(interval)
