"""真实已锁定DeepAgents执行器/回调与PG；LLM为显式受控fixture。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("deepagents")

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_observability_api import fixture

pytest.importorskip("common.observability")
from common.langfuse_tracing import with_langfuse_tracing
from common.middleware.tenant_runtime_guard_middleware import TenantRuntimeGuardMiddleware
from common.observability import store
from common.observability.collector import get_collector, reset_collector_for_tests
from common.observability.deepagents_adapter import observed_subagents, observed_summarization
from common.observability.middleware import ObservabilityModelMiddleware

pytestmark = pytest.mark.asyncio


class ControlledToolsModel(FakeMessagesListChatModel):
    """只决定测试中的调用次序；没有替换真实提供商的验收证据。"""

    def bind_tools(self, tools, **kwargs):
        return self


async def test_subagent_and_sdk_summary_have_distinct_observations(ledger_env, monkeypatch):
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:1/unused")
    for key in ("SILICONFLOW_API_KEY", "SERPER_API_KEY", "MAIN_LLM_API_KEY"):
        monkeypatch.setenv(key, "controlled-not-a-provider-key")
    c, data, ctx = await fixture(ledger_env)
    from common.identity.db import fetch_identity_state

    from app.security import parse_session_cookie

    session_id, _ = parse_session_cookie(c.cookies.get("air_agent_session"))
    state = await fetch_identity_state(session_id, user_id=ctx.user_id, tenant_id=ctx.tenant_id)
    assert state is not None
    config = {
        "configurable": {
            "langgraph_auth_user": {
                "identity": ctx.user_id,
                "tenant_id": ctx.tenant_id,
                "membership_id": ctx.membership_id,
                "session_id": session_id,
                "session_ctx_version": state.session_ctx_version,
                "user_auth_revision": state.user_auth_revision,
                "tenant_auth_revision": state.tenant_auth_revision,
                "membership_revision": state.membership_revision,
            },
            "thread_id": ctx.thread_id,
            "ledger_request_id": ctx.request_id,
            "ledger_attempt_id": ctx.attempt_id,
            "ledger_submission_id": ctx.submission_id,
            "ledger_graph_id": "basic-qa",
        }
    }
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    model = ControlledToolsModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "id": "controlled-task",
                        "args": {
                            "description": "直接回复子任务完成",
                            "subagent_type": "general-purpose",
                        },
                    }
                ],
                usage_metadata=usage,
            ),
            AIMessage(content="子任务完成", usage_metadata=usage),
            AIMessage(content="主任务完成", usage_metadata=usage),
        ]
    )
    backend = StateBackend()
    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[
            TenantRuntimeGuardMiddleware(),
            ObservabilityModelMiddleware(),
            observed_summarization(model, backend),
        ],
        subagents=observed_subagents(model, backend),
    )
    remote_enabled = os.environ.get("AIR_AGENT_AUX_LANGFUSE_TESTS") == "1"
    if remote_enabled:
        agent = with_langfuse_tracing(agent)
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="执行受控子任务")]}, config=config
        )
        assert result["messages"][-1].content == "主任务完成"
        summary_model = ControlledToolsModel(
            responses=[AIMessage(content="历史摘要", usage_metadata=usage)]
        )
        summary = observed_summarization(summary_model, backend)

        async def summarize(_):
            return await summary._acreate_summary([HumanMessage(content="历史fixture")])

        summary_run = RunnableLambda(summarize)
        if remote_enabled:
            summary_run = with_langfuse_tracing(summary_run)
        assert await summary_run.ainvoke({}, config=config) == "历史摘要"
        if remote_enabled:
            from langfuse import get_client

            await asyncio.to_thread(get_client().flush)
        stats = await asyncio.to_thread(get_collector().flush, 5)
        assert stats["failed"] == 0 and stats["dropped"] == 0, stats
        response = await c.get("/agent/observability/runs/" + ctx.attempt_id)
        assert response.status_code == 200, response.text
        observations = response.json()["model_observations"]
        assert sorted(row["role"] for row in observations) == [
            "main",
            "main",
            "subagent",
            "summarizer",
        ], observations
        assert len({row["logical_call_id"] for row in observations}) == 4, observations
        child = next(row for row in observations if row["role"] == "subagent")
        assert child["parent_observation_id"] in {
            row["observation_id"] for row in observations if row["role"] == "main"
        }, observations
        assert sum(row["usage"]["total_tokens"] for row in observations) == 60
        remote_proof = []
        if remote_enabled:
            for trace in response.json()["trace_links"]:
                for _ in range(20):
                    readback = await c.get("/agent/observability/traces/" + trace["trace_id"])
                    assert readback.status_code == 200, readback.text
                    body = readback.json()
                    if body["sync_state"] == "confirmed":
                        break
                    await asyncio.sleep(0.5)
                assert body["sync_state"] == "confirmed", body
                remote_proof.append(
                    {
                        "trace_id": trace["trace_id"],
                        "remote_ids": [row["id"] for row in body["observations"]],
                    }
                )
            assert sum(len(item["remote_ids"]) for item in remote_proof) == 4, remote_proof
        root = Path(__file__).resolve().parents[3]
        evidence_name = (
            "deepagents-langfuse-readback.json" if remote_enabled else "deepagents-auxiliary.json"
        )
        (root / "docs/reviews/evidence/takeover-03-05" / evidence_name).write_text(
            json.dumps(
                {
                    "boundary": (
                        "真实DeepAgents执行器/受限身份查询/PG；模型为受控fixture，不冒充真实LLM"
                    ),
                    "observations": [
                        {
                            key: row.get(key)
                            for key in [
                                "observation_id",
                                "logical_call_id",
                                "role",
                                "parent_observation_id",
                                "submission_id",
                                "usage",
                            ]
                        }
                        for row in observations
                    ],
                    "collector": stats,
                    "real_langfuse_readback": remote_proof,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    finally:
        await c.aclose()
        await get_collector().close()
        reset_collector_for_tests()
        await store.close_pool()


@pytest.mark.parametrize("has_rules", [True, False])
async def test_actual_find_skill_load_success_and_failure_stages(
    ledger_env, monkeypatch, tmp_path, has_rules
):
    """候选由受控router提供；真实读文件/工具/Agent调用与PG，不评价Embedding质量。"""
    from types import SimpleNamespace

    from common.identity.db import fetch_identity_state
    from common.skill_discovery import create_find_skill_tool
    from common.skill_router import SkillRouteCandidate
    from sqlalchemy import text

    from app.db import OwnerSessionLocal
    from app.security import parse_session_cookie

    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:1/unused")
    for key in ["SILICONFLOW_API_KEY", "MAIN_LLM_API_KEY", "SERPER_API_KEY"]:
        monkeypatch.setenv(key, "controlled-not-a-provider-key")
    c, data, ctx = await fixture(ledger_env)
    sid, _ = parse_session_cookie(c.cookies.get("air_agent_session"))
    identity = await fetch_identity_state(sid, user_id=ctx.user_id, tenant_id=ctx.tenant_id)
    config = {
        "configurable": {
            "langgraph_auth_user": {
                "identity": ctx.user_id,
                "tenant_id": ctx.tenant_id,
                "membership_id": ctx.membership_id,
                "session_id": sid,
                "session_ctx_version": identity.session_ctx_version,
                "user_auth_revision": identity.user_auth_revision,
                "tenant_auth_revision": identity.tenant_auth_revision,
                "membership_revision": identity.membership_revision,
            },
            "thread_id": ctx.thread_id,
            "ledger_request_id": ctx.request_id,
            "ledger_attempt_id": ctx.attempt_id,
            "ledger_submission_id": ctx.submission_id,
            "ledger_graph_id": "basic-qa",
        }
    }
    path = tmp_path / "SKILL.md"
    marker = "controlled-skill-body-must-not-enter-events"
    path.write_text(marker if has_rules else "")

    async def match(_):
        return [
            SkillRouteCandidate(
                "controlled-skill", "受控候选", "/skills/controlled-skill/SKILL.md", (), 1.0
            )
        ]

    router = SimpleNamespace(amatch=match, resolve_skill_file=lambda _: path)
    tool = create_find_skill_tool(router)
    model = ControlledToolsModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "find_skill", "args": {"question": "受控问题"}, "id": "skill-call"}
                ],
            ),
            AIMessage(content="受控执行完成"),
        ]
    )
    agent = create_deep_agent(
        model=model,
        tools=[tool],
        middleware=[TenantRuntimeGuardMiddleware(), ObservabilityModelMiddleware()],
    )
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="受控问题")]}, config=config
        )
        assert result["messages"][-1].content == "受控执行完成"
        stats = await asyncio.to_thread(get_collector().flush, 5)
        assert stats["failed"] == stats["dropped"] == 0, stats
        async with OwnerSessionLocal() as session:
            spans = list(
                (
                    await session.execute(
                        text(
                            "SELECT stage,status,skill_version FROM agent_capability_spans "
                            "WHERE attempt_id=:a AND span_kind='skill' ORDER BY created_at"
                        ),
                        {"a": ctx.attempt_id},
                    )
                ).mappings()
            )
            leaked = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_events "
                    "WHERE attempt_id=:a AND payload::text LIKE :marker"
                ),
                {"a": ctx.attempt_id, "marker": "%" + marker + "%"},
            )
        assert {r["stage"] for r in spans} == (
            {"matched", "selected", "loaded", "executed"}
            if has_rules
            else {"matched", "selected", "loaded"}
        ), spans
        loaded = next(r for r in spans if r["stage"] == "loaded")
        assert loaded["status"] == ("succeeded" if has_rules else "failed"), spans
        if has_rules:
            assert len(loaded["skill_version"]) == 64
            assert (
                next(r for r in spans if r["stage"] == "executed")["skill_version"]
                == loaded["skill_version"]
            )
        assert leaked == 0
        root = Path(__file__).resolve().parents[3]
        (root / f"docs/reviews/evidence/takeover-03-05/skill-stages-{has_rules}.json").write_text(
            json.dumps(
                {
                    "boundary": "受控候选和模型；真实文件、DeepAgents工具执行器、身份核验和PG",
                    "spans": [dict(r) for r in spans],
                    "body_in_events": bool(leaked),
                    "collector": stats,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    finally:
        await c.aclose()
        await get_collector().close()
        reset_collector_for_tests()
        await store.close_pool()
