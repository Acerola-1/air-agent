"""Shared publisher, safety buffer and streaming answer tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from common.output_streaming.answer import stream_final_answer
from common.output_streaming.contract import (
    EVENT_ABORTED,
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_START,
    OutputStreamValidator,
)
from common.output_streaming.publisher import OutputAborted, OutputPublisher
from common.output_streaming.safety import SafeTextStream, StreamSafetyError


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def of(self, event_type: str) -> list[dict]:
        return [event for event in self.events if event.get("type") == event_type]


class SlowModel:
    """Fake streaming model with a barrier after the first public unit."""

    def __init__(
        self,
        chunks: list[str],
        *,
        barrier_after: int = 1,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.chunks = chunks
        self.barrier_after = barrier_after
        self.reached = asyncio.Event()
        self.release = gate or asyncio.Event()
        self.finished = False

    async def astream(self, messages, config=None):  # noqa: ANN001
        for index, text in enumerate(self.chunks):
            yield AIMessageChunk(content=text)
            if index == self.barrier_after:
                self.reached.set()
                await self.release.wait()
        self.finished = True

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return AIMessage(content="".join(self.chunks))


def _publisher(recorder: Recorder, **kwargs):
    return OutputPublisher(
        {"configurable": {"thread_id": "thread-1"}},
        graph_id="basic-qa",
        writer=recorder,
        **kwargs,
    )


@pytest.mark.anyio
async def test_publisher_lifecycle_and_idempotent_done():
    recorder = Recorder()
    publisher = _publisher(recorder)
    publisher.start()
    await publisher.publish("第一段。")
    await publisher.publish("第二段。")
    text = await publisher.finish("第一段。第二段。")
    assert text == "第一段。第二段。"
    assert await publisher.finish("第一段。第二段。") == text
    types = [event["type"] for event in recorder.events]
    assert types == [EVENT_START, EVENT_DELTA, EVENT_DELTA, EVENT_DONE]
    validator = OutputStreamValidator()
    for event in recorder.events:
        validator.accept(event)
    assert validator.state.errors == []
    assert validator.state.text == text


@pytest.mark.anyio
async def test_publisher_abort_is_terminal_and_probe_blocks_output():
    recorder = Recorder()

    async def deny() -> bool:
        return False

    publisher = _publisher(recorder, authorization_probe=deny)
    publisher.start()
    with pytest.raises(OutputAborted):
        await publisher.publish("不应公开")
    assert recorder.events[-1]["type"] == EVENT_ABORTED
    assert recorder.of(EVENT_DELTA) == []
    await publisher.publish("迟到") if False else None


@pytest.mark.anyio
async def test_first_delta_reaches_writer_before_source_finishes():
    recorder = Recorder()
    model = SlowModel(["第一段。", "第二段。", "第三段。"], barrier_after=1)
    publisher = _publisher(recorder)
    task = asyncio.create_task(
        stream_final_answer(
            model,
            [HumanMessage(content="介绍空气质量")],
            {},
            publisher=publisher,
            policy_messages=[HumanMessage(content="介绍空气质量")],
            question="介绍空气质量",
        )
    )
    await asyncio.wait_for(model.reached.wait(), timeout=2)
    # Source is blocked on its barrier; a public delta already left the process.
    assert recorder.of(EVENT_DELTA), "no delta before source completion"
    assert not model.finished
    model.release.set()
    result = await asyncio.wait_for(task, timeout=3)
    assert result.aborted is False
    assert result.text == "第一段。第二段。第三段。"
    validator = OutputStreamValidator()
    for event in recorder.events:
        validator.accept(event)
    assert validator.state.errors == []


@pytest.mark.anyio
async def test_reasoning_tag_split_across_chunks_never_leaks():
    recorder = Recorder()
    model = SlowModel(["<thi", "nk>内部推理</think>公开正文。"], barrier_after=99)
    publisher = _publisher(recorder)
    result = await stream_final_answer(
        model,
        [HumanMessage(content="你好")],
        {},
        publisher=publisher,
        policy_messages=[HumanMessage(content="你好")],
        question="你好",
    )
    joined = "".join(event.get("message", "") for event in recorder.of(EVENT_DELTA))
    assert "内部推理" not in joined
    assert "公开正文。" in joined
    assert result.text == "公开正文。"


@pytest.mark.anyio
async def test_unknown_citation_aborts_before_publication():
    recorder = Recorder()
    model = SlowModel(
        ["依据 [[cite:00000000-0000-4000-8000-000000000000]]。"], barrier_after=99
    )
    publisher = _publisher(recorder)
    result = await stream_final_answer(
        model,
        [HumanMessage(content="给我一个依据")],
        {},
        publisher=publisher,
        policy_messages=[HumanMessage(content="给我一个依据")],
        question="给我一个依据",
    )
    assert result.aborted is True
    assert recorder.of(EVENT_DONE) == []
    assert recorder.of(EVENT_ABORTED)
    assert "00000000" not in "".join(
        event.get("message", "") for event in recorder.of(EVENT_DELTA)
    )


@pytest.mark.anyio
async def test_mid_stream_specific_regulation_text_aborts_without_done():
    recorder = Recorder()
    model = SlowModel(["背景说明。", "浓度限值为 35 微克/立方米。"], barrier_after=99)
    publisher = _publisher(recorder)
    result = await stream_final_answer(
        model,
        [HumanMessage(content="介绍一下这个指标")],
        {},
        publisher=publisher,
        policy_messages=[HumanMessage(content="介绍一下这个指标")],
        question="介绍一下这个指标",
    )
    assert result.aborted is True
    assert recorder.of(EVENT_DONE) == []
    assert all(
        "35" not in event.get("message", "") for event in recorder.of(EVENT_DELTA)
    )


@pytest.mark.anyio
async def test_policy_bound_question_uses_single_controlled_unit():
    recorder = Recorder()
    model = SlowModel(["不能给出未核验限值。"], barrier_after=99)
    publisher = _publisher(recorder)
    result = await stream_final_answer(
        model,
        [HumanMessage(content="PM2.5 的标准限值是多少？")],
        {},
        publisher=publisher,
        policy_messages=[HumanMessage(content="PM2.5 的标准限值是多少？")],
        question="PM2.5 的标准限值是多少？",
    )
    assert result.delivery_mode == "policy_bound"
    assert result.aborted is False
    assert len(recorder.of(EVENT_DELTA)) == 1


def test_safety_buffer_json_citation_unicode_and_bounded_growth():
    buffer = SafeTextStream()
    assert buffer.feed('{"success":') == []
    assert buffer.feed("false}") == ['{"success":false}']
    assertion = SafeTextStream()
    assert assertion.feed("😀 中文，") == []
    assert assertion.feed("继续。") == ["😀 中文，继续。"]
    cite = SafeTextStream()
    assert cite.feed("[[cite:abc") == []
    assert cite.feed("def]]。") == ["[[cite:abcdef]]。"]
    with pytest.raises(StreamSafetyError):
        SafeTextStream().feed("x" * 5000)


def test_safety_buffer_rejects_unterminated_reasoning():
    buffer = SafeTextStream()
    buffer.feed("<think>没有结束")
    with pytest.raises(StreamSafetyError):
        buffer.flush()


@pytest.mark.anyio
async def test_done_text_equals_joined_deltas_and_hash():
    recorder = Recorder()
    model = SlowModel(["甲。", "乙。", "丙。"], barrier_after=99)
    publisher = _publisher(recorder)
    result = await stream_final_answer(
        model,
        [HumanMessage(content="写三段")],
        {},
        publisher=publisher,
        policy_messages=[HumanMessage(content="写三段")],
        question="写三段",
    )
    deltas = "".join(event["message"] for event in recorder.of(EVENT_DELTA))
    done = recorder.of(EVENT_DONE)[-1]
    assert deltas == done["message"] == result.message.content
    assert json.loads(json.dumps(done))["content_sha256"] == done["content_sha256"]


@pytest.mark.anyio
async def test_cancelled_stream_emits_aborted_before_propagating():
    recorder = Recorder()

    class CancelledModel:
        async def astream(self, messages, config=None):
            yield AIMessageChunk(content="部分。")
            raise asyncio.CancelledError()

    publisher = _publisher(recorder)
    with pytest.raises(asyncio.CancelledError):
        await stream_final_answer(
            CancelledModel(),
            [HumanMessage(content="写一段")],
            {},
            publisher=publisher,
            policy_messages=[HumanMessage(content="写一段")],
            question="写一段",
        )
    assert recorder.of(EVENT_ABORTED)
    assert recorder.of(EVENT_DONE) == []


@pytest.mark.anyio
async def test_sync_positive_probe_and_conflicting_done():
    from common.output_streaming.publisher import OutputStateError

    recorder = Recorder()
    publisher = _publisher(recorder, authorization_probe=lambda: True)
    publisher.start()
    await publisher.publish("已验证正文。")
    await publisher.finish()
    with pytest.raises(OutputStateError, match="conflicting"):
        await publisher.finish("另一份正文。")
    assert len(recorder.of(EVENT_DONE)) == 1
