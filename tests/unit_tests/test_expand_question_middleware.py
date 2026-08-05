"""推荐追问中间件延迟控制测试."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from common.middleware import expand_question_middleware as module
from common.middleware.expand_question_middleware import ExpandQuestionMiddleware

pytestmark = pytest.mark.anyio


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def astream(self, _messages):
        self.calls += 1
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield FakeChunk(chunk)


def _state() -> dict:
    return {
        "messages": [
            HumanMessage(content="今天空气质量如何"),
            AIMessage(content="空气质量良。"),
        ]
    }


async def test_expand_question_can_be_disabled(monkeypatch) -> None:
    llm = FakeLLM(["不会被调用"])
    monkeypatch.setattr(module.ModelRegistry, "deepseek_v4_flash", llm)
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {"configurable": {"expand_question_enabled": False}},
    )
    events: list[dict] = []

    result = await ExpandQuestionMiddleware().aafter_agent(
        _state(),
        SimpleNamespace(stream_writer=events.append),
    )

    await asyncio.sleep(0)
    assert result is None
    assert llm.calls == 0
    assert events == []


async def test_expand_question_pushes_before_after_agent_returns(monkeypatch) -> None:
    llm = FakeLLM(["追问一\n", "追问二"])
    monkeypatch.setattr(module.ModelRegistry, "deepseek_v4_flash", llm)
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {"configurable": {"expand_question_enabled": True}},
    )
    events: list[dict] = []

    result = await ExpandQuestionMiddleware().aafter_agent(
        _state(),
        SimpleNamespace(stream_writer=events.append),
    )

    assert result is None
    assert events == [
        {
            "node": "expand_question",
            "type": "expanded_questions",
            "message": ["追问一", "追问二"],
        }
    ]


async def test_expand_question_failure_does_not_raise(monkeypatch) -> None:
    class FailingLLM:
        async def astream(self, _messages):
            raise RuntimeError("model unavailable")
            yield

    monkeypatch.setattr(module.ModelRegistry, "deepseek_v4_flash", FailingLLM())
    monkeypatch.setattr(
        module,
        "get_config",
        lambda: {"configurable": {"expand_question_enabled": True}},
    )

    result = await ExpandQuestionMiddleware().aafter_agent(
        _state(),
        SimpleNamespace(stream_writer=lambda _event: None),
    )

    await asyncio.sleep(0)
    assert result is None
