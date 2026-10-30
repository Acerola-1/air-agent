"""自有恢复库的真实 RLS 读取与真实计数 MCP 发送边界."""

import asyncio
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from common.event_ledger import store
from common.tool_execution import policy
from common.tool_execution.mcp import execute_mcp_tool_request
from tests.unit_tests import test_graph_execution_policy as helpers
from tests.unit_tests.test_graph_execution_policy import boundary as boundary

_read_snapshot = store.fetch_capability_selection_snapshot
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("OWNERSHIP_SEND_PG_FIXTURE"),
        reason="requires the task-owned restored database runner",
    ),
]


@pytest.mark.parametrize("has_rules", [True, False])
async def test_actual_sdk_skill_observation(boundary, monkeypatch, has_rules):
    """当前 SDK 文件加载的成功／失败和执行观测真实写入运行角色的 PG."""
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage, HumanMessage

    from common.middleware.tenant_runtime_guard_middleware import (
        TenantRuntimeGuardMiddleware,
    )
    from common.observability import store as observations
    from common.observability.collector import get_collector, reset_collector_for_tests
    from common.observability.middleware import ObservabilityModelMiddleware

    class ScriptedModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    fixture = json.loads(Path(os.environ["OWNERSHIP_SEND_PG_FIXTURE"]).read_text())
    row = fixture["selection"]
    actor = replace(
        helpers.ident(),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        membership_id=row["membership_id"],
    )
    config = helpers._run_config(actor)
    config["configurable"].update(
        thread_id=row["thread_id"],
        ledger_request_id=row["request_id"],
        ledger_attempt_id=row["attempt_id"],
        ledger_submission_id=row["submission_id"],
        capability_selection_id=row["id"],
        capability_selection_digest=row["selection_digest"],
        capability_skill_mode="auto",
        capability_mcp_mode="auto",
    )
    connections = json.loads(os.environ["AGENT_MCP_CONNECTIONS_JSON"])
    connections["servers"][0]["tenant_id"] = actor.tenant_id
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(connections))
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    skill = f"controlled-file-{has_rules}"
    path = f"/skills/{skill}/SKILL.md"
    marker = "synthetic-skill-body-must-not-enter-events"
    files = {path: create_file_data(marker)} if has_rules else {}
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": path},
                        "id": "read-skill",
                    }
                ],
            ),
            AIMessage(content="合成执行完成"),
        ]
    )
    agent = create_deep_agent(
        model=model,
        backend=StateBackend(),
        middleware=[TenantRuntimeGuardMiddleware(), ObservabilityModelMiddleware()],
    )
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="读取合成规则")], "files": files},
            config=config,
        )
        assert result["messages"][-1].content == "合成执行完成"
        stats = await asyncio.to_thread(get_collector().flush, 5)
        assert stats["failed"] == stats["dropped"] == 0
        async with await psycopg.AsyncConnection.connect(
            fixture["owner_uri"], row_factory=psycopg.rows.dict_row
        ) as conn:
            spans = await (
                await conn.execute(
                    "SELECT stage,status,skill_version FROM agent_capability_spans WHERE attempt_id=%s AND span_kind='skill' AND span_name=%s",
                    (row["attempt_id"], skill),
                )
            ).fetchall()
            leaked = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM agent_events WHERE attempt_id=%s AND payload::text LIKE %s",
                    (row["attempt_id"], "%" + marker + "%"),
                )
            ).fetchone()
        assert {item["stage"] for item in spans} == (
            {"selected", "loaded", "executed"} if has_rules else {"selected", "loaded"}
        ), spans
        loaded = next(item for item in spans if item["stage"] == "loaded")
        assert loaded["status"] == ("succeeded" if has_rules else "failed")
        if has_rules:
            assert len(loaded["skill_version"]) == 64
            assert (
                next(item for item in spans if item["stage"] == "executed")[
                    "skill_version"
                ]
                == loaded["skill_version"]
            )
        assert leaked["n"] == 0
    finally:
        await get_collector().close()
        reset_collector_for_tests()
        await observations.close_pool()


async def test_membership_binding_and_deletion_during_policy(boundary, monkeypatch):
    """同用户成员更换和策略等待中删除都拒绝；同一有效选择先真实发送成功."""
    fixture = json.loads(Path(os.environ["OWNERSHIP_SEND_PG_FIXTURE"]).read_text())
    row = fixture["selection"]
    identity = replace(
        helpers.ident(),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        membership_id=row["membership_id"],
    )
    config = helpers._run_config(identity)
    config["configurable"].update(
        thread_id=row["thread_id"],
        ledger_attempt_id=row["attempt_id"],
        ledger_submission_id=row["submission_id"],
        capability_selection_id=row["id"],
        capability_selection_digest=row["selection_digest"],
        capability_mcp_mode=row["mcp_mode"],
        capability_tool_ids=row["tool_ids"],
    )
    connection = json.loads(os.environ["AGENT_MCP_CONNECTIONS_JSON"])
    connection["servers"][0]["tenant_id"] = identity.tenant_id
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(connection))
    monkeypatch.setattr(helpers, "ident", lambda: identity)
    monkeypatch.setattr(helpers, "_run_config", lambda _: config)
    monkeypatch.setattr(store, "fetch_capability_selection_snapshot", _read_snapshot)
    server, _ = boundary
    req, _ = await helpers.request_for(boundary, monkeypatch)
    # Current model definitions are sufficient; execution must not require load records.
    req.state.pop("bqa_capabilities")
    req.state.pop("bqa_turn_key")
    scope = store.LedgerScope(
        identity.tenant_id, identity.user_id, identity.membership_id
    )
    try:
        actual = await _read_snapshot(
            scope, attempt_id=row["attempt_id"], submission_id=row["submission_id"]
        )
        assert actual and actual["membership_id"] == identity.membership_id
        assert (await execute_mcp_tool_request(req)).status == "success"
        assert await server.call_count("subject-a") == 1

        # Same user and tenant, new membership: the old accepted selection is invisible.
        changed = replace(scope, membership_id=str(uuid.uuid4()))
        assert (
            await _read_snapshot(
                changed,
                attempt_id=row["attempt_id"],
                submission_id=row["submission_id"],
            )
            is None
        )
        req.runtime.config["configurable"]["langgraph_auth_user"]["membership_id"] = (
            changed.membership_id
        )
        assert (await execute_mcp_tool_request(req)).status == "error"
        assert await server.call_count("subject-a") == 1
        req.runtime.config["configurable"]["langgraph_auth_user"]["membership_id"] = (
            identity.membership_id
        )

        original = helpers.tool_policy.authorize_send
        deleted = False

        async def delete_while_policy_awaits(*args):
            nonlocal deleted
            validator = await original(*args)
            async with await psycopg.AsyncConnection.connect(
                fixture["owner_uri"]
            ) as conn:
                await conn.execute(
                    "INSERT INTO agent_thread_deletions(tenant_id,thread_id,requested_by,user_id) VALUES (%s,%s,%s,%s)",
                    (
                        identity.tenant_id,
                        row["thread_id"],
                        identity.user_id,
                        identity.user_id,
                    ),
                )
            deleted = True
            return validator

        monkeypatch.setitem(
            policy._POLICIES,
            "basic-qa",
            policy._Binding(delete_while_policy_awaits, True),
        )
        assert (await execute_mcp_tool_request(req)).status == "error"
        assert deleted
        assert (
            await _read_snapshot(
                scope, attempt_id=row["attempt_id"], submission_id=row["submission_id"]
            )
            is None
        )
        assert await server.call_count("subject-a") == 1
    finally:
        await store.close_pool()
