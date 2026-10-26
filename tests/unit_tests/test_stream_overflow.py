"""流式终答遇上下文超限：未发布正文才允许恢复，已发布片段不得重放."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from common.output_streaming.answer import stream_final_answer


class ProviderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


class FakePublisher:
    def __init__(self) -> None:
        self.total_chars = 0
        self.emitted_text = ""
        self.message_id = "m"
        self.output_id = "o"
        self.aborted = None
        self.finished = None

    def start(self) -> None:
        return None

    async def publish(self, unit: str) -> bool:
        self.total_chars += len(unit)
        self.emitted_text += unit
        return True

    async def abort(self, reason: str, detail: str | None = None) -> None:
        self.aborted = (reason, detail)

    async def finish(self, text: str) -> str:
        self.finished = text
        return text


class OverflowModel:
    def __init__(self, *, partial: bool) -> None:
        self.partial = partial

    def astream(self, messages, config=None):
        async def generator():
            if self.partial:
                yield AIMessageChunk(content="第一句。")
            raise ProviderError(400, "maximum context length exceeded")

        return generator()


@pytest.mark.anyio
async def test_overflow_before_visible_text_is_recoverable():
    publisher = FakePublisher()
    with pytest.raises(ProviderError):
        await stream_final_answer(
            OverflowModel(partial=False),
            [HumanMessage(content="q")],
            {},
            publisher=publisher,
        )
    assert publisher.aborted is None


@pytest.mark.anyio
async def test_overflow_after_visible_text_aborts_without_replay():
    publisher = FakePublisher()
    result = await stream_final_answer(
        OverflowModel(partial=True),
        [HumanMessage(content="q")],
        {},
        publisher=publisher,
    )
    assert result.aborted
    assert result.abort_reason == "generation_failed"
    assert publisher.aborted is not None
    assert publisher.total_chars > 0
