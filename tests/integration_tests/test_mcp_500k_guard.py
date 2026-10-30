"""端到端集成测试：50 万字符超大 MCP 输出治理全链路验证."""

from __future__ import annotations

import asyncio
from typing import Annotated
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from basic_qa.tool_runtime import composed_tool_wrapper
from common.storage.artifact_db import get_tool_artifact


# 1. 模拟工具定义
@tool
def normal_weather_tool(city: str) -> str:
    """查询指定城市的正常空气质量数据."""
    return f"【{city}空气质量报告】AQI: 55, 良, 首要污染物: PM10, 站点均值正常。"


@tool
def bad_overflow_mcp_tool(region: str) -> str:
    """模拟 Spring AI MCP Server：参数传空返回 50 万字符全国数据."""
    # 构造至少 500,000 字符的全国各区县 JSON 字符串
    prefix = '{"status": "success", "total": 3000, "data": ['
    item = '{"district": "某某区县", "aqi": 42, "pm25": 15},'
    fill_count = (500_000 // len(item)) + 10
    return (
        prefix + (item * fill_count) + '{"district": "海淀区", "aqi": 35, "pm25": 10}]}'
    )


class GuardTestState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def test_mcp_500k_overflow_end_to_end(tmp_path, monkeypatch):
    """端到端验证 50 万字符超限拦截、落库、Subagent提炼与数据保真."""

    async def _run():
        # 隔离数据库
        test_db = tmp_path / "integration_test.db"
        monkeypatch.setattr(
            "common.storage.artifact_db._get_sqlite_path", lambda: str(test_db)
        )
        monkeypatch.setattr(
            "common.storage.artifact_db._is_pg_configured", lambda: False
        )
        monkeypatch.setattr("common.storage.artifact_db._initialized", False)

        # Mock Subagent 的大模型推理输出（模拟精准抽取海淀区）
        mock_subagent_llm = AsyncMock()
        mock_subagent_llm.ainvoke.return_value = AIMessage(
            content="【海淀区空气质量提炼】从全量数据中检索到海淀区指标：AQI 35，优，PM2.5 为 10μg/m³。"
        )
        monkeypatch.setattr(
            "common.tool_output_summary.ModelRegistry.main_llm",
            mock_subagent_llm,
        )

        # 2. 构建带 Checkpointer 的 StateGraph
        builder = StateGraph(GuardTestState)
        tool_node = ToolNode(
            tools=[normal_weather_tool, bad_overflow_mcp_tool],
            handle_tool_errors=True,
            awrap_tool_call=composed_tool_wrapper,
        )
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)

        checkpointer = MemorySaver()
        graph = builder.compile(checkpointer=checkpointer)

        # 3. 构造请求：先包含一个正常工具调用，紧接着包含一个 50 万字符的超限调用
        normal_call_id = "call_normal_001"
        overflow_call_id = "call_overflow_002"

        human_msg = HumanMessage(content="查询北京市和海淀区的空气质量")
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": normal_call_id,
                    "name": "normal_weather_tool",
                    "args": {"city": "北京"},
                },
                {
                    "id": overflow_call_id,
                    "name": "bad_overflow_mcp_tool",
                    "args": {"region": ""},
                },
            ],
        )

        import base64 as _b64
        import hashlib as _hl
        import hmac as _hm
        import json as _json
        import time as _t

        monkeypatch.setenv("ARCHIVE_SCOPE_KEYS", _json.dumps(["it-scope-key"]))
        _body = (
            _b64.urlsafe_b64encode(
                _json.dumps(
                    {
                        "v": 2,
                        "kid": "k1",
                        "tenantId": "ta",
                        "userId": "u",
                        "threadId": "thread_test_500k",
                        "aud": "air-agent-archive",
                        "exp": _t.time() + 3600,
                    }
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        _sig = _hm.new(b"it-scope-key", _body.encode(), _hl.sha256).hexdigest()
        thread_config = {
            "configurable": {
                "thread_id": "thread_test_500k",
                "__artifact_scope": f"{_body}.{_sig}",
                "processing_run_id": "run_it",
            }
        }

        # 4. 执行图流转（验证 5.4.1: 主图未发生上下文超窗或异常崩溃）
        final_state = await graph.ainvoke(
            {"messages": [human_msg, ai_msg]},
            config=thread_config,
        )

        messages = final_state["messages"]
        assert len(messages) == 4  # HumanMessage + AIMessage + 2 条 ToolMessage

        normal_tm: ToolMessage = messages[2]
        overflow_tm: ToolMessage = messages[3]

        # 验证 5.4.4: 前面调用的正常工具数据未被误裁，完整保留
        assert normal_tm.tool_call_id == normal_call_id
        assert (
            normal_tm.content
            == "【北京空气质量报告】AQI: 55, 良, 首要污染物: PM10, 站点均值正常。"
        )
        assert normal_tm.artifact is None

        # 验证 5.4.3: ToolMessage.content 成功被 Subagent 浓缩至安全长度
        assert overflow_tm.tool_call_id == overflow_call_id
        assert len(overflow_tm.content) < 1500
        assert "【海淀区空气质量提炼】" in overflow_tm.content
        assert "AQI 35" in overflow_tm.content

        # 验证 5.4.2: 原始 50 万字符成功存入数据库专属表，能通过 artifact_id 查到
        assert isinstance(overflow_tm.artifact, dict)
        artifact_id = overflow_tm.artifact["artifact_id"]
        assert artifact_id.startswith("art_")
        assert overflow_tm.artifact["char_length"] >= 500_000

        db_record = await get_tool_artifact(
            artifact_id, tenant_id="ta", user_id="u", thread_id="thread_test_500k"
        )
        assert db_record is not None
        assert db_record["tool_call_id"] == overflow_call_id
        assert db_record["tool_name"] == "bad_overflow_mcp_tool"
        assert len(db_record["raw_payload"]) >= 500_000
        assert "海淀区" in db_record["raw_payload"]

        # 验证 5.4.5: Checkpointer 存储的 checkpoint 大小保持轻量
        saved_checkpoint = checkpointer.get(thread_config)
        assert saved_checkpoint is not None
        # checkpoint 中 ToolMessage.content 仅包含提炼内容，无 50 万原始字符
        saved_messages = saved_checkpoint["channel_values"]["messages"]
        saved_overflow_tm = saved_messages[-1]
        assert len(saved_overflow_tm.content) < 1500
        # 且 artifact 只有轻量引用
        assert len(str(saved_overflow_tm.artifact)) < 300

    asyncio.run(_run())
