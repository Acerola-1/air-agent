"""任务 1.2 探针二：run metadata 可控性、恢复入口与状态可校准性。

回答：
A. `POST /threads/{tid}/runs` 的 `metadata` 是否被持久化并可由 GET run 回读？
B. `configurable` 自定义键（非保留键）是否透传并出现在 run.kwargs？
C. 恢复入口：`command.resume` 新建运行的返回与 run 关系（新 run_id？同线程？）。
D. 运行列表按状态/count 检索能力（校准"是否有未关联运行"）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

ADMIN = os.environ.get("LIVE_ADMIN_URL", "http://127.0.0.1:8100")
NATIVE = os.environ.get("LIVE_NATIVE_URL", "http://127.0.0.1:2024")
INTERNAL = os.environ.get("INTERNAL_API_TOKEN", "")
PASSWORD = os.environ.get("REVIEW_FIXTURE_PASSWORD", "local-e2e-pass")
GRAPH = "basic-qa"


async def _login(account: str):
    c = httpx.AsyncClient(base_url=ADMIN, timeout=30)
    r = await c.post("/auth/login", json={"account": account, "password": PASSWORD})
    r.raise_for_status()
    return c, r.json()


async def _switch(c, ctx, tid):
    if (ctx.get("current_tenant") or {}).get("id") == tid:
        return ctx
    r = await c.post(
        "/auth/switch-tenant",
        json={"tenant_id": tid, "expected_ctx_version": ctx["context_version"]},
    )
    r.raise_for_status()
    return r.json()


async def _ticket(c, ctx):
    r = await c.post(
        "/auth/runtime-ticket",
        headers={"x-internal-token": INTERNAL},
        json={"target_tenant_id": ctx["current_tenant"]["id"]},
    )
    r.raise_for_status()
    return r.json()["ticket"]


async def _admit(
    admin: httpx.AsyncClient, ctx: dict, tenant_id: str, thread_id: str, graph: str
) -> dict:
    """Change add-agent-event-ledger 起：原生运行入口要求台账受理预留。

    本探针记录的是**镜像自身能力**（run_id 指定/幂等/metadata/恢复），这些结论不受
    预留校验影响；但探针要能继续复跑，就必须先经可信台账受理。
    """
    res = await admin.post(
        "/agent/requests",
        headers={
            "x-tenant-id": tenant_id,
            "x-tenant-context-version": str(ctx["context_version"]),
        },
        json={
            "thread_id": thread_id,
            "submission_key": uuid.uuid4().hex,
            "graph_id": graph,
            "operation_kind": "initial",
            "content": f"probe {graph}",
        },
    )
    res.raise_for_status()
    return res.json()


async def main() -> None:
    out: dict[str, object] = {}
    admin, ctx = await _login("acc_alice")
    tenant = next(
        (
            i["tenant"]["id"]
            for i in ctx["tenants"]
            if i["tenant"]["name"].endswith("A")
        ),
        ctx["tenants"][0]["tenant"]["id"],
    )
    ctx = await _switch(admin, ctx, tenant)
    ticket = await _ticket(admin, ctx)
    h = {"Authorization": f"Bearer {ticket}", "content-type": "application/json"}
    thread_id = str(uuid.uuid4())
    marker = "ledger-probe-" + uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(base_url=NATIVE, timeout=90) as lg:
        r = await lg.post(
            "/threads",
            headers=h,
            json={
                "thread_id": thread_id,
                "metadata": {"tenant_id": tenant, "user_id": ctx["user"]["id"]},
            },
        )
        out["thread_create"] = r.status_code
        await lg.post(
            "/internal/threads/claim-confirmation",
            headers={**h, "x-internal-token": INTERNAL},
            json={"thread_id": thread_id},
        )

        # A. run metadata 持久化与回读
        admitted = await _admit(admin, ctx, tenant, thread_id, GRAPH)
        r = await lg.post(
            f"/threads/{thread_id}/runs",
            headers=h,
            json={
                "assistant_id": GRAPH,
                "input": {"messages": [{"role": "user", "content": "probe metadata"}]},
                "metadata": {"ledger_marker": marker, "ledger_request": "req-1"},
                "config": {
                    "configurable": {
                        "ledger_request_id": admitted["request_id"],
                        "ledger_attempt_id": admitted["attempt_id"],
                    }
                },
            },
        )
        out["create_status"] = r.status_code
        created = r.json()
        rid = str(created.get("run_id", ""))
        out["created_run_id"] = rid
        out["create_response_metadata"] = created.get("metadata")
        g = await lg.get(f"/threads/{thread_id}/runs/{rid}", headers=h)
        out["get_status"] = g.status_code
        if g.status_code == 200:
            d = g.json()
            out["get_metadata"] = d.get("metadata")
            out["get_status_field"] = d.get("status")
            out["metadata_persisted"] = (d.get("metadata") or {}).get(
                "ledger_marker"
            ) == marker

        # B. configurable 自定义键透传
        admitted2 = await _admit(admin, ctx, tenant, thread_id, GRAPH)
        r2 = await lg.post(
            f"/threads/{thread_id}/runs",
            headers=h,
            json={
                "assistant_id": GRAPH,
                "input": {
                    "messages": [{"role": "user", "content": "probe configurable"}]
                },
                "config": {
                    "configurable": {
                        "ledger_request_id": admitted2["request_id"],
                        "ledger_attempt_id": admitted2["attempt_id"],
                        "probe_extra_key": "req-xyz",
                    }
                },
            },
        )
        rid2 = str(r2.json().get("run_id", ""))
        g2 = await lg.get(f"/threads/{thread_id}/runs/{rid2}", headers=h)
        if g2.status_code == 200:
            conf = ((g2.json().get("kwargs") or {}).get("config") or {}).get(
                "configurable"
            ) or {}
            out["configurable_echo"] = {
                "ledger_request_id": conf.get("ledger_request_id"),
                "ledger_attempt": conf.get("ledger_attempt"),
            }
            out["configurable_rejected_reserved"] = "run_id" in conf

        # D. runs 列表：是否有 count/search
        rc = await lg.get(f"/threads/{thread_id}/runs/count", headers=h)
        out["runs_count_status"] = rc.status_code
        if rc.status_code == 200:
            out["runs_count_body"] = rc.json()
        rs = await lg.post(
            f"/threads/{thread_id}/runs/search",
            headers=h,
            json={"metadata": {"ledger_marker": marker}},
        )
        out["runs_search_status"] = rs.status_code
        if rs.status_code == 200:
            items = rs.json()
            out["runs_search_count"] = len(items) if isinstance(items, list) else None
            out["runs_search_ids"] = [x.get("run_id") for x in (items or [])][:5]

        # C. 恢复入口：command.resume 新建运行
        admitted3 = await _admit(admin, ctx, tenant, thread_id, GRAPH)
        rr = await lg.post(
            f"/threads/{thread_id}/runs",
            headers=h,
            json={
                "assistant_id": GRAPH,
                "command": {"resume": "continue"},
                "metadata": {"ledger_marker": marker + "-resume"},
                "config": {
                    "configurable": {
                        "ledger_request_id": admitted3["request_id"],
                        "ledger_attempt_id": admitted3["attempt_id"],
                    }
                },
            },
        )
        out["resume_status"] = rr.status_code
        if rr.status_code in (200, 201):
            rb = rr.json()
            out["resume_run_id"] = rb.get("run_id")
            out["resume_same_run_id"] = rb.get("run_id") == rid
            out["resume_thread_id_same"] = rb.get("thread_id") == thread_id

        # 清理
        for x in (rid, rid2):
            await lg.post(f"/threads/{thread_id}/runs/{x}/cancel?wait=false", headers=h)
        d = await lg.delete(f"/threads/{thread_id}", headers=h)
        out["delete"] = d.status_code

    await admin.post("/auth/logout")
    await admin.aclose()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
