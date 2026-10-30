"""基础问答的官方 Agent Server 入口.

构图和条件边在 builder.py，节点实现在 nodes.py。
持久化由运行服务按部署配置注入，本项目正式运行使用 PostgreSQL；
本入口只构建业务图，不自行创建数据库连接或 checkpointer。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from loguru import logger

from application.business_policies import configure
from basic_qa.builder import build_basic_qa_graph
from basic_qa.tool_policy import authorize_send
from common.langfuse_tracing import with_langfuse_tracing
from common.tool_execution.policy import register_execution_policy

configure()

SKILLS_DIR = Path(__file__).parent / "skills"

register_execution_policy("basic-qa", authorize_send)

_graph = build_basic_qa_graph(
    skills_dir=SKILLS_DIR,
    name="basic-qa",
    # 正式 Agent Server 按 POSTGRES_URI 注入 PG checkpointer。
    # None 表示这里不自行绑定后端；单独在 Python 中调用时不会自动持久化。
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
