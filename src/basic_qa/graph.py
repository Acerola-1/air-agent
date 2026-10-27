"""基础问答空气质量查询图入口（LangGraph 显式节点流实现）.

原 deepagents 中间件链实现已由 common.business_graph 节点流替代：
权限审查、技能路由、提示词组装为确定性前置节点，
最终输出由 finalize_output 节点统一推送 final_output_delta/done 事件。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from loguru import logger

from common.business_graph import build_business_graph
from common.langfuse_tracing import with_langfuse_tracing

SKILLS_DIR = Path(__file__).parent / "skills"

_graph = build_business_graph(
    skills_dir=SKILLS_DIR,
    name="basic-qa",
    checkpointer=None,
)


def graph(config: RunnableConfig):
    """Bind Langfuse attributes per admitted execution, not at import time.

    The native worker restores the admitted attempt/thread config before calling
    this factory. Binding the official handler here lets LangChain propagate
    ``langfuse_user_id`` / ``langfuse_session_id`` to every child observation;
    schema and state reads reuse the compiled graph without starting a trace.
    """
    if not (config.get("configurable") or {}).get("__is_for_execution__"):
        return _graph
    try:
        return with_langfuse_tracing(_graph, config=config)
    except Exception as exc:  # noqa: BLE001 - observation failure cannot fail answers
        logger.warning("Langfuse run tracing unavailable: {}", type(exc).__name__)
        return _graph
