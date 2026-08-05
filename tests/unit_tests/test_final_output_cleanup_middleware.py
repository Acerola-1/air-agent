"""最终输出清理中间件测试."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from common.middleware.final_output_cleanup_middleware import (
    FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
    FinalOutputCleanupMiddleware,
    build_final_output_cleanup_prompt,
)

pytestmark = pytest.mark.anyio


class FakeChunk:
    def __init__(self, content) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.calls = 0
        self.prompts: list[list[dict[str, str]]] = []

    async def astream(self, messages):
        self.calls += 1
        self.prompts.append(messages)
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield FakeChunk(chunk)


class FailingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, _messages):
        self.calls += 1
        raise RuntimeError("model unavailable")
        yield


def _runtime(events: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(stream_writer=events.append)


async def test_extracts_last_visible_ai_message_and_streams_events() -> None:
    llm = FakeLLM(["【核心结论】", "空气质量良。"])
    events: list[dict] = []
    state = {
        "messages": [
            HumanMessage(content="今天空气质量如何"),
            AIMessage(
                content="我先调用工具查询。",
                tool_calls=[
                    {
                        "name": "find_skill",
                        "args": {},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content='{"success": true}', tool_call_id="call_1"),
            AIMessage(content=""),
            AIMessage(content="【核心结论】空气质量良。"),
        ]
    }

    result = await FinalOutputCleanupMiddleware(
        model=llm,
        timeout_seconds=5,
    ).aafter_agent(state, _runtime(events))

    assert result is None
    assert llm.calls == 1
    assert "【核心结论】空气质量良。" in llm.prompts[0][0]["content"]
    assert "我先调用工具查询。" not in llm.prompts[0][0]["content"]
    assert events == [
        {
            "node": "final_output_cleanup",
            "type": "progress",
            "message": FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
        },
        {
            "node": "final_output_cleanup",
            "type": "final_output_delta",
            "message": "【核心结论】",
        },
        {
            "node": "final_output_cleanup",
            "type": "final_output_delta",
            "message": "空气质量良。",
        },
        {
            "node": "final_output_cleanup",
            "type": "final_output_done",
            "message": "【核心结论】空气质量良。",
        },
    ]


async def test_skips_when_no_visible_answer() -> None:
    llm = FakeLLM(["不会被调用"])
    events: list[dict] = []
    state = {
        "messages": [
            HumanMessage(content="今天空气质量如何"),
            ToolMessage(content="{}", tool_call_id="call_1"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_skill",
                        "args": {},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    }

    result = await FinalOutputCleanupMiddleware(
        model=llm,
        timeout_seconds=5,
    ).aafter_agent(state, _runtime(events))

    assert result is None
    assert llm.calls == 0
    assert events == []


async def test_finalizer_failure_falls_back_silently() -> None:
    llm = FailingLLM()
    events: list[dict] = []
    state = {"messages": [AIMessage(content="【核心结论】空气质量良。")]}

    result = await FinalOutputCleanupMiddleware(
        model=llm,
        timeout_seconds=5,
    ).aafter_agent(state, _runtime(events))

    assert result is None
    assert llm.calls == 1
    assert events == [
        {
            "node": "final_output_cleanup",
            "type": "progress",
            "message": FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
        }
    ]


async def test_empty_finalizer_output_does_not_push_done() -> None:
    llm = FakeLLM(["", None])
    events: list[dict] = []
    state = {"messages": [AIMessage(content="【核心结论】空气质量良。")]}

    result = await FinalOutputCleanupMiddleware(
        model=llm,
        timeout_seconds=5,
    ).aafter_agent(state, _runtime(events))

    assert result is None
    assert llm.calls == 1
    assert events == [
        {
            "node": "final_output_cleanup",
            "type": "progress",
            "message": FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
        }
    ]


async def test_unavailable_writer_skips_cleanup() -> None:
    llm = FakeLLM(["不会被调用"])
    state = {"messages": [AIMessage(content="【核心结论】空气质量良。")]}

    result = await FinalOutputCleanupMiddleware(
        model=llm,
        timeout_seconds=5,
    ).aafter_agent(state, SimpleNamespace(stream_writer=None))

    assert result is None
    assert llm.calls == 0


def test_cleanup_prompt_is_fidelity_oriented() -> None:
    prompt = build_final_output_cleanup_prompt("【核心结论】空气质量良。")

    assert "只是在下方" in prompt
    assert "删除过程性信息和内部实现信息" in prompt
    assert "不得重构、扩写、压缩、总结、重排" in prompt
    assert "不得补充新事实、新判断、新建议或新数据" in prompt
    assert "如果待清理正文已经干净，必须原样输出" in prompt
    assert "已生成的正文结构和表达风格" in prompt


def test_cleanup_prompt_covers_tool_errors() -> None:
    """确认清理提示词覆盖工具错误消息、状态码、超时和失败原因."""
    prompt = build_final_output_cleanup_prompt("正文")

    assert "工具错误消息" in prompt
    assert "502" in prompt
    assert "500" in prompt
    assert "timeout" in prompt
    assert "API 返回错误" in prompt


def test_cleanup_prompt_preserves_permission_correction() -> None:
    """确认清理提示词保留权限修正说明."""
    prompt = build_final_output_cleanup_prompt("正文")

    assert "权限修正说明" in prompt
    assert "不可删除" in prompt


def test_cleanup_prompt_preserves_markdown_and_data_details() -> None:
    prompt = build_final_output_cleanup_prompt("正文")

    assert "Markdown 标题、列表、表格" in prompt
    assert "日期、数值、单位" in prompt
    assert "段落顺序" in prompt
