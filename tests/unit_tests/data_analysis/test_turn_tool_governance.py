"""当前轮多个较小工具结果累计超限时的有界治理回归."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from common.config import config
from data_analysis.context.tool_results import (
    apply_replacements,
    govern_current_turn_tools,
)


def turn_messages(count: int = 4, size: int = 6000):
    calls = [{"id": f"c{i}", "name": "query", "args": {"i": i}} for i in range(count)]
    return [
        HumanMessage(content="统计本月数据", id="h"),
        AIMessage(content="", tool_calls=calls, id="a"),
        *[
            ToolMessage(
                content="x" * size, tool_call_id=f"c{i}", name="query", id=f"t{i}"
            )
            for i in range(count)
        ],
    ]


@pytest.mark.anyio
async def test_cumulative_results_are_archived_and_replaced(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 20000)
    monkeypatch.setattr(
        "data_analysis.context.tool_results.runtime_scope",
        lambda config: ("tenant", "user", "thread", "run"),
    )
    saved = []

    async def save(**kwargs):
        saved.append(kwargs)
        return f"art_{len(saved)}"

    async def summarize(**kwargs):
        return ("摘要内容" * 20, {"complete": True})

    monkeypatch.setattr("data_analysis.context.tool_results.save_tool_artifact", save)
    monkeypatch.setattr(
        "data_analysis.context.tool_results.summarize_large_tool_output", summarize
    )

    messages = turn_messages()
    replacements = await govern_current_turn_tools(messages, {})
    assert replacements
    assert saved and saved[0]["raw_payload"] == "x" * 6000
    assert all(len(item.content) < 6000 for item in replacements)
    assert all(item.artifact["storage_status"] == "stored" for item in replacements)
    merged = apply_replacements(messages, replacements)
    assert [message.id for message in merged] == ["h", "a", "t0", "t1", "t2", "t3"]
    # 原历史不被原地修改。
    assert messages[2].content == "x" * 6000


@pytest.mark.anyio
async def test_structured_results_use_manifest_not_raw(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 20000)
    monkeypatch.setattr(
        "data_analysis.context.tool_results.runtime_scope",
        lambda config: ("tenant", "user", "thread", "run"),
    )
    rows = [{"station": f"s{i}", "pm25": i} for i in range(2000)]
    raw = json.dumps({"data": rows})

    async def save(**kwargs):
        return "art_json"

    monkeypatch.setattr("data_analysis.context.tool_results.save_tool_artifact", save)
    summarize = AsyncMock(
        side_effect=AssertionError("structured data must not summarize")
    )
    monkeypatch.setattr(
        "data_analysis.context.tool_results.summarize_large_tool_output", summarize
    )
    messages = [
        HumanMessage(content="统计", id="h"),
        AIMessage(
            content="",
            tool_calls=[{"id": "c", "name": "query", "args": {}}],
            id="a",
        ),
        ToolMessage(content=raw, tool_call_id="c", name="query", id="t"),
    ]
    replacements = await govern_current_turn_tools(messages, {})
    assert replacements
    payload = json.loads(replacements[0].content)
    assert payload["kind"] == "structured_data"
    assert payload["artifact_id"] == "art_json"
    summarize.assert_not_awaited()


@pytest.mark.anyio
async def test_failure_returns_none_without_partial_truth(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 20000)
    monkeypatch.setattr(
        "data_analysis.context.tool_results.runtime_scope",
        lambda config: ("tenant", "user", "thread", "run"),
    )

    async def save(**kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("data_analysis.context.tool_results.save_tool_artifact", save)
    assert await govern_current_turn_tools(turn_messages(), {}) is None
