# ruff: noqa: E402
# 显式开启受控测试并设置无外呼占位配置后才导入运行模块。
"""真实受控MCP协议+真实PG绑定/观测；不代表生产企业协议已核验。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import OwnerSessionLocal
from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_observability_api import fixture

if os.environ.get("AIR_AGENT_OBSERVABILITY_MCP_TESTS") != "1":
    pytest.skip("显式开启本机受控MCP验收", allow_module_level=True)
# 公共配置模块要求这些键；本测试不调用模型或搜索，真实MCP地址由工厂显式传入。
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
    os.environ.setdefault(key, "controlled-unused")
pytest.importorskip("common.observability")
from common.identity.db import close_pool as close_identity_pool
from common.identity.runtime import RuntimeIdentity
from common.mcp_authorization import McpAuthorizationError
from common.mcp_scoped import ScopedMCPToolFactory
from common.observability import store
from common.observability.collector import get_collector, reset_collector_for_tests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from mcp_fixture.server import FixtureServer

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("AIR_AGENT_OBSERVABILITY_MCP_TESTS") != "1",
        reason="显式开启受控本机MCP协议与观测验收",
    ),
]


async def test_two_services_two_tenants_retry_cancel_and_revocation(ledger_env, monkeypatch):
    monkeypatch.setenv("TENANT_MCP_FIXTURE_MODE", "true")
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("AA35_CONTROLLED_A", "tok-tenant-a")
    monkeypatch.setenv("AA35_CONTROLLED_B", "tok-tenant-b")
    reset_collector_for_tests()
    c_a, d_a, a = await fixture(ledger_env)
    c_b, d_b, b = await fixture({**ledger_env, "tenant_a": ledger_env["tenant_b"]})
    one, two = FixtureServer(), FixtureServer()
    await one.start()
    await two.start()
    fac = ScopedMCPToolFactory({"controlled-one": one.url, "controlled-two": two.url})
    contexts = {"a": a, "b": b}
    try:
        async with OwnerSessionLocal() as session:
            for label, ctx in contexts.items():
                for server in ("controlled-one", "controlled-two"):
                    await session.execute(
                        text(
                            "INSERT INTO mcp_identity_bindings(tenant_id,member"
                            "ship_id,server_key,external_subject,credential_ref"
                            ",protocol_version,status) VALUES (:t,:m,:s,:subjec"
                            "t,:ref,'v1-fixture','active')"
                        ),
                        {
                            "t": ctx.tenant_id,
                            "m": ctx.membership_id,
                            "s": server,
                            "subject": "subject-" + label,
                            "ref": "env:AA35_CONTROLLED_" + label.upper(),
                        },
                    )
            await session.commit()

        def config(ctx, call):
            return {
                "configurable": {
                    "thread_id": ctx.thread_id,
                    "langgraph_auth_user": {
                        "identity": ctx.user_id,
                        "tenant_id": ctx.tenant_id,
                        "membership_id": ctx.membership_id,
                        "session_id": str(uuid.uuid4()),
                    },
                },
                "metadata": {
                    "ledger_request_id": ctx.request_id,
                    "ledger_attempt_id": ctx.attempt_id,
                    "ledger_submission_id": ctx.submission_id,
                    "ledger_graph_id": "basic-qa",
                    "obs_tool_call_id": call,
                },
            }

        def identity(ctx):
            return RuntimeIdentity(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                membership_id=ctx.membership_id,
                session_id=str(uuid.uuid4()),
            )

        calls = [
            fac.call(
                identity(ctx),
                server,
                "query_air",
                {"user_id": "forged", "region": "controlled"},
                run_config=config(ctx, "same-provider-call"),
            )
            for ctx in contexts.values()
            for server in ("controlled-one", "controlled-two")
        ]
        values = await asyncio.gather(*calls)
        assert len(values) == 4
        assert await one.call_count("subject-a") == 1 and await one.call_count("subject-b") == 1
        assert await two.call_count("subject-a") == 1 and await two.call_count("subject-b") == 1
        await one.set_flaky(2)
        retry_config = config(a, "same-retry-call")
        errors = 0
        for _ in range(3):
            try:
                result = await fac.call(
                    identity(a),
                    "controlled-one",
                    "flaky_query",
                    {"user_id": "forged", "region": "controlled"},
                    run_config=retry_config,
                )
                errors += bool(getattr(result, "is_error", False))
            except Exception:
                errors += 1
        assert errors == 2
        cancel = asyncio.create_task(
            fac.call(
                identity(a),
                "controlled-one",
                "delayed_query",
                {"user_id": "forged"},
                run_config=config(a, "cancelled-call"),
            )
        )
        before = await one.call_count("subject-a")
        for _ in range(40):
            if await one.call_count("subject-a") > before:
                break
            await asyncio.sleep(0.025)
        cancel.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel
        before = await one.call_count("subject-a")
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE mcp_identity_bindings SET status='disabled',auth_re"
                    "vision=auth_revision+1 WHERE tenant_id=:t AND server_key='"
                    "controlled-one'"
                ),
                {"t": a.tenant_id},
            )
            await session.commit()
        with pytest.raises(McpAuthorizationError):
            await fac.call(
                identity(a),
                "controlled-one",
                "query_air",
                {"user_id": "forged", "region": "controlled"},
                run_config=config(a, "revoked-call"),
            )
        assert await one.call_count("subject-a") == before
        stats = await asyncio.to_thread(get_collector().flush, 2)
        assert stats["failed"] == 0 and stats["pending"] == 0, stats
        async with OwnerSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT c.tenant_id::text,c.service_key,c.tool_name"
                            ",c.logical_call_id::text,a.retry_index,a.status,a."
                            "network_attempted FROM agent_tool_calls c JOIN age"
                            "nt_tool_call_attempts a ON a.tenant_id=c.tenant_id"
                            " AND a.logical_call_id=c.logical_call_id WHERE c.a"
                            "ttempt_id IN (:a,:b) ORDER BY c.service_key,c.tool"
                            "_name,a.retry_index"
                        ),
                        {"a": a.attempt_id, "b": b.attempt_id},
                    )
                )
                .mappings()
                .all()
            )
        retry = [r for r in rows if r["tool_name"] == "flaky_query"]
        assert [r["retry_index"] for r in retry] == [0, 1, 2] and len(
            {r["logical_call_id"] for r in retry}
        ) == 1, retry
        assert [r["status"] for r in retry] == [
            "technical_error",
            "technical_error",
            "succeeded",
        ], retry
        assert any(r["status"] == "cancelled" and r["network_attempted"] for r in rows)
        assert any(
            r["status"] == "permission_denied" and r["network_attempted"] is False for r in rows
        )
        query = [r for r in rows if r["tool_name"] == "query_air" and r["network_attempted"]]
        assert len(query) == 4 and len({r["logical_call_id"] for r in query}) == 4
        assert all(r["status"] == "succeeded" for r in query), query
        metrics_a = await c_a.get("/agent/observability/metrics")
        metrics_b = await c_b.get("/agent/observability/metrics")
        assert metrics_a.status_code == 200 and metrics_b.status_code == 200, (
            metrics_a.text,
            metrics_b.text,
        )
        groups_a = metrics_a.json()["tool_groups"]
        groups_b = metrics_b.json()["tool_groups"]
        assert sum(row["network_attempts"] for row in groups_a) == 6, groups_a
        assert sum(row["network_attempts"] for row in groups_b) == 2, groups_b
        assert sum(row["permission_denied"] for row in groups_a) == 1, groups_a
        assert sum(row["technical_error"] for row in groups_a) == 2, groups_a
        assert sum(row["cancelled"] for row in groups_a) == 1, groups_a
        proof = {
            "boundary": "真实受控MCP+真实绑定和PG；生产企业协议仍禁用",
            "observations": [dict(r) for r in rows],
            "collector": stats,
            "cross_tenant_network_calls": 4,
            "revoked_network_delta": 0,
        }
        (
            Path(__file__).resolve().parents[3]
            / "docs/reviews/evidence/takeover-03-05/controlled-mcp.json"
        ).write_text(json.dumps(proof, indent=2) + "\n")
    finally:
        await c_a.aclose()
        await c_b.aclose()
        await one.stop()
        await two.stop()
        await get_collector().close()
        reset_collector_for_tests()
        await store.close_pool()
        await close_identity_pool()
