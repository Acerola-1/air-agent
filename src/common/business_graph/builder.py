"""业务图构建器：basic_qa / intelligent_analysis 共享的显式节点流.

替代 create_deep_agent + 12 个中间件的 agent loop：

图结构（意图车道）:
    START -> classify_intent
      ├─ chitchat/knowledge -> prepare_model -> call_model [<-> execute_tools] -> finalize_output
      └─ data_query -> check_permission -> resolve_skill -> prepare_model
                    -> call_model [<-> execute_tools] -> finalize_output
    check_permission 硬拒绝时直接跳到 finalize_output。

Java 端消费契约（stream_mode=["custom","updates"]）保持不变：
progress / final_output_delta / final_output_done / rich_output /
expanded_questions 事件形状与原中间件实现一致。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from common.business_graph.intent import INTENT_DATA_QUERY
from common.business_graph.nodes import BusinessGraphNodes
from common.business_graph.prompting import DEFAULT_BUSINESS_SYSTEM_PROMPT
from common.business_graph.skill_content import create_load_skill_tool
from common.business_graph.state import BusinessGraphState
from common.business_graph.tool_wrappers import composed_tool_wrapper
from common.config.checkpointing import get_checkpointer
from common.runtime_tools import get_business_tools
from common.skill_router import SkillSemanticRouter


def _route_after_intent(
    state: BusinessGraphState,
) -> Literal["check_permission", "prepare_model"]:
    """条件边：意图车道分流——data_query 走权限+技能全流程；其余直达 prepare_model."""
    if (state.get("intent") or INTENT_DATA_QUERY) == INTENT_DATA_QUERY:
        return "check_permission"
    return "prepare_model"


def _route_after_permission(
    state: BusinessGraphState,
) -> Literal["resolve_skill", "finalize_output"]:
    """条件边：权限硬拒绝 -> finalize_output；否则继续技能路由."""
    if state.get("permission_rejection"):
        return "finalize_output"
    return "resolve_skill"


def _route_after_model(
    state: BusinessGraphState,
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
    state: BusinessGraphState,
) -> Literal["call_model", "finalize_output"]:
    """条件边：execute_tools 后路由——最后一条是 ToolMessage -> 回到 call_model；否则 -> finalize_output."""
    messages = state.get("messages", [])
    if not messages:
        return "finalize_output"

    last_message = messages[-1]
    if isinstance(last_message, ToolMessage):
        return "call_model"
    return "finalize_output"


def build_business_graph(
    *,
    skills_dir: str | Path,
    name: str,
    base_system_prompt: str = DEFAULT_BUSINESS_SYSTEM_PROMPT,
):
    """构建并编译业务图.

    Args:
        skills_dir: 当前业务图的 skills 目录
        name: 图名（与 langgraph.json assistant_id 对应，仅用于日志）
        base_system_prompt: 基础系统提示词，默认为通用空气质量助手提示

    Returns:
        编译后的 LangGraph 图实例。
    """
    router = SkillSemanticRouter(skills_dir)
    # 后台线程预热 embedding 索引，避免首个请求承担冷构建延迟；
    # 不阻塞 graph 导入（符合 agent-runtime-performance 轻量导入要求）
    threading.Thread(
        target=router.prewarm,
        name=f"skill-router-prewarm-{name}",
        daemon=True,
    ).start()

    load_skill_tool = create_load_skill_tool(router)
    nodes = BusinessGraphNodes(
        router=router,
        base_system_prompt=base_system_prompt,
        load_skill_tool=load_skill_tool,
        graph_name=name,
    )

    builder = StateGraph(BusinessGraphState)

    builder.add_node("classify_intent", nodes.classify_intent)
    builder.add_node("check_permission", nodes.check_permission)
    builder.add_node("resolve_skill", nodes.resolve_skill)
    builder.add_node("prepare_model", nodes.prepare_model)
    builder.add_node("call_model", nodes.call_model)
    builder.add_node(
        "execute_tools",
        ToolNode(
            tools=[*get_business_tools(), load_skill_tool],
            handle_tool_errors=True,
            awrap_tool_call=composed_tool_wrapper,
        ),
    )
    builder.add_node("finalize_output", nodes.finalize_output)

    builder.add_edge(START, "classify_intent")

    # 意图车道分流：闲聊/知识车道跳过权限审查与技能路由
    builder.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "check_permission": "check_permission",
            "prepare_model": "prepare_model",
        },
    )

    builder.add_conditional_edges(
        "check_permission",
        _route_after_permission,
        {
            "resolve_skill": "resolve_skill",
            "finalize_output": "finalize_output",
        },
    )

    builder.add_edge("resolve_skill", "prepare_model")
    builder.add_edge("prepare_model", "call_model")

    builder.add_conditional_edges(
        "call_model",
        _route_after_model,
        {
            "execute_tools": "execute_tools",
            "finalize_output": "finalize_output",
        },
    )

    builder.add_conditional_edges(
        "execute_tools",
        _route_after_tools,
        {
            "call_model": "call_model",
            "finalize_output": "finalize_output",
        },
    )

    builder.set_finish_point("finalize_output")

    graph = builder.compile(checkpointer=get_checkpointer())
    logger.info("业务图节点流编译完成: name={}，skills_dir={}", name, skills_dir)
    return graph
