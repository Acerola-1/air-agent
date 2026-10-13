"""结构化数据分析业务图入口（LangGraph 节点流实现）.

用 StateGraph + 显式节点替代 create_deep_agent 的 agent loop，
消除中间件链遍历开销，减少模型调用轮数。

图结构:
    START -> resolve_skill -> prepare_model -> call_model -> [execute_tools] -> call_model -> finalize_output -> END

call_model 后若包含 tool_calls 则进入 execute_tools，
工具执行完毕后回到 call_model，让模型基于工具结果生成最终答案，
然后进入 finalize_output 输出结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from common.business_graph.tool_wrappers import composed_tool_wrapper
from common.event_ledger.lifecycle import make_ledger_finalize_node
from common.langfuse_tracing import with_langfuse_tracing
from common.runtime_tools import get_business_tools
from data_analysis.nodes import (
    call_model,
    finalize_output,
    prepare_model,
    resolve_skill,
)
from data_analysis.state import DataAnalysisState

SKILLS_DIR = Path(__file__).parent / "skills"


def _build_tool_node() -> ToolNode:
    """构建工具执行节点.

    使用 composed_tool_wrapper 组合以下逻辑:
    - 工具进度推送
    - 动态工具实例绑定
    - MCP/通用异常兜底
    - 富输出推送 + ToolMessage 压缩
    """
    return ToolNode(
        tools=get_business_tools(include_computation=True),
        handle_tool_errors=False,
        awrap_tool_call=composed_tool_wrapper,
    )


def _route_after_model(
    state: DataAnalysisState,
) -> Literal["execute_tools", "finalize_output"]:
    """条件边：call_model 后路由——有 tool_calls -> execute_tools；无 -> finalize_output."""
    messages = state.get("messages", [])
    if not messages:
        return "finalize_output"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    return "finalize_output"


def _route_after_tools(
    state: DataAnalysisState,
) -> Literal["call_model", "finalize_output"]:
    """条件边：execute_tools 后路由——最后一条是 ToolMessage -> 回到 call_model 生成答案；否则 -> finalize_output."""
    messages = state.get("messages", [])
    if not messages:
        return "finalize_output"

    last_message = messages[-1]
    if isinstance(last_message, ToolMessage):
        return "call_model"
    return "finalize_output"


def build_graph(*, checkpointer: Any = None):
    """构建并编译 data_analysis 节点流图.

    Args:
        checkpointer: 持久化后端实例；默认 None 表示不绑定任何 checkpointer。
                     langgraph-api / langgraph dev 模式下由平台注入，调用方无需传入。
                     本地自建入口可显式传入 `get_checkpointer()` 拿到 SQLite 实例。

    Returns:
        编译后的 CompiledStateGraph。
    """
    builder = StateGraph(DataAnalysisState)

    # 添加节点
    builder.add_node("resolve_skill", resolve_skill)
    builder.add_node("prepare_model", prepare_model)
    builder.add_node("call_model", call_model)
    builder.add_node("execute_tools", _build_tool_node())
    builder.add_node("finalize_output", finalize_output)

    # 入口
    builder.add_edge(START, "resolve_skill")

    # 确定性链
    builder.add_edge("resolve_skill", "prepare_model")
    builder.add_edge("prepare_model", "call_model")

    # 条件边：call_model 后路由
    builder.add_conditional_edges(
        "call_model",
        _route_after_model,
        {
            "execute_tools": "execute_tools",
            "finalize_output": "finalize_output",
        },
    )

    # 条件边：execute_tools 后路由——回到 call_model 让模型基于工具结果生成答案
    builder.add_conditional_edges(
        "execute_tools",
        _route_after_tools,
        {
            "call_model": "call_model",
            "finalize_output": "finalize_output",
        },
    )

    # 出口：经台账收尾节点落终态与可见回答版本（change add-agent-event-ledger）。
    # 保持编译结果是 Graph 类型；节点在缺受理上下文/台账不可用时直接返回。
    builder.add_node("ledger_finalize", make_ledger_finalize_node("data-analysis"))
    builder.add_edge("finalize_output", "ledger_finalize")
    builder.set_finish_point("ledger_finalize")

    compiled = builder.compile(checkpointer=checkpointer)
    logger.info(
        "data_analysis 节点流图编译完成: checkpointer={}",
        type(checkpointer).__name__ if checkpointer else "None",
    )
    return compiled


graph = with_langfuse_tracing(build_graph(checkpointer=None))
