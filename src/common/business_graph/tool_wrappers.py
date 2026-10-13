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

from common.artifact_scope import runtime_scope
from common.business_graph.subagent_summarizer import summarize_large_tool_output
from common.config import config
from common.context import get_message_content
from common.data_manifest import build_manifest, parse_payload
from common.runtime_events import event_payload
from common.runtime_tools import get_mcp_tool_names, tool_name
from common.storage.artifact_db import (
    claim_computation,
    save_tool_artifact,
    update_artifact_summary,
)

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

    for t in get_business_tools(include_computation=True):
        if tool_name(t) == name:
            return request.override(tool=t)

    return request


def _is_mcp_tool(request: Any) -> bool:
    """判断本次请求是否指向已知 MCP 远程工具（不含本地业务工具）."""
    tool_name_str = request.tool_call.get("name")
    if not isinstance(tool_name_str, str):
        return False
    return tool_name_str in set(get_mcp_tool_names())


async def execute_mcp_tool_request(request: Any) -> ToolMessage:
    """企业授权执行路径（两种图机制共用；任务 7.2/7.3）.

    直接 handler(request) 只会触达 schema 代理（其协程拒绝直接执行）。这里按
    已认证运行身份解析绑定后调用真实工具；授权错误转为可读降级 ToolMessage，
    响应不含凭据。调用**每次**重新解析绑定，重试天然复查授权。
    """
    from common.mcp_authorization import McpAuthorizationError
    from common.mcp_scoped import execute_mcp_tool, get_factory

    name = str(request.tool_call.get("name") or "")
    tool_call_id = str(request.tool_call.get("id") or "")
    server_key = get_factory().server_of(name) or ""
    run_config = getattr(request.runtime, "config", {}) or {}
    try:
        payload = await execute_mcp_tool(
            server_key, name, dict(request.tool_call.get("args") or {}), run_config
        )
    except McpAuthorizationError as exc:
        logger.warning("MCP 调用被授权层拒绝: tool={}, reason={}", name, str(exc)[:200])
        content = json.dumps(
            {"code": 403, "success": False, "message": str(exc)}, ensure_ascii=False
        )
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=name or None,
            status="error",
        )
    content = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name or None)


async def _invoke_mcp_with_retry(
    request: Any,
    handler: Callable[[Any], Awaitable[Any]],
    first_exc: Exception,
) -> Any:
    """MCP 工具失败后的代码级重试；重试耗尽则降级为 _mcp_fallback.

    F02/7.3：每次重试发送前重新核验身份与绑定授权（revalidate 查真实身份库）；
    授权拒绝直接终止重试并返回可读拒绝，不被瞬时错误重试吞掉。
    """
    from common.identity.runtime import (
        RuntimeIdentityError,
        identity_from_request,
        revalidate,
    )

    identity = identity_from_request(request)
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
        if identity is not None:
            try:
                await revalidate(identity)
            except RuntimeIdentityError as exc:
                return ToolMessage(
                    content=f"授权状态已变化，重试被拒绝：{exc}",
                    tool_call_id=str(request.tool_call.get("id") or ""),
                    name=tool_name_str,
                    status="error",
                )
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except RuntimeIdentityError as exc:
            return ToolMessage(
                content=f"授权状态已变化，重试被拒绝：{exc}",
                tool_call_id=str(request.tool_call.get("id") or ""),
                name=tool_name_str,
                status="error",
            )
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

    # 1.5 企业运行守卫：授权撤销/停用后下一次工具调用拒绝执行（任务 5.4/7.3）。
    # F02：走统一的请求级可信来源（get_config 环境/ToolRuntime.config/server_info），
    # 不再依赖 request.runtime.config 单一形状。
    from common.identity.runtime import RuntimeIdentityError, guard_request_call

    try:
        await guard_request_call(request)
    except RuntimeIdentityError as exc:
        return ToolMessage(
            content=json.dumps(
                {
                    "code": 403,
                    "success": False,
                    "message": f"授权状态已变化，工具调用被拒绝：{exc}",
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    # 2. 执行工具
    #    MCP 工具走企业授权执行层（每次调用重新解析绑定，重试前复查授权）；
    #    本地工具维持原 handler 路径。
    mcp = _is_mcp_tool(request)

    async def _run(req: Any) -> Any:
        if mcp:
            return await execute_mcp_tool_request(req)
        return await handler(req)

    try:
        result = await _run(request)
    except GraphBubbleUp:
        # ⚠️ 关键: 人机交互中断异常必须向上冒泡，严禁降级捕获！
        raise
    except Exception as exc:
        if (
            mcp
            and not isinstance(exc, (ValueError, TypeError))
            and getattr(exc, "status_code", 500) >= 500
        ):
            result = await _invoke_mcp_with_retry(request, _run, exc)
        else:
            result = _global_fallback(request, exc)

    # 3. 工具输出治理
    # Command 类型直接透传
    if isinstance(result, Command):
        return result

    if not isinstance(result, ToolMessage):
        return result
    raw_content = result.content
    content_str = (
        raw_content
        if isinstance(raw_content, str)
        else json.dumps(raw_content, ensure_ascii=False, default=str)
    )
    try:
        business = parse_payload(content_str)
    except (ValueError, TypeError):
        business = None
    is_error = result.status == "error"
    error_message = "工具调用未完成，请调整参数或稍后重试。"
    if isinstance(business, dict):
        code = str(business.get("code", ""))
        if business.get("success") is False or (code.isdigit() and int(code) >= 400):
            is_error = True
            error_message = (
                "参数校验失败，请调整查询条件。"
                if code in {"400", "422"}
                else "数据服务暂未完成查询，请稍后重试。"
            )
    if is_error:
        result = result.model_copy(
            update={
                "status": "error",
                "additional_kwargs": {
                    **result.additional_kwargs,
                    "error_message": error_message,
                },
            }
        )
    tool_name_str = str(request.tool_call.get("name") or result.name or "unknown_tool")
    tool_call_id = str(request.tool_call.get("id") or result.tool_call_id)
    run_config = getattr(request.runtime, "config", {}) or {}
    writer = getattr(request.runtime, "stream_writer", None)
    data_status = None
    if len(content_str) > config.TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS:
        artifact_id = None
        try:
            tenant_id, user_id, thread_id, run_id = runtime_scope(run_config)
            artifact_id = await save_tool_artifact(
                tool_call_id=tool_call_id,
                tool_name=tool_name_str,
                raw_payload=content_str,
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            if len(content_str.encode()) > config.QUICKJS_MAX_INPUT_BYTES:
                raise ValueError("资料超出当前单批全量处理配额，尚未完成")
            manifest = None if is_error else build_manifest(content_str, artifact_id)
            if manifest is not None:
                await claim_computation(
                    artifact_id,
                    run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    attempt=False,
                )
                reduced = json.dumps(manifest, ensure_ascii=False)
                data_status = event_payload(
                    run_config,
                    "context_status",
                    stage="data",
                    status="started",
                    artifact_id=artifact_id,
                    message="完整资料已保存，正在准备计算。",
                )
                if callable(writer):
                    writer(data_status)
            elif is_error:
                reduced = error_message
            else:
                if callable(writer):
                    writer(
                        event_payload(
                            run_config,
                            "context_status",
                            stage="data",
                            status="started",
                            artifact_id=artifact_id,
                        )
                    )
                reduced, coverage = await summarize_large_tool_output(
                    user_question=_extract_user_question(request),
                    tool_name=tool_name_str,
                    tool_args=request.tool_call.get("args") or {},
                    raw_output=content_str,
                    run_config=dict(run_config or {}),
                )
                await update_artifact_summary(
                    artifact_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    summary=reduced,
                    metadata=coverage,
                )
                data_status = event_payload(
                    run_config,
                    "context_status",
                    stage="data",
                    status="completed",
                    artifact_id=artifact_id,
                )
                if callable(writer):
                    writer(data_status)
            result = result.model_copy(
                update={
                    "content": reduced,
                    "artifact": {
                        "storage_type": "database",
                        "storage_status": "stored",
                        "artifact_id": artifact_id,
                        "tool_call_id": tool_call_id,
                        "char_length": len(content_str),
                    },
                }
            )
        except Exception:
            # 无法完整处理时明确失败，不能声称已保存或完整摘要。
            result = result.model_copy(
                update={
                    "status": "error",
                    "content": "资料整理未完成，无法据此提供完整统计。请重新查询或缩小范围。",
                    "artifact": {
                        "storage_type": "database",
                        "storage_status": "stored",
                        "artifact_id": artifact_id,
                    }
                    if artifact_id
                    else None,
                    "additional_kwargs": {
                        **result.additional_kwargs,
                        "error_message": "资料保存或整理失败，请重试。",
                    },
                }
            )
            data_status = event_payload(
                run_config,
                "context_status",
                stage="data",
                status="failed",
                artifact_id=artifact_id,
                message="资料保存或整理未完成。",
            )
            if callable(writer):
                writer(data_status)
    if callable(writer):
        writer(
            event_payload(
                run_config,
                "tool_status",
                status="failed" if result.status == "error" else "completed",
                tool_call_id=tool_call_id,
                tool_name=tool_name_str,
                message=result.additional_kwargs.get("error_message", "工具执行完成"),
            )
        )
    if data_status is not None:
        return Command(update={"messages": [result], "data_status": data_status})
    return result
