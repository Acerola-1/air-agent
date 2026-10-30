"""Basic QA 会话工作流构建器.

Basic QA: START → classify_intent（校验输入并恢复会话引用）→ prepare_model
→ call_model ⇄ execute_tools → answer_model → ledger_finalize → END。
classify_intent 是沿用的检查点标识，实际调用 prepare_session，不做意图分类。
搜索／加载是模型按需选择的工具，没有独立的 Skill 路由节点。
身份检查在受理与逐发送守卫，不另设 check_permission 节点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from basic_qa.nodes import BasicQANodes
from basic_qa.state import BasicQAInput, BasicQAState
from basic_qa.tool_policy import basic_qa_tool_wrapper
from common.event_ledger.lifecycle import make_ledger_finalize_node
from common.runtime_tools import get_business_tools
from common.skill_router import SkillSemanticRouter


def _route_after_model(
    state: BasicQAState,
) -> Literal["execute_tools", "answer_model", "__end__"]:
    """条件边：计划调工具 -> execute_tools；计划完成 -> answer_model；控制消息 -> END."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    if isinstance(last_message, AIMessage) and last_message.additional_kwargs.get(
        "ui_visible"
    ):
        return "__end__"
    return "answer_model"


def _route_after_tools(
    state: BasicQAState,
) -> Literal["call_model", "__end__"]:
    """条件边：execute_tools 后路由——最后一条是 ToolMessage -> 回到 call_model；否则 -> END."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, ToolMessage):
        return "call_model"
    return "__end__"


def build_basic_qa_graph(
    *,
    skills_dir: str | Path,
    name: str,
    checkpointer: Any = None,
):
    """构建并编译业务图.

    Args:
        skills_dir: 当前业务图的 skills 目录
        name: 公开图名，应与 langgraph.json 注册的 basic-qa 一致。
        checkpointer: 直接 Python 调用时可显式提供的持久化后端。
                     官方 Agent Server 入口传 None，由平台按部署配置注入后端；
                     本项目正式配置为 PostgreSQL。脱离平台直接调用且传 None 时，
                     不会自动持久化；隔离测试可显式传入自己的 checkpointer。

    Returns:
        编译后的 LangGraph 图实例。
    """
    router = SkillSemanticRouter(skills_dir)
    # 索引由首次真实匹配按需构建；图导入不启动Embedding外呼线程。
    # 运维若需预热，可在显式启动阶段调用router.prewarm，不能靠导入副作用。

    from basic_qa.capabilities.runtime import create_load_skill_tool

    load_skill_tool = create_load_skill_tool()
    nodes = BasicQANodes(
        router=router,
        load_skill_tool=load_skill_tool,
        graph_name=name,
    )

    builder = StateGraph(
        BasicQAState,
        input_schema=BasicQAInput,
    )

    # 该标识已写入有效旧检查点；方法名按实际职责命名，恢复入口保持稳定。
    builder.add_node("classify_intent", nodes.prepare_session)
    builder.add_node("prepare_model", nodes.prepare_model)
    builder.add_node("call_model", nodes.call_model)
    builder.add_node("answer_model", nodes.answer_model)
    extra_tools = [
        load_skill_tool,
        nodes._search_skills_tool,
        nodes._search_mcp_tool,
        nodes._load_mcp_tool,
        nodes._finish_planning_tool,
    ]
    tool_node = ToolNode(
        tools=[*get_business_tools(include_computation=True), *extra_tools],
        handle_tool_errors=False,
        awrap_tool_call=basic_qa_tool_wrapper,
    )

    async def execute_tools(state: BasicQAState, config: RunnableConfig):
        from basic_qa.capabilities.runtime import reject_conflicting_batch

        calls = state["messages"][-1].tool_calls
        rejected = reject_conflicting_batch(
            calls,
            disclosed_names=set(
                (state.get("bqa_disclosure") or {}).get("tools", {}).values()
            ),
        )
        if (
            not rejected
            and len(calls) > 1
            and any(c.get("name") == "finish_planning" for c in calls)
        ):
            rejected = [
                ToolMessage(
                    content="结束声明必须单独调用。",
                    tool_call_id=c["id"],
                    name=c["name"],
                    status="error",
                )
                for c in calls
            ]
        if rejected:
            return {"messages": rejected}
        from basic_qa.capabilities.runtime import LOAD_TOOLS

        if any(c.get("name") in LOAD_TOOLS for c in calls):
            # ToolNode runs ordinary business calls concurrently. Loads instead
            # see cumulative state and commit one merged checkpoint update.
            working = dict(state)
            merged: dict[str, Any] = {"messages": []}
            for call in calls:
                from basic_qa.tool_recovery import (
                    control_budget_result,
                    control_request_exhausted,
                )

                if control_request_exhausted(
                    [*state["messages"], *merged["messages"]], call
                ):
                    merged["messages"].append(control_budget_result(call))
                    continue
                single = state["messages"][-1].model_copy(update={"tool_calls": [call]})
                result = await tool_node.ainvoke(
                    {**working, "messages": [*state["messages"][:-1], single]},
                    config,
                )
                for update in result if isinstance(result, list) else [result]:
                    values = (
                        update.update
                        if hasattr(update, "update") and not isinstance(update, dict)
                        else update
                    )
                    for key, value in values.items():
                        if key == "messages":
                            merged[key].extend(value)
                        else:
                            merged[key] = value
                            working[key] = value
            from basic_qa.capabilities.runtime import trusted_turn_key
            from basic_qa.capabilities.state import touch_executed

            executed = {
                m.name
                for m in merged["messages"]
                if isinstance(m, ToolMessage)
                and m.additional_kwargs.get("network_attempted") is True
            }
            caps = touch_executed(working, executed, trusted_turn_key(config))
            if caps is not None:
                merged["bqa_capabilities"] = caps
            return merged
        result = await tool_node.ainvoke(state, config)
        from basic_qa.capabilities.runtime import trusted_turn_key
        from basic_qa.capabilities.state import touch_executed

        outputs = result if isinstance(result, list) else [result]
        receipts = []
        for output in outputs:
            values = (
                output.update
                if hasattr(output, "update") and not isinstance(output, dict)
                else output
            )
            receipts.extend(values.get("messages", []))
        executed = {
            m.name
            for m in receipts
            if isinstance(m, ToolMessage)
            and m.additional_kwargs.get("network_attempted") is True
        }
        caps = touch_executed(state, executed, trusted_turn_key(config))
        if caps is not None:
            if isinstance(result, dict):
                result["bqa_capabilities"] = caps
            else:
                from langgraph.types import Command

                result.append(Command(update={"bqa_capabilities": caps}))
        return result

    builder.add_node("execute_tools", execute_tools)

    # 台账生命周期落点（change add-agent-event-ledger）：终态与可见回答版本在
    # 图内节点落账，保持编译结果是 Graph 类型（with_listeners 会变成
    # RunnableBinding，平台加载器按 "Graph or Graph factory" 校验会拒绝）。
    # 节点内部失败关闭：无台账上下文/台账不可用时直接返回，不改变既有行为。
    builder.add_node(
        "ledger_finalize",
        make_ledger_finalize_node(name),
    )

    builder.add_edge(START, "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        lambda state: (
            "__end__" if state.get("intent") == "input_overflow" else "prepare_model"
        ),
        {
            "prepare_model": "prepare_model",
            "__end__": "ledger_finalize",
        },
    )

    builder.add_edge("prepare_model", "call_model")

    builder.add_conditional_edges(
        "call_model",
        _route_after_model,
        {
            "execute_tools": "execute_tools",
            "answer_model": "answer_model",
            "__end__": "ledger_finalize",
        },
    )

    builder.add_conditional_edges(
        "execute_tools",
        _route_after_tools,
        {
            "call_model": "call_model",
            "__end__": "ledger_finalize",
        },
    )

    builder.add_edge("answer_model", "ledger_finalize")
    builder.add_edge("ledger_finalize", END)

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("业务图节点流编译完成: name={}，skills_dir={}", name, skills_dir)
    return graph
