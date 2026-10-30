"""数据图工具失败预算与公共包装装配契约."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from data_analysis import tool_policy


def rejected(name, code):
    return ToolMessage(
        name=name, content=f'{{"code":{code},"success":false}}', tool_call_id="c"
    )


def test_failures_are_bounded_within_current_turn():
    messages = [
        HumanMessage(content="本轮"),
        rejected("denied", 403),
        rejected("bad", 422),
    ]
    assert tool_policy.exhausted_tools(messages) == {"denied"}
    messages.append(rejected("bad", 400))
    assert tool_policy.exhausted_tools(messages) == {"denied", "bad"}
    messages.append(HumanMessage(content="下一轮"))
    assert tool_policy.exhausted_tools(messages) == set()


@pytest.mark.anyio
async def test_wrapper_supplies_current_turn_budget(monkeypatch):
    wrapped = AsyncMock(return_value="result")
    monkeypatch.setattr(tool_policy, "_composed_tool_wrapper", wrapped)
    request = SimpleNamespace(
        state={"messages": [HumanMessage(content="问"), rejected("x", 403)]}
    )
    handler = AsyncMock()
    assert await tool_policy.composed_tool_wrapper(request, handler) == "result"
    wrapped.assert_awaited_once_with(request, handler, exhausted_tools={"x"})
