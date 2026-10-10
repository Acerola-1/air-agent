"""业务图工具执行组合包装器.

将原来分散在多个 DeepAgents 中间件中的工具调用逻辑，
组合为一个 awrap_tool_call 函数，供 ToolNode 使用。
data_analysis 与 basic_qa / intelligent_analysis 节点流共用。

等效中间件:
- SkillToolRegistryMiddleware → 动态绑定工具实例
- GlobalExceptionMiddleware → 异常兜底
- MCPResilienceMiddleware → MCP 工具降级
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from loguru import logger

from common.business_graph.subagent_summarizer import summarize_large_tool_output
from common.config import config
from common.context import get_message_content
from common.runtime_tools import get_mcp_tool_names, tool_name
from common.storage.artifact_db import save_tool_artifact

# MCP 远程工具代码级重试策略：替代原先写在各 skill 提示词里的
# "工具调用失败时，自动重试 1 次，间隔 2 秒"。代码级重试无需额外 LLM 轮次，
# 且能真正等待退避间隔；本地工具（text2sql/知识检索等）失败多为确定性错误，不重试。
_MCP_RETRY_ATTEMPTS = 1
_MCP_RETRY_BACKOFF_SECONDS = 2.0


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


def _extract_user_question(request: Any) -> str:
    """从 request.state 中提取最近一条用户提问."""
    state = getattr(request, "state", None)
    if not isinstance(state, dict):
        return ""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return get_message_content(msg)
    return ""


async def composed_tool_wrapper(
    request: Any,
    handler: Callable[[Any], Awaitable[Any]],
) -> Any:
    """组合工具调用包装器.

    执行顺序:
    1. 动态绑定工具实例
    2. 执行工具（透传 GraphBubbleUp，MCP 工具失败代码级重试，通用异常兜底）
    3. 工具输出治理：超限自动落库至专属表并调度 Subagent 提炼，防止主上下文膨胀
    """
    # 1. 动态绑定工具实例
    request = _bind_tool_instance(request)

    # 2. 执行工具
    try:
        result = await handler(request)
    except GraphBubbleUp:
        # ⚠️ 关键: 人机交互中断异常必须向上冒泡，严禁降级捕获！
        raise
    except Exception as exc:
        if _is_mcp_tool(request):
            result = await _invoke_mcp_with_retry(request, handler, exc)
        else:
            result = _global_fallback(request, exc)

    # 3. 工具输出治理
    # Command 类型直接透传
    if isinstance(result, Command):
        return result

    if isinstance(result, ToolMessage):
        raw_content = result.content
        if isinstance(raw_content, str):
            content_str = raw_content
        else:
            try:
                content_str = json.dumps(raw_content, ensure_ascii=False) if raw_content else ""
            except Exception:
                content_str = str(raw_content)

        threshold = getattr(config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 20000)
        if len(content_str) > threshold:
            tool_name_str = str(request.tool_call.get("name") or getattr(result, "name", "") or "unknown_tool")
            tool_call_id = str(request.tool_call.get("id") or getattr(result, "tool_call_id", "") or "")
            tool_args = request.tool_call.get("args") or {}
            user_question = _extract_user_question(request)

            # 3.1 原始超大载荷写入专属数据库表
            try:
                artifact_id = await save_tool_artifact(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name_str,
                    raw_payload=content_str,
                )
            except Exception as db_err:
                logger.error("超大工具输出持久化失败: tool={}, error={}", tool_name_str, db_err)
                artifact_id = "storage_failed"

            # 3.2 调度独立 192K Subagent 精准提炼
            summary_text = await summarize_large_tool_output(
                user_question=user_question,
                tool_name=tool_name_str,
                tool_args=tool_args,
                raw_output=content_str,
            )

            # 3.3 替换为轻量内容并保留 artifact 数据库引用
            artifact_meta = {
                "storage_type": "database",
                "table": "tool_artifact_storage",
                "artifact_id": artifact_id,
                "tool_call_id": tool_call_id,
                "char_length": len(content_str),
            }
            return result.model_copy(
                update={
                    "content": summary_text,
                    "artifact": artifact_meta,
                }
            )

    return result

