"""任务 1.2 探针：验证当前官方镜像的运行受理/ID/metadata/恢复/幂等能力。

对**真实** air-agent/langgraph-api:latest（langgraph_api 0.15.1）执行只读+合成
线程操作，回答以下问题（不能靠模拟 SDK 推断）：

1. `POST /threads/{tid}/runs` 是否接受调用方指定 run_id？重复同 ID 是幂等复用
   还是冲突？
2. `POST /threads/{tid}/runs/stream` 是否同样支持指定 run_id（流式入口）？
3. `GET /threads/{tid}/runs/{rid}` 返回哪些字段（状态/metadata/时间）？
4. 人工恢复：`POST /threads/{tid}/runs/{rid}` 是否支持 command resume？未完成
   运行的恢复语义如何？
5. 未知 run 的查询/取消是否 404（区分"对象缺失"与"平台不可用"）。

只创建/删除本脚本生成的合成空白线程，不读取历史用户对话，不重置业务卷。
需要：admin :8100、native :2024 运行当前代码；acc_alice 可登录。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

ADMIN = os.environ.get("LIVE_ADMIN_URL", "http://127.0.0.1:8100")
NATIVE = os.environ.get("LIVE_NATIVE_URL", "http://127.0.0.1:2024")
INTERNAL = os.environ.get(
    "INTERNAL_API_TOKEN",
    "local-internal-token",
)
PASSWORD = os.environ.get("REVIEW_FIXTURE_PASSWORD", "local-e2e-pass")
GRAPH = "basic-qa"


async def _login(account: str) -> tuple[httpx.AsyncClient, dict]:
    client = httpx.AsyncClient(base_url=ADMIN, timeout=30)
    res = await client.post(
        "/auth/login", json={"account": account, "password": PASSWORD}
    )
    res.raise_for_status()
    return client, res.json()


def _pick_tenant(ctx: dict, suffix: str) -> str:
    for item in ctx["tenants"]:
        if item["tenant"]["name"].endswith(suffix):
            return item["tenant"]["id"]
    return ctx["tenants"][0]["tenant"]["id"]


async def _switch(client: httpx.AsyncClient, ctx: dict, tenant_id: str) -> dict:
    if (ctx.get("current_tenant") or {}).get("id") == tenant_id:
        return ctx
    res = await client.post(
        "/auth/switch-tenant",
        json={"tenant_id": tenant_id, "expected_ctx_version": ctx["context_version"]},
    )
    res.raise_for_status()
    return res.json()


async def _ticket(client: httpx.AsyncClient, ctx: dict) -> str:
    res = await client.post(
        "/auth/runtime-ticket",
        headers={"x-internal-token": INTERNAL},
        json={"target_tenant_id": ctx["current_tenant"]["id"]},
    )
    res.raise_for_status()
    return res.json()["ticket"]


def _h(ticket: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ticket}", "content-type": "application/json"}


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
    tenant = _pick_tenant(ctx, "A")
    ctx = await _switch(admin, ctx, tenant)
    ticket = await _ticket(admin, ctx)
    headers = _h(ticket)
    thread_id = str(uuid.uuid4())
    runs_created: list[str] = []
    async with httpx.AsyncClient(base_url=NATIVE, timeout=60) as lg:
        # 建线程 + 确认认领（复用 0006 认领协议）
        r = await lg.post(
            "/threads",
            headers=headers,
            json={
                "thread_id": thread_id,
                "metadata": {"tenant_id": tenant, "user_id": ctx["user"]["id"]},
            },
        )
        out["thread_create"] = r.status_code
        cf = await lg.post(
            "/internal/threads/claim-confirmation",
            headers={**headers, "x-internal-token": INTERNAL},
            json={"thread_id": thread_id},
        )
        out["claim_confirm"] = cf.status_code

        # ---- 1. 调用方指定 run_id ----
        prealloc = str(uuid.uuid4())
        admitted = await _admit(admin, ctx, tenant, thread_id, GRAPH)
        body_prealloc = {
            "assistant_id": GRAPH,
            "input": {
                "messages": [{"role": "user", "content": "probe prealloc run_id"}]
            },
            "run_id": prealloc,
            "config": {
                "configurable": {
                    "ledger_request_id": admitted["request_id"],
                    "ledger_attempt_id": admitted["attempt_id"],
                }
            },
        }
        r = await lg.post(
            f"/threads/{thread_id}/runs", headers=headers, json=body_prealloc
        )
        out["create_with_run_id_status"] = r.status_code
        out["create_with_run_id_body"] = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.text[:300]
        )
        if r.status_code in (200, 201):
            runs_created.append(str(r.json().get("run_id") or prealloc))
        out["prealloc_matches"] = (
            out["create_with_run_id_body"].get("run_id") == prealloc
            if isinstance(out["create_with_run_id_body"], dict)
            else None
        )

        # ---- 2. 重复同 run_id（幂等 or 冲突）----
        r2 = await lg.post(
            f"/threads/{thread_id}/runs",
            headers=headers,
            json={
                **body_prealloc,
                "config": {
                    "configurable": {
                        "ledger_request_id": admitted["request_id"],
                        "ledger_attempt_id": admitted["attempt_id"],
                    }
                },
            },
        )
        out["duplicate_same_run_id_status"] = r2.status_code
        out["duplicate_same_run_id_body"] = (
            r2.json()
            if r2.headers.get("content-type", "").startswith("application/json")
            else r2.text[:300]
        )

        # ---- 3. 运行查询字段 ----
        observed_run = str(
            (out["create_with_run_id_body"] or {}).get("run_id")
            if isinstance(out["create_with_run_id_body"], dict)
            else prealloc
        )
        g = await lg.get(f"/threads/{thread_id}/runs/{observed_run}", headers=headers)
        out["run_get_status"] = g.status_code
        if g.status_code == 200:
            data = g.json()
            out["run_get_keys"] = sorted(data.keys())
            out["run_get_sample"] = {
                k: data.get(k)
                for k in (
                    "run_id",
                    "thread_id",
                    "assistant_id",
                    "status",
                    "metadata",
                    "created_at",
                    "updated_at",
                    "kwargs",
                )
                if k in data
            }

        # ---- 5. 未知 run 查询/取消 ----
        unknown = str(uuid.uuid4())
        gu = await lg.get(f"/threads/{thread_id}/runs/{unknown}", headers=headers)
        out["unknown_run_get"] = gu.status_code
        cu = await lg.post(
            f"/threads/{thread_id}/runs/{unknown}/cancel?wait=false", headers=headers
        )
        out["unknown_run_cancel"] = cu.status_code

        # ---- 4. 人工恢复（interrupt resume）与流式 run_id ----
        # 4a. 先建一个流式运行并尝试指定 run_id
        prealloc_stream = str(uuid.uuid4())
        admitted_stream = await _admit(admin, ctx, tenant, thread_id, GRAPH)
        try:
            async with lg.stream(
                "POST",
                f"/threads/{thread_id}/runs/stream",
                headers=headers,
                json={
                    "assistant_id": GRAPH,
                    "input": {
                        "messages": [{"role": "user", "content": "probe stream run_id"}]
                    },
                    "run_id": prealloc_stream,
                    "stream_mode": ["values"],
                    "config": {
                        "configurable": {
                            "ledger_request_id": admitted_stream["request_id"],
                            "ledger_attempt_id": admitted_stream["attempt_id"],
                        }
                    },
                },
            ) as s:
                out["stream_status"] = s.status_code
                first = ""
                async for chunk in s.aiter_text():
                    first += chunk
                    if len(first) > 600:
                        break
                out["stream_head"] = first[:600]
        except Exception as exc:  # noqa: BLE001
            out["stream_error"] = f"{type(exc).__name__}: {exc}"

        # 4b. cancel + 状态（真实取消语义）
        if observed_run:
            c = await lg.post(
                f"/threads/{thread_id}/runs/{observed_run}/cancel?wait=false",
                headers=headers,
            )
            out["cancel_status"] = c.status_code
            again = await lg.post(
                f"/threads/{thread_id}/runs/{observed_run}/cancel?wait=false",
                headers=headers,
            )
            out["cancel_again_status"] = again.status_code

        # 4c. 恢复：对已完成运行 POST /runs/{rid}（命令式恢复入口探测）
        rs = await lg.post(
            f"/threads/{thread_id}/runs/{observed_run}",
            headers=headers,
            json={"command": {"resume": "continue"}},
        )
        out["resume_on_run_status"] = rs.status_code
        out["resume_on_run_body"] = (
            rs.json()
            if rs.headers.get("content-type", "").startswith("application/json")
            else rs.text[:300]
        )

        # ---- runs 列表（含状态）----
        rl = await lg.get(f"/threads/{thread_id}/runs?limit=10", headers=headers)
        out["runs_list_status"] = rl.status_code
        if rl.status_code == 200:
            items = rl.json()
            out["runs_list_count"] = len(items) if isinstance(items, list) else None
            out["runs_list_sample"] = [
                {"run_id": x.get("run_id"), "status": x.get("status")}
                for x in (items if isinstance(items, list) else [])[:5]
            ]

        # 清理（只删本脚本合成线程）
        for rid in runs_created:
            await lg.post(
                f"/threads/{thread_id}/runs/{rid}/cancel?wait=false", headers=headers
            )
        d = await lg.delete(f"/threads/{thread_id}", headers=headers)
        out["thread_delete"] = d.status_code

    await admin.post("/auth/logout")
    await admin.aclose()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
