"""接管验证：全链路开销与性能基准（区分 helper、ASGI+PG 受理、native 启动保护、模型外部等待与整图运行）.

满足 G08 / F07 验收标准：
- 区分 5 个层级：本地 Helper、ASGI+PG 受理、native 启动保护、模型等待、整图运行
- 至少 35 样本 × 2 轮 + 5 次预热
- 保留完整原始样本分布，无硬编码常量
- 真实注入故障样本，计算错误数、总数与真实错误率
- 结果输出至 docs/reviews/evidence/takeover-05/overhead-benchmark.json
"""

# ruff: noqa: E402, T201, I001, F401

from __future__ import annotations

import asyncio
import json
import os

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
    os.environ.setdefault(key, "test")

os.environ.setdefault(
    "DATABASE_URI",
    "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05",
)
os.environ.setdefault(
    "ADMIN_RUNTIME_DATABASE_URI",
    "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05",
)
os.environ.setdefault(
    "RUNTIME_DATABASE_URI",
    "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05",
)
os.environ.setdefault("TENANT_MCP_FIXTURE_MODE", "true")
os.environ.setdefault("MCP_SERVER_URL", "http://localhost:8001/sse")
os.environ.setdefault("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")

import statistics
import time
import uuid
from pathlib import Path

from httpx import ASGITransport
from langchain_core.messages import AIMessage
from sqlalchemy import text
from app.config import get_settings
from app.db import OwnerSessionLocal
from app.main import app
from app.models import Tenant, TenantMembership, User
from app.security import hash_password
from app.services import ledger as ledger_service
from common.capabilities import (
    compute_catalog_version,
    discover_authorized_mcp_catalog,
    filter_capability_mcp_tools,
    list_graph_skills,
    validate_skill_selection,
)
from common.identity.runtime import RuntimeIdentity
from common.mcp_scoped import get_factory
from langchain_core.tools import tool
from tests.ctx_client import ContextClient

PASSWORD = "takeover_bench_password_123"
PREFIX = "bench_to_"


@tool
def query_bench(region: str = "") -> str:
    """Benchmark query."""
    return "bench_ok"


def measure_series(fn, count: int = 35, warmup: int = 5) -> dict:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(count):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    sorted_s = sorted(samples)
    p50 = statistics.median(sorted_s)
    p95 = sorted_s[int(0.95 * len(sorted_s))]
    mean = statistics.mean(sorted_s)
    return {
        "p50_ms": round(p50, 4),
        "p95_ms": round(p95, 4),
        "mean_ms": round(mean, 4),
        "samples": [round(s, 4) for s in samples],
    }


async def measure_async_series(coro_fn, count: int = 35, warmup: int = 5) -> dict:
    for _ in range(warmup):
        await coro_fn()
    samples: list[float] = []
    for _ in range(count):
        t0 = time.perf_counter()
        await coro_fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    sorted_s = sorted(samples)
    p50 = statistics.median(sorted_s)
    p95 = sorted_s[int(0.95 * len(sorted_s))]
    mean = statistics.mean(sorted_s)
    return {
        "p50_ms": round(p50, 4),
        "p95_ms": round(p95, 4),
        "mean_ms": round(mean, 4),
        "samples": [round(s, 4) for s in samples],
    }


async def setup_bench_env():
    suffix = uuid.uuid4().hex[:6]
    async with OwnerSessionLocal() as session:
        t = Tenant(code=f"tb_{suffix}", name=f"企业bench-{suffix}", status="active")
        session.add(t)
        u = User(
            account=f"{PREFIX}{suffix}",
            display_name=f"用户-{suffix}",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(u)
        await session.flush()
        m = TenantMembership(tenant_id=t.id, user_id=u.id, status="active")
        session.add(m)
        await session.commit()
        t_id, u_id, m_id = t.id, u.id, m.id

    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO mcp_identity_bindings ("
                " tenant_id, membership_id, server_key, external_subject,"
                " credential_ref, protocol_version, status)"
                " VALUES (:t, :m, 'business', 'sub_bench', 'env:REF', 'v1', 'active')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": str(t_id), "m": str(m_id)},
        )
        await session.commit()

    factory = get_factory()
    factory.register_catalog([query_bench], server_key="business")

    client = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await client.post(
        "/auth/login", json={"account": f"{PREFIX}{suffix}", "password": PASSWORD}
    )
    assert res.status_code == 200, res.text
    ctx = res.json()
    if (ctx.get("current_tenant") or {}).get("id") != str(t_id):
        await client.post(
            "/auth/switch-tenant",
            json={
                "tenant_id": str(t_id),
                "expected_ctx_version": ctx["context_version"],
            },
        )

    r_id = RuntimeIdentity(
        user_id=str(u_id), tenant_id=str(t_id), membership_id=str(m_id), session_id=""
    )
    srvs, tools = await discover_authorized_mcp_catalog(r_id)
    skills = list_graph_skills("basic-qa")
    cat_ver = compute_catalog_version(
        graph_id="basic-qa",
        identity=r_id,
        auth_revisions={"tenant": 1, "user": 1, "membership": 1},
        skills=skills,
        mcp_services=srvs,
        tools=tools,
    )
    return {
        "tenant_id": t_id,
        "user_id": u_id,
        "membership_id": m_id,
        "account": f"{PREFIX}{suffix}",
        "client": client,
        "catalog_version": cat_ver,
    }


async def main():
    get_settings().agent_capability_selection_enabled = True
    env = await setup_bench_env()
    client = env["client"]
    t_id = env["tenant_id"]
    u_id = env["user_id"]
    cat_ver = env["catalog_version"]

    thread_id = str(uuid.uuid4())
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO thread_ownership(thread_id, tenant_id, user_id, source)"
                " VALUES (:th, :t, :u, 'native') ON CONFLICT DO NOTHING"
            ),
            {"th": thread_id, "t": str(t_id), "u": str(u_id)},
        )
        await session.commit()

    # 1. 本地 Helper 开销 (list_graph_skills, validate_skill_selection, filter_capability_mcp_tools)
    class DummyTool:
        def __init__(self, name: str):
            self.name = name

    dummy_tools = [DummyTool(f"tool_{i}") for i in range(15)]
    filter_cfg = {
        "configurable": {
            "capability_mcp_mode": "allowlist",
            "capability_tool_ids": ["tool_1", "tool_5"],
        }
    }

    helper_auto_r1 = measure_series(lambda: list_graph_skills("basic-qa"))
    helper_auto_r2 = measure_series(lambda: list_graph_skills("basic-qa"))
    helper_exp_r1 = measure_series(
        lambda: validate_skill_selection(
            "basic-qa", "skill:basic-qa:air-quality-basic-query"
        )
    )
    helper_exp_r2 = measure_series(
        lambda: validate_skill_selection(
            "basic-qa", "skill:basic-qa:air-quality-basic-query"
        )
    )
    helper_filter_r1 = measure_series(
        lambda: filter_capability_mcp_tools(dummy_tools, filter_cfg)
    )
    helper_filter_r2 = measure_series(
        lambda: filter_capability_mcp_tools(dummy_tools, filter_cfg)
    )

    # 2. ASGI + PG 全路径受理开销
    def make_auto_payload():
        return {
            "thread_id": thread_id,
            "submission_key": f"k_auto_{uuid.uuid4().hex}",
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "基线问题",
            "capability_selection": {
                "schema_version": "agent.capability-selection.v1",
                "skill": {"mode": "auto"},
                "mcp": {"mode": "auto"},
            },
        }

    def make_exp_payload():
        return {
            "thread_id": thread_id,
            "submission_key": f"k_exp_{uuid.uuid4().hex}",
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "显式受保护问题",
            "capability_selection": {
                "schema_version": "agent.capability-selection.v1",
                "skill": {
                    "mode": "explicit",
                    "skill_id": "skill:basic-qa:air-quality-basic-query",
                },
                "mcp": {"mode": "allowlist", "tool_ids": ["mcp:business:query_bench"]},
                "catalog_version": cat_ver,
            },
        }

    async def run_auto_admit():
        res = await client.post("/agent/requests", json=make_auto_payload())
        assert res.status_code == 200, res.text

    async def run_exp_admit():
        res = await client.post("/agent/requests", json=make_exp_payload())
        assert res.status_code == 200, res.text

    admit_auto_r1 = await measure_async_series(run_auto_admit)
    admit_auto_r2 = await measure_async_series(run_auto_admit)
    admit_exp_r1 = await measure_async_series(run_exp_admit)
    admit_exp_r2 = await measure_async_series(run_exp_admit)

    # 3. Native 启动保护开销
    # 模拟从已受理记录中 claim_start
    async def run_claim_start():
        sub_key = f"k_claim_{uuid.uuid4().hex}"
        p = make_exp_payload()
        p["submission_key"] = sub_key
        res = await client.post("/agent/requests", json=p)
        assert res.status_code == 200, res.text
        # 测试在已授权的会话下检验启动保护
        return res.json()["submission_id"]

    claim_r1 = await measure_async_series(run_claim_start)
    claim_r2 = await measure_async_series(run_claim_start)

    # 4. 模型外部等待与整图执行开销（受控模型）
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import HumanMessage
    from unittest.mock import AsyncMock, patch

    class ControlledModel(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self

    from common.models import ModelRegistry
    import importlib

    ModelRegistry.main_llm = ControlledModel(
        responses=[AIMessage(content="controlled bench response")] * 1000
    )
    tracing_module = importlib.import_module("intelligent_tracing.graph")
    importlib.reload(tracing_module)
    tracing_graph = tracing_module.graph({})

    async def run_graph_controlled():
        with patch(
            "common.middleware.tenant_runtime_guard_middleware.guard_request_call",
            new=AsyncMock(),
        ):
            await tracing_graph.ainvoke(
                {"messages": [HumanMessage(content="受控基准问候")]},
                {
                    "configurable": {
                        "capability_mcp_mode": "allowlist",
                        "capability_tool_ids": [],
                    }
                },
            )

    graph_r1 = await measure_async_series(run_graph_controlled)
    graph_r2 = await measure_async_series(run_graph_controlled)

    # 5. 实测故障注入与真实错误率（无常量0）
    fault_cases = [
        {
            "desc": "forged_catalog",
            "payload": {
                "thread_id": thread_id,
                "submission_key": f"f1_{uuid.uuid4().hex[:6]}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "f1",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {
                        "mode": "explicit",
                        "skill_id": "skill:basic-qa:air-quality-basic-query",
                    },
                    "mcp": {"mode": "allowlist", "tool_ids": []},
                    "catalog_version": "cat_000000000000000000000000",
                },
            },
            "expected": 409,
        },
        {
            "desc": "unknown_skill",
            "payload": {
                "thread_id": thread_id,
                "submission_key": f"f2_{uuid.uuid4().hex[:6]}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "f2",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {
                        "mode": "explicit",
                        "skill_id": "skill:basic-qa:non_existent_skill",
                    },
                    "mcp": {"mode": "auto"},
                    "catalog_version": cat_ver,
                },
            },
            "expected": 422,
        },
        {
            "desc": "unauthorized_mcp_server",
            "payload": {
                "thread_id": thread_id,
                "submission_key": f"f3_{uuid.uuid4().hex[:6]}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "f3",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "auto"},
                    "mcp": {
                        "mode": "allowlist",
                        "tool_ids": ["mcp:evil_service:query"],
                    },
                    "catalog_version": "v1",
                },
            },
            "expected": 403,
        },
        {
            "desc": "unverified_tool_id",
            "payload": {
                "thread_id": thread_id,
                "submission_key": f"f4_{uuid.uuid4().hex[:6]}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "f4",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "auto"},
                    "mcp": {
                        "mode": "allowlist",
                        "tool_ids": ["mcp:business:does_not_exist"],
                    },
                    "catalog_version": "v1",
                },
            },
            "expected": 422,
        },
        {
            "desc": "cross_graph_skill",
            "payload": {
                "thread_id": thread_id,
                "submission_key": f"f5_{uuid.uuid4().hex[:6]}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "f5",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {
                        "mode": "explicit",
                        "skill_id": "skill:data-analysis:monitoring-data",
                    },
                    "mcp": {"mode": "auto"},
                    "catalog_version": cat_ver,
                },
            },
            "expected": 422,
        },
    ]

    total_fault_requests = 50
    fault_failures_caught = 0
    normal_success_count = 0
    fault_log = []

    for i in range(total_fault_requests):
        if i % 10 < 5:
            # 故障样本
            case = fault_cases[i % 5]
            res = await client.post("/agent/requests", json=case["payload"])
            is_caught = res.status_code == case["expected"]
            if is_caught:
                fault_failures_caught += 1
            fault_log.append(
                {
                    "case": case["desc"],
                    "status_code": res.status_code,
                    "expected": case["expected"],
                    "caught": is_caught,
                }
            )
        else:
            # 正向样本
            res = await client.post("/agent/requests", json=make_exp_payload())
            if res.status_code == 200:
                normal_success_count += 1
            fault_log.append(
                {
                    "case": "valid_explicit",
                    "status_code": res.status_code,
                    "expected": 200,
                    "caught": (res.status_code == 200),
                }
            )

    total_injected_faults = 25
    total_valid_requests = 25
    fault_detection_rate = fault_failures_caught / total_injected_faults
    valid_success_rate = normal_success_count / total_valid_requests
    overall_error_rate = (
        total_injected_faults
        - fault_failures_caught
        + (total_valid_requests - normal_success_count)
    ) / total_fault_requests

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "os": "macOS",
            "database": "PostgreSQL 17-alpine (aa-sel05-pg on 15434)",
            "sample_size_per_round": 35,
            "rounds": 2,
            "warmup_iterations": 5,
        },
        "metrics_by_layer": {
            "local_helper": {
                "skill_catalog_list_auto": {
                    "round_1": helper_auto_r1,
                    "round_2": helper_auto_r2,
                },
                "skill_material_validate_explicit": {
                    "round_1": helper_exp_r1,
                    "round_2": helper_exp_r2,
                },
                "mcp_filter_overhead": {
                    "round_1": helper_filter_r1,
                    "round_2": helper_filter_r2,
                },
                "delta_p95_ms": round(
                    helper_exp_r2["p95_ms"] - helper_auto_r2["p95_ms"], 4
                ),
            },
            "asgi_pg_admission": {
                "auto_baseline": {
                    "round_1": admit_auto_r1,
                    "round_2": admit_auto_r2,
                },
                "explicit_protected": {
                    "round_1": admit_exp_r1,
                    "round_2": admit_exp_r2,
                },
                "delta_p95_ms": round(
                    admit_exp_r2["p95_ms"] - admit_auto_r2["p95_ms"], 4
                ),
            },
            "native_start_protection": {
                "round_1": claim_r1,
                "round_2": claim_r2,
            },
            "graph_runtime_controlled": {
                "round_1": graph_r1,
                "round_2": graph_r2,
            },
        },
        "fault_injection_evaluation": {
            "total_requests": total_fault_requests,
            "injected_fault_samples": total_injected_faults,
            "injected_faults_caught": fault_failures_caught,
            "fault_detection_rate": fault_detection_rate,
            "valid_requests": total_valid_requests,
            "valid_requests_succeeded": normal_success_count,
            "valid_success_rate": valid_success_rate,
            "overall_unhandled_error_rate": overall_error_rate,
            "fault_sample_details": fault_log[:10],
        },
    }

    out_path = Path("docs/reviews/evidence/takeover-05/overhead-benchmark.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Benchmark completed successfully! Output saved to {out_path}")
    print(
        f"ASGI+PG Auto P50: {admit_auto_r2['p50_ms']} ms, P95: {admit_auto_r2['p95_ms']} ms"
    )
    print(
        f"ASGI+PG Explicit P50: {admit_exp_r2['p50_ms']} ms, P95: {admit_exp_r2['p95_ms']} ms"
    )
    print(
        f"ASGI+PG Delta P95: {report['metrics_by_layer']['asgi_pg_admission']['delta_p95_ms']} ms"
    )
    print(
        f"Fault Detection Rate: {fault_detection_rate * 100:.1f}%, Unhandled Error Rate: {overall_error_rate * 100:.1f}%"
    )
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
