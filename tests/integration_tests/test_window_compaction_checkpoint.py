"""使用显式指定的自有临时PostgreSQL验证摘要检查点回读."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_compaction_and_recovery_survive_postgres_reopen(monkeypatch):
    uri = os.getenv("COMPACTION_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("需要本任务自建空库的 COMPACTION_TEST_DATABASE_URI")

    from psycopg.conninfo import conninfo_to_dict

    connection = conninfo_to_dict(uri)
    assert connection.get("host") in {"127.0.0.1", "localhost"}
    assert connection.get("dbname", "").startswith("compaction_check")
    for key in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MCP_SERVER_URL",
        "SILICONFLOW_API_KEY",
        "DEEPSEEK_API_KEY",
        "XFYUN_MAAS_API_KEY",
        "XIAOMI_MIMO_API_KEY",
        "SERPER_API_KEY",
        "ARK_API_KEY",
        "MAIN_LLM_API_KEY",
    ):
        if key not in os.environ:
            monkeypatch.setenv(key, "test")

    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph

    import common.context_preparation as preparation
    from common.business_graph.state import BusinessGraphState
    from common.config import config
    from common.model_capacity import capacity_from_window

    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_KEEP_TURNS", 3)
    summary_calls = []

    async def summarize(messages, previous_summary, **kwargs):
        summary_calls.append(kwargs["watermark"])
        return "已确认事实 PINNED_FACT_42"

    async def authorize(*args, **kwargs):
        return None

    monkeypatch.setattr(preparation, "summarize_conversation_history", summarize)
    monkeypatch.setattr("common.identity.runtime.guard_runtime_call", authorize)
    capacity = capacity_from_window(
        1600, role="main", output_reserved_tokens=100, safety_tokens=0
    )
    force_overflow = False
    provider_calls = 0

    async def prepare(state):
        nonlocal provider_calls
        prepared = await preparation.prepare_model_request(state, {}, capacity=capacity)
        if force_overflow:

            async def invoke(request):
                nonlocal provider_calls
                provider_calls += 1
                if provider_calls == 1:

                    class ProviderOverflow(Exception):
                        status_code = 400

                    raise ProviderOverflow("maximum context length exceeded")
                return AIMessage(content="已恢复")

            _, prepared = await preparation.call_with_context_recovery(
                invoke,
                prepared,
                state=state,
                run_config={},
                prepare_kwargs={"capacity": capacity},
            )
        return {**prepared.updates, "context_metrics": prepared.metrics}

    def compile_graph(saver):
        graph = StateGraph(BusinessGraphState)
        graph.add_node("prepare", prepare)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", END)
        return graph.compile(checkpointer=saver)

    history = [
        message
        for index in range(7)
        for message in (
            HumanMessage(
                content="历史资料" * 200 if index < 4 else f"问题{index}",
                id=f"h{index}",
            ),
            AIMessage(content=f"回答{index}", id=f"a{index}"),
        )
    ]
    run = {"configurable": {"thread_id": f"compaction-check-{uuid4().hex}"}}
    async with AsyncPostgresSaver.from_conn_string(uri) as saver:
        await saver.setup()
        graph = compile_graph(saver)
        first = await graph.ainvoke({"messages": history}, run)
        assert first["summary_covered_message_id"] == "a3"
        assert first["summary"] == "已确认事实 PINNED_FACT_42"
        assert len(first["messages"]) == len(history)

    async with AsyncPostgresSaver.from_conn_string(uri) as saver:
        graph = compile_graph(saver)
        reopened = await graph.aget_state(run)
        assert reopened.values["summary_covered_message_id"] == "a3"
        force_overflow = True
        await graph.ainvoke({"messages": [HumanMessage(content="继续", id="h7")]}, run)

    async with AsyncPostgresSaver.from_conn_string(uri) as saver:
        snapshot = await compile_graph(saver).aget_state(run)
        assert snapshot.values["summary_covered_message_id"] == "a4"
        assert snapshot.values["summary"] == "已确认事实 PINNED_FACT_42"
        assert snapshot.values["messages"][0].content == history[0].content
        assert len(snapshot.values["messages"]) == len(history) + 1
    assert provider_calls == 2
    assert summary_calls == [None, "a3"]
