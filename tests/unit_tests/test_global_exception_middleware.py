"""全局异常兜底中间件测试."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

try:
    importlib.import_module("langchain.agents.middleware.types")
except ModuleNotFoundError:
    middleware_module = ModuleType("langchain.agents.middleware")
    middleware_types_module = ModuleType("langchain.agents.middleware.types")

    class AgentMiddleware:
        """LangChain 中间件基类的最小测试替身."""

    class ModelRequest:
        """LangChain 模型请求类型的最小测试替身."""

        def __class_getitem__(cls, _item: Any) -> type["ModelRequest"]:
            return cls

    class ToolCallRequest:
        """LangChain 工具调用请求类型的最小测试替身."""

    middleware_types_module.AgentMiddleware = AgentMiddleware
    middleware_types_module.ModelRequest = ModelRequest
    middleware_types_module.ToolCallRequest = ToolCallRequest
    middleware_module.types = middleware_types_module
    sys.modules["langchain.agents.middleware"] = middleware_module
    sys.modules["langchain.agents.middleware.types"] = middleware_types_module

GLOBAL_EXCEPTION_MIDDLEWARE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "common"
    / "middleware"
    / "global_exception_middleware.py"
)
spec = importlib.util.spec_from_file_location(
    "global_exception_middleware_under_test",
    GLOBAL_EXCEPTION_MIDDLEWARE_PATH,
)
assert spec is not None
assert spec.loader is not None
global_exception_middleware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(global_exception_middleware)
GlobalExceptionMiddleware = global_exception_middleware.GlobalExceptionMiddleware
MODEL_USER_MESSAGE = global_exception_middleware.MODEL_USER_MESSAGE
TOOL_USER_MESSAGE = global_exception_middleware.TOOL_USER_MESSAGE


def _model_request(events: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(runtime=SimpleNamespace(stream_writer=events.append))


def _tool_request(tool_name: str, events: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": "call-1"},
        runtime=SimpleNamespace(stream_writer=events.append),
    )


def test_model_failure_returns_friendly_ai_message() -> None:
    middleware = GlobalExceptionMiddleware()
    events: list[dict[str, Any]] = []

    result = middleware.wrap_model_call(
        _model_request(events),
        lambda _request: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    assert isinstance(result, AIMessage)
    assert result.content == MODEL_USER_MESSAGE
    assert events == []


def test_tool_failure_returns_error_tool_message() -> None:
    middleware = GlobalExceptionMiddleware()
    events: list[dict[str, Any]] = []

    result = middleware.wrap_tool_call(
        _tool_request("local_tool", events),
        lambda _request: (_ for _ in ()).throw(ValueError("bad input")),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert json.loads(result.content) == {
        "code": 500,
        "success": False,
        "message": TOOL_USER_MESSAGE,
    }


@pytest.mark.anyio
async def test_async_model_failure_returns_friendly_ai_message() -> None:
    middleware = GlobalExceptionMiddleware()
    events: list[dict[str, Any]] = []

    async def failing_handler(_request: Any) -> Any:
        raise TimeoutError("timeout")

    result = await middleware.awrap_model_call(_model_request(events), failing_handler)

    assert isinstance(result, AIMessage)
    assert result.content == MODEL_USER_MESSAGE
