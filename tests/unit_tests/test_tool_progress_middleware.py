"""工具进度中间件测试."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

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

TOOL_PROGRESS_MIDDLEWARE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "common"
    / "middleware"
    / "tool_progress_middleware.py"
)
spec = importlib.util.spec_from_file_location(
    "tool_progress_middleware_under_test",
    TOOL_PROGRESS_MIDDLEWARE_PATH,
)
assert spec is not None
assert spec.loader is not None
tool_progress_middleware = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool_progress_middleware
spec.loader.exec_module(tool_progress_middleware)
ToolProgressMiddleware = tool_progress_middleware.ToolProgressMiddleware
ProgressToolName = tool_progress_middleware.ProgressToolName
TOOL_PROGRESS_SPECS = tool_progress_middleware.TOOL_PROGRESS_SPECS


def _request(tool_name: str, events: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name},
        runtime=SimpleNamespace(stream_writer=events.append),
    )


def test_find_skill_progress_event_is_human_readable() -> None:
    middleware = ToolProgressMiddleware()
    events: list[dict[str, Any]] = []

    result = middleware.wrap_tool_call(
        _request("find_skill", events),
        lambda _request: "ok",
    )

    assert result == "ok"
    assert events == [
        {
            "type": "progress",
            "node": "skill_discovery",
            "name": "find_skill",
            "message": "正在寻找可用技能...",
        }
    ]


def test_keyword_progress_event_uses_business_stage() -> None:
    middleware = ToolProgressMiddleware()
    events: list[dict[str, Any]] = []

    middleware.wrap_tool_call(
        _request("search_policy_by_region", events),
        lambda _request: "ok",
    )

    assert events == [
        {
            "type": "progress",
            "node": "search",
            "name": "search_policy_by_region",
            "message": "正在查询相关政策...",
        }
    ]


def test_unknown_tool_uses_query_fallback() -> None:
    middleware = ToolProgressMiddleware()
    events: list[dict[str, Any]] = []

    middleware.wrap_tool_call(
        _request("unknown_business_tool", events),
        lambda _request: "ok",
    )

    assert events == [
        {
            "type": "progress",
            "node": "query",
            "name": "unknown_business_tool",
            "message": "正在查询相关数据...",
        }
    ]


def test_mcp_tool_uses_unified_external_query_progress() -> None:
    middleware = ToolProgressMiddleware()
    events: list[dict[str, Any]] = []

    middleware.wrap_tool_call(
        _request("mcp_city_common_get_air_quality_realtime_stat", events),
        lambda _request: "ok",
    )

    assert events == [
        {
            "type": "progress",
            "node": "mcp",
            "name": "mcp_city_common_get_air_quality_realtime_stat",
            "message": "正在查询相关数据...",
        }
    ]


def test_system_tool_progress_specs_cover_all_declared_tool_names() -> None:
    assert set(TOOL_PROGRESS_SPECS) == set(ProgressToolName)
    assert TOOL_PROGRESS_SPECS[ProgressToolName.LS].message == "正在浏览可用资料..."
    assert TOOL_PROGRESS_SPECS[ProgressToolName.EVAL].message == "正在执行辅助分析..."
