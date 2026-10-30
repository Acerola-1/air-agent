"""v2入口按会话恢复能力，不返回旧逐句意图车道."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from basic_qa.nodes import BasicQANodes


@pytest.mark.anyio
@pytest.mark.parametrize(
    "question",
    [
        "湖州市今天的空气质量如何？",
        "那杭州呢？",
        "可能原因是什么？",
        "缩成两句。",
        "谢谢。",
    ],
)
async def test_v2_entry_keeps_conversation_in_native_loop(monkeypatch, question):
    from basic_qa.capabilities import projection

    initialize = AsyncMock(return_value={"bqa_turn_key": "attempt"})
    monkeypatch.setattr(projection, "initialize", initialize)
    service = BasicQANodes(
        router=SimpleNamespace(),
        load_skill_tool=None,
        graph_name="basic-qa",
    )
    result = await service.prepare_session(
        {"messages": [HumanMessage(content=question)]}, {}
    )
    assert result["intent"] == "data_query"
    assert result["bqa_turn_key"] == "attempt"
    assert initialize.await_count == 1
