"""业务图工具执行组合包装器.

将原来分散在多个 DeepAgents 中间件中的工具调用逻辑，
组合为一个 awrap_tool_call 函数，供 ToolNode 使用。
data_analysis 与 basic_qa / intelligent_analysis 节点流共用。

等效中间件:
- ToolProgressMiddleware → 推送进度事件
- SkillToolRegistryMiddleware → 动态绑定工具实例
- GlobalExceptionMiddleware → 异常兜底
- MCPResilienceMiddleware → MCP 工具降级
- ArtifactMiddleware → Fenced code block 扫描 → Artifact 画布推送 + 内容压缩
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import ToolMessage
from loguru import logger

from common.middleware.artifact_middleware import ArtifactMiddleware
from common.middleware.tool_progress_middleware import (
    ProgressSpec,
    ToolProgressMiddleware,
)
from common.runtime_tools import get_mcp_tool_names, tool_name

# MCP 远程工具代码级重试策略：替代原先写在各 skill 提示词里的
# "工具调用失败时，自动重试 1 次，间隔 2 秒"。代码级重试无需额外 LLM 轮次，
# 且能真正等待退避间隔；本地工具（text2sql/知识检索等）失败多为确定性错误，不重试。
_MCP_RETRY_ATTEMPTS = 1
_MCP_RETRY_BACKOFF_SECONDS = 2.0


def _get_progress_spec(tool_name_str: str) -> ProgressSpec:
    """获取工具对应的进度文案（复用 ToolProgressMiddleware 逻辑）."""
    middleware = ToolProgressMiddleware()
    return middleware._spec_for_tool(tool_name_str)


def _push_tool_progress(request: Any) -> None:
    """推送工具调用进度事件到前端."""
    writer = getattr(request.runtime, "stream_writer", None)
    if not callable(writer):
        return

    tool_name_str = str(request.tool_call.get("name") or "")
    spec = _get_progress_spec(tool_name_str)
    writer(
        {
            "type": "progress",
            "node": spec.node,
            "name": tool_name_str,
            "message": spec.message,
        }
    )


def _bind_tool_instance(request: Any) -> Any:
    """动态绑定工具实例（等效于 SkillToolRegistryMiddleware.awrap_tool_call）."""
    if request.tool is not None:
        return request

    name = request.tool_call.get("name")
    if not isinstance(name, str):
        return request

    # 从运行时业务工具列表中查找工具实例
    from common.runtime_tools import get_business_tools

    for t in get_business_tools():
        if tool_name(t) == name:
            return request.override(tool=t)

    return request


def _is_mcp_tool(request: Any) -> bool:
    """判断本次请求是否指向已知 MCP 远程工具（不含本地业务工具）."""
    tool_name_str = request.tool_call.get("name")
    if not isinstance(tool_name_str, str):
        return False
    return tool_name_str in set(get_mcp_tool_names())


async def _invoke_mcp_with_retry(
    request: Any,
    handler: Callable[[Any], Awaitable[Any]],
    first_exc: Exception,
) -> Any:
    """MCP 工具失败后的代码级重试；重试耗尽则降级为 _mcp_fallback."""
    last_exc = first_exc
    tool_name_str = str(request.tool_call.get("name") or "")
    for attempt in range(1, _MCP_RETRY_ATTEMPTS + 1):
        logger.warning(
            "MCP 工具调用失败，{}s 后重试({}/{}): tool={}, error={}",
            _MCP_RETRY_BACKOFF_SECONDS,
            attempt,
            _MCP_RETRY_ATTEMPTS,
            tool_name_str,
            last_exc,
        )
        await asyncio.sleep(_MCP_RETRY_BACKOFF_SECONDS)
        try:
            return await handler(request)
        except Exception as exc:
            last_exc = exc
    return _mcp_fallback(request, last_exc)


def _mcp_fallback(request: Any, exc: Exception) -> ToolMessage:
    """构造 MCP 工具降级 ToolMessage（等效于 MCPResilienceMiddleware）."""
    tool_name_str = str(request.tool_call.get("name") or "")
    tool_call_id = str(request.tool_call.get("id") or "")
    logger.exception(
        "MCP 工具调用失败，已转为降级 ToolMessage: tool={}, error={}",
        tool_name_str,
        exc,
    )

    # 推送降级状态事件
    writer = getattr(request.runtime, "stream_writer", None)
    if callable(writer):
        writer(
            {
                "node": "mcp_tool_call",
                "type": "external_service_status",
                "status": "unavailable",
                "tool_name": tool_name_str,
                "message": "暂未获取到相关数据，已跳过本次查询",
            }
        )

    payload = {
        "code": 503,
        "success": False,
        "message": "暂未获取到相关数据，请稍后重试。",
    }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=tool_name_str or None,
        status="error",
    )


def _global_fallback(request: Any, exc: Exception) -> ToolMessage:
    """构造通用工具异常降级 ToolMessage（等效于 GlobalExceptionMiddleware）."""
    tool_name_str = str(request.tool_call.get("name") or "")
    tool_call_id = str(request.tool_call.get("id") or "")
    logger.exception(
        "工具调用失败，已转为降级 ToolMessage: tool={}, error={}",
        tool_name_str,
        exc,
    )
    payload = {
        "code": 500,
        "success": False,
        "message": "暂未获取到相关数据，请稍后重试。",
    }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=tool_name_str or None,
        status="error",
    )


def _handle_artifact(request: Any, result: Any) -> Any:
    """扫描 ToolMessage 中的 fenced code block → 推送 Artifact 事件 + 压缩内容."""
    tool_name_str = str(request.tool_call.get("name") or "")
    writer = getattr(getattr(request, "runtime", None), "stream_writer", None)
    logger.info(
        "Artifact 诊断: tool={}, has_writer={}, result_type={}",
        tool_name_str,
        callable(writer),
        type(result).__name__,
    )
    middleware = ArtifactMiddleware()
    return middleware._handle_tool_result(request, result)


async def composed_tool_wrapper(
    request: Any,
    handler: Callable[[Any], Awaitable[Any]],
) -> Any:
    """组合工具调用包装器.

    执行顺序:
    1. 推送进度事件
    2. 动态绑定工具实例
    3. 执行工具（含异常兜底）
    4. 扫描 fenced code block 推送 Artifact 画布（create_artifact 工具跳过二次处理）
    """
    # 1. 推送进度
    _push_tool_progress(request)

    # 2. 动态绑定工具实例
    request = _bind_tool_instance(request)

    # 3. 执行工具（MCP 工具失败先代码级重试，再异常兜底）
    try:
        result = await handler(request)
    except Exception as exc:
        if _is_mcp_tool(request):
            result = await _invoke_mcp_with_retry(request, handler, exc)
        else:
            result = _global_fallback(request, exc)

    # 4. 扫描 fenced code block 推送 Artifact 画布
    result = _handle_artifact(request, result)

    return result
