"""Basic QA 会话工作流构建器.

Basic QA: START → classify_intent（校验输入并恢复会话引用）→ prepare_model
→ call_model ⇄ execute_tools → answer_model → ledger_finalize → END。
节点 ID 是持久化契约；实现分别为 prepare_session、prepare_prompt、
plan_tools、ToolBatchExecutor.run_tools 和 compose_answer。
模型按需调用搜索／加载工具，身份检查由受理与逐发送守卫执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from loguru import logger

from basic_qa.nodes import BasicQANodes
from basic_qa.state import INPUT_OVERFLOW, BasicQAInput, BasicQAState
from basic_qa.tool_batches import ToolBatchExecutor
from common.event_ledger.lifecycle import make_ledger_finalize_node
from common.runtime_tools import get_business_tools
from common.skill_router import SkillSemanticRouter


def _route_after_session(
    state: BasicQAState,
) -> Literal["prepare_model", "__end__"]:
    """输入超限直接收尾，其余入口结果进入提示准备."""
    return "__end__" if state.get("intent") == INPUT_OVERFLOW else "prepare_model"


def _route_after_plan(
    state: BasicQAState,
) -> Literal["execute_tools", "answer_model", "__end__"]:
    """条件边：计划调工具 -> execute_tools；计划完成 -> answer_model；控制消息 -> END."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    # 中止回执只由 compose_answer 产生，且它直接连台账收尾，不会回到本路由。
    if isinstance(last_message, AIMessage) and last_message.additional_kwargs.get(
        "control_code"
    ):
        return "__end__"
    return "answer_model"


def _route_after_tool_calls(
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


def bound_node_callable(graph: Any, node: str) -> Any:
    """取回已编译节点绑定的可调用对象.

    LangGraph 把节点函数包在 ``StateNodeSpec.runnable`` 中，该结构可能随版本变化；
    适配集中在此处，调用方不直接依赖内部字段。
    """
    runnable = graph.builder.nodes[node].runnable
    return getattr(runnable, "afunc", None) or getattr(runnable, "func", None)


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

    # 节点 ID 用于检查点恢复；更名需要配套迁移。
    builder.add_node("classify_intent", nodes.prepare_session)
    builder.add_node("prepare_model", nodes.prepare_prompt)
    builder.add_node("call_model", nodes.plan_tools)
    builder.add_node("answer_model", nodes.compose_answer)
    tool_executor = ToolBatchExecutor(
        tools=[*get_business_tools(include_computation=True), *nodes.control_tools]
    )
    builder.add_node("execute_tools", tool_executor.run_tools)

    # 台账生命周期落点：终态与可见回答版本在
    # 图内节点落账，保持编译结果是 Graph 类型（with_listeners 会变成
    # RunnableBinding，平台加载器按 "Graph or Graph factory" 校验会拒绝）。
    # 收尾不修改图状态；缺台账上下文或台账不可用时不影响已有回答。
    builder.add_node(
        "ledger_finalize",
        make_ledger_finalize_node(name),
    )

    builder.add_edge(START, "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        _route_after_session,
        {
            "prepare_model": "prepare_model",
            "__end__": "ledger_finalize",
        },
    )

    builder.add_edge("prepare_model", "call_model")

    builder.add_conditional_edges(
        "call_model",
        _route_after_plan,
        {
            "execute_tools": "execute_tools",
            "answer_model": "answer_model",
            "__end__": "ledger_finalize",
        },
    )

    builder.add_conditional_edges(
        "execute_tools",
        _route_after_tool_calls,
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
