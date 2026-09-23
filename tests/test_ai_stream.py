"""Unit tests for the streaming helpers behind every AI surface."""

from types import SimpleNamespace

import pytest

from backend.services.ai_stream import (
    PartialJsonFieldReader,
    aiter_blocking_generator,
    stream_json_completion,
)


def _feed(reader: PartialJsonFieldReader, text: str, size: int = 1) -> str:
    """Feed `text` in `size`-sized chunks to mimic model token delivery."""
    return "".join(reader.feed(text[index : index + size]) for index in range(0, len(text), size))


def test_reader_forwards_first_matching_field_one_character_at_a_time():
    reader = PartialJsonFieldReader(("reply", "follow_up_question"))

    assert _feed(reader, '{"reply": "请补充授课对象。", "teaching_goal": "理解浮力"}') == "请补充授课对象。"


def test_reader_skips_nullable_field_and_streams_a_later_candidate():
    reader = PartialJsonFieldReader(("reply", "confirm_summary"))

    assert _feed(reader, '{"reply": null, "confirm_summary": "已形成需求。"}') == "已形成需求。"


def test_reader_decodes_escapes_and_unicode():
    reader = PartialJsonFieldReader(("reply",))

    assert _feed(reader, '{"reply": "第一行\\n第二行 \\"引用\\""}') == '第一行\n第二行 "引用"'
    assert _feed(PartialJsonFieldReader(("reply",)), '{"reply": "\\u4e2d\\u6587"}') == "中文"


def test_reader_does_not_leak_characters_after_the_value_closes():
    reader = PartialJsonFieldReader(("reply",))

    assert _feed(reader, '{"reply": "甲", "teaching_goal": "乙"}') == "甲"


def test_reader_holds_incomplete_trailing_escape():
    reader = PartialJsonFieldReader(("reply",))

    # The backslash arrives without its payload yet; it must not be emitted raw.
    assert reader.feed('{"reply": "尾\\') == "尾"
    assert reader.feed("n") == "\n"


def test_reader_yields_nothing_when_no_candidate_is_present():
    reader = PartialJsonFieldReader(("reply",))

    assert _feed(reader, '{"teaching_goal": "理解浮力"}') == ""


class _FakeCompletions:
    def __init__(self, pieces: list[str]) -> None:
        self._pieces = pieces
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])
            for piece in self._pieces
        ]
        return iter(chunks)


def _fake_client(pieces: list[str]) -> tuple[SimpleNamespace, _FakeCompletions]:
    completions = _FakeCompletions(pieces)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_stream_json_completion_forwards_deltas_then_the_raw_document():
    document = '{"reply": "你好", "teaching_goal": "理解浮力"}'
    client, completions = _fake_client(list(document))

    frames = list(
        stream_json_completion(
            client,
            [{"role": "user", "content": "hi"}],
            peek_fields=("reply",),
            model="test-model",
        )
    )

    assert "".join(value for kind, value in frames if kind == "delta") == "你好"
    assert [value for kind, value in frames if kind == "raw"] == [document]
    assert completions.kwargs["stream"] is True
    assert completions.kwargs["model"] == "test-model"


def test_stream_json_completion_tolerates_chunks_without_choices():
    client, _ = _fake_client([])
    client.chat.completions.create = lambda **kwargs: iter(
        [SimpleNamespace(choices=[]), SimpleNamespace(choices=[SimpleNamespace(delta=None)])]
    )

    frames = list(stream_json_completion(client, [], peek_fields=("reply",)))

    assert frames == [("raw", "")]


async def test_aiter_blocking_generator_forwards_every_item():
    def source():
        yield "a"
        yield "b"

    assert [item async for item in aiter_blocking_generator(source())] == ["a", "b"]


async def test_aiter_blocking_generator_propagates_producer_failures():
    def source():
        yield "a"
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        async for _ in aiter_blocking_generator(source()):
            pass
