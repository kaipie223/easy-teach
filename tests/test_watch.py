"""Unit tests for the progress watcher behind the SSE task stream."""

import pytest

from backend.core.errors import ApiError
from backend.services.watch import watch_snapshot


async def _drain(source):
    return [frame async for frame in source]


async def test_watch_emits_only_when_the_snapshot_changes():
    states = [
        {"status": "pending", "progress": 0},
        {"status": "pending", "progress": 0},
        {"status": "processing", "progress": 40},
        {"status": "completed", "progress": 100},
    ]
    calls = {"count": 0}

    def snapshot():
        value = states[min(calls["count"], len(states) - 1)]
        calls["count"] += 1
        return value

    frames = await _drain(watch_snapshot(snapshot, interval=0))

    # The repeated pending snapshot must not produce a duplicate frame.
    assert [kind for kind, _ in frames] == ["progress", "progress", "result"]
    assert [payload["progress"] for _, payload in frames] == [0, 40, 100]


async def test_watch_raises_when_the_target_disappears():
    with pytest.raises(ApiError) as excinfo:
        await _drain(watch_snapshot(lambda: None, interval=0))

    assert excinfo.value.code == "WATCH_TARGET_MISSING"


async def test_watch_closes_immediately_on_a_terminal_snapshot():
    frames = await _drain(watch_snapshot(lambda: {"status": "failed"}, interval=0))

    assert [kind for kind, _ in frames] == ["result"]


async def test_watch_closes_after_the_maximum_duration():
    frames = await _drain(
        watch_snapshot(lambda: {"status": "processing"}, interval=0, max_seconds=0)
    )

    assert [kind for kind, _ in frames] == ["progress"]


async def test_watch_keeps_a_project_stream_open_when_the_predicate_is_never_terminal():
    frames = await _drain(
        watch_snapshot(
            lambda: {"active": True},
            is_terminal=lambda _payload: False,
            interval=0,
            max_seconds=0,
        )
    )

    # A long-lived subscription must never be closed by a `result` frame; only
    # the watch time cap ends it.
    assert [kind for kind, _ in frames] == ["progress"]
