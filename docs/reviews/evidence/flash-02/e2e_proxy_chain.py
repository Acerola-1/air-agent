"""任务 3.6/8.2 live 证据：经**聊天代理**（Next BFF）的受理链路。

与 `e2e_ledger_chain.py`（直连 admin）互补：本脚本走浏览器同源代理
`POST /api/threads/{tid}/runs`，验证：
- 代理在创建运行前调用 admin 台账受理（未受理不启动运行）；
- 代理把台账关联注入平台 run metadata/configurable；
- 代理在拿到 run_id 后回读平台 run 校验标记并确认；
- 幂等：同 `x-submission-key` 重放不新建提问/尝试。

只创建/删除本脚本生成的合成线程与运行。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

ADMIN = os.environ.get("LIVE_ADMIN_URL", "http://127.0.0.1:8100")
PROXY = os.environ.get("LIVE_CHAT_PROXY_URL", "http://127.0.0.1:3000")
NATIVE = os.environ.get("LIVE_NATIVE_URL", "http://127.0.0.1:2024")
PASSWORD = os.environ.get("REVIEW_FIXTURE_PASSWORD", "local-e2e-pass")
INTERNAL = os.environ.get("INTERNAL_API_TOKEN", "")
GRAPH = "basic-qa"


async def main() -> None:
    out: dict[str, object] = {}
    async with httpx.AsyncClient(base_url=ADMIN, timeout=30) as admin:
        ctx = (
            await admin.post("/auth/login", json={"account": "acc_alice", "password": PASSWORD})
        ).json()
        tenant = next(
            (t["tenant"]["id"] for t in ctx["tenants"] if t["tenant"]["name"].endswith("A")),
            ctx["tenants"][0]["tenant"]["id"],
        )
        if (ctx.get("current_tenant") or {}).get("id") != tenant:
            ctx = (
                await admin.post(
                    "/auth/switch-tenant",
                    json={"tenant_id": tenant, "expected_ctx_version": ctx["context_version"]},
                )
            ).json()
        cookies = admin.cookies
        headers = {
            "x-tenant-id": tenant,
            "x-tenant-context-version": str(ctx["context_version"]),
            "content-type": "application/json",
        }
        thread_id = ""
        async with httpx.AsyncClient(base_url=PROXY, cookies=cookies, timeout=90) as proxy:
            # 1) 经代理建线程（BFF 铸造 ID + 确认认领）
            created = await proxy.post(
                "/api/threads", headers=headers, json={"metadata": {"graph_id": GRAPH}}
            )
            out["proxy_thread_create"] = created.status_code
            thread_id = str(created.json().get("thread_id") or "")
            out["minted_thread_id"] = bool(thread_id)

            # 2) 经代理创建运行（代理内部先受理、再启动、再确认）
            key = uuid.uuid4().hex
            run = await proxy.post(
                f"/api/threads/{thread_id}/runs",
                headers={**headers, "x-submission-key": key, "x-operation-kind": "initial"},
                json={
                    "assistant_id": GRAPH,
                    "input": {"messages": [{"role": "user", "content": "代理台账链路验证"}]},
                    "stream_mode": ["values"],
                },
            )
            out["proxy_run_status"] = run.status_code
            body = run.json() if "json" in (run.headers.get("content-type") or "") else {}
            run_id = str(body.get("run_id") or "")
            out["proxy_run_id"] = run_id
            out["ledger_marker_header"] = bool(run.headers.get("x-ledger-marker"))

            # 3) 台账回读：应已有确认的平台 run 关联
            ticket = (
                await admin.post(
                    "/auth/runtime-ticket",
                    headers={"x-internal-token": INTERNAL},
                    json={"target_tenant_id": tenant},
                )
            ).json()["ticket"]
            runs = await admin.get("/agent/runs?limit=20")
            items = runs.json().get("items", []) if runs.status_code == 200 else []
            mine = [i for i in items if i["thread_id"] == thread_id]
            out["ledger_attempt_for_thread"] = len(mine)
            out["ledger_linked_run"] = bool(
                run_id and any(i["platform_run_id"] == run_id for i in mine)
            )
            out["ledger_state"] = mine[0]["state"] if mine else None

            # 4) 幂等：同 submission_key 重放 → 复用
            replay = await proxy.post(
                f"/api/threads/{thread_id}/runs",
                headers={**headers, "x-submission-key": key, "x-operation-kind": "initial"},
                json={
                    "assistant_id": GRAPH,
                    "input": {"messages": [{"role": "user", "content": "代理台账链路验证"}]},
                    "stream_mode": ["values"],
                },
            )
            out["proxy_replay_status"] = replay.status_code
            runs2 = await admin.get("/agent/runs?limit=20")
            items2 = runs2.json().get("items", []) if runs2.status_code == 200 else []
            mine2 = [i for i in items2 if i["thread_id"] == thread_id]
            # 同键同内容重放不应新增 attempt（仍 1 条）
            out["attempts_after_replay"] = len(mine2)
            out["replay_no_new_attempt"] = len(mine2) == len(mine)

            # 5) 停止意图经代理记录
            stop = await proxy.post(
                "/api/agent-runs/stop-intent",
                headers=headers,
                json={
                    "thread_id": thread_id,
                    "platform_run_id": run_id,
                    "origin": "user_stop",
                    "phase": "processing",
                },
            )
            out["proxy_stop_intent_status"] = stop.status_code
            out["proxy_stop_recorded"] = stop.json().get("recorded")

            # 清理
            async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
                if run_id:
                    await lg.post(
                        f"/threads/{thread_id}/runs/{run_id}/cancel?wait=false",
                        headers={"Authorization": f"Bearer {ticket}"},
                    )
            out["proxy_thread_delete"] = (
                await proxy.delete(f"/api/threads/{thread_id}", headers=headers)
            ).status_code
        await admin.post("/auth/logout")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
