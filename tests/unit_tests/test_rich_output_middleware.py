"""富输出中间件测试."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from langchain_core.messages import ToolMessage

try:
    importlib.import_module("langchain.agents.middleware.types")
except ModuleNotFoundError:
    middleware_module = ModuleType("langchain.agents.middleware")
    middleware_types_module = ModuleType("langchain.agents.middleware.types")

    class AgentMiddleware:
        """LangChain 中间件基类的最小测试替身."""

    class ToolCallRequest:
        """LangChain 工具调用请求类型的最小测试替身."""

    middleware_types_module.AgentMiddleware = AgentMiddleware
    middleware_types_module.ToolCallRequest = ToolCallRequest
    middleware_module.types = middleware_types_module
    sys.modules["langchain.agents.middleware"] = middleware_module
    sys.modules["langchain.agents.middleware.types"] = middleware_types_module

RICH_OUTPUT_MIDDLEWARE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "common"
    / "middleware"
    / "rich_output_middleware.py"
)
spec = importlib.util.spec_from_file_location(
    "rich_output_middleware_under_test",
    RICH_OUTPUT_MIDDLEWARE_PATH,
)
assert spec is not None
assert spec.loader is not None
rich_output_middleware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rich_output_middleware)
RichOutputMiddleware = rich_output_middleware.RichOutputMiddleware


def _request(tool_name: str, events: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name},
        runtime=SimpleNamespace(stream_writer=events.append),
    )


def _tool_message(payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(content=json.dumps(payload), tool_call_id="call-1")


def test_rich_outputs_are_streamed_and_tool_message_is_compacted() -> None:
    middleware = RichOutputMiddleware()
    events: list[dict[str, Any]] = []

    result = middleware.wrap_tool_call(
        _request("generate_hour_aqi_chart", events),
        lambda _request: _tool_message(
            {
                "success": True,
                "code": 200,
                "message": "success",
                "data": {
                    "rich_outputs": [
                        {
                            "output_type": "chart",
                            "display_mode": "canvas",
                            "sequence": 1,
                            "title": "绍兴市昨日小时 AQI 趋势",
                            "chart_subtype": "line",
                            "data": {
                                "xAxis": {"type": "category", "data": ["01:00"]},
                                "series": [{"type": "line", "data": [45]}],
                            },
                        }
                    ]
                },
            }
        ),
    )

    assert events == [
        {
            "node": "deepagent_executor",
            "type": "rich_output",
            "output_type": "chart",
            "display_mode": "canvas",
            "chart_subtype": "line",
            "title": "绍兴市昨日小时 AQI 趋势",
            "tool_name": "generate_hour_aqi_chart",
            "sequence": 1,
            "message": {
                "xAxis": {"type": "category", "data": ["01:00"]},
                "series": [{"type": "line", "data": [45]}],
            },
        }
    ]
    assert isinstance(result, ToolMessage)
    assert json.loads(result.content) == {
        "code": 200,
        "success": True,
        "message": "工具已生成 1 个附件：绍兴市昨日小时 AQI 趋势。附件已发送给前端渲染，附件内容不进入对话上下文。",
    }


def test_non_attachment_tool_message_is_preserved() -> None:
    middleware = RichOutputMiddleware()
    events: list[dict[str, Any]] = []
    payload = {
        "success": True,
        "code": 200,
        "message": "success",
        "data": {"value": 42},
    }

    result = middleware.wrap_tool_call(
        _request("normal_tool", events),
        lambda _request: _tool_message(payload),
    )

    assert events == []
    assert isinstance(result, ToolMessage)
    assert json.loads(result.content) == payload


def test_chart_data_tool_message_is_preserved() -> None:
    middleware = RichOutputMiddleware()
    events: list[dict[str, Any]] = []
    payload = {
        "success": True,
        "code": 200,
        "data": {
            "chart_data": [{"x": "2026-05-13", "y": 80}],
        },
    }

    result = middleware.wrap_tool_call(
        _request("broadcastHour", events),
        lambda _request: _tool_message(payload),
    )

    assert events == []
    assert isinstance(result, ToolMessage)
    assert json.loads(result.content) == payload
