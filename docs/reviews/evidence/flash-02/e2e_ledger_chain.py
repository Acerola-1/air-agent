"""任务 3.2/3.3 live 证据：从可信受理到真实 run 与台账回读的最小完整链路。

链路：会话票据身份 → 台账受理（admin）→ 受控创建真实原生 run（注入台账关联
metadata）→ 回读平台 run 校验关联 → 确认受理 → 台账回读（状态/关联一致）。

同时验证受理歧义路径：不确认时保持待确认，且不自动重发。

前提：admin :8100（含台账路由）、native :2024、业务库已迁移到 0007。
只创建/删除本脚本生成的合成线程。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

ADMIN = os.environ.get("LIVE_ADMIN_URL", "http://127.0.0.1:8100")
NATIVE = os.environ.get("LIVE_NATIVE_URL", "http://127.0.0.1:2024")
PASSWORD = os.environ.get("REVIEW_FIXTURE_PASSWORD", "local-e2e-pass")
GRAPH = "basic-qa"
INTERNAL = os.environ.get("INTERNAL_API_TOKEN", "")


async def main() -> None:
    out: dict[str, object] = {}
    async with httpx.AsyncClient(base_url=ADMIN, timeout=30) as admin:
        login = await admin.post(
            "/auth/login", json={"account": "acc_alice", "password": PASSWORD}
        )
        login.raise_for_status()
        ctx = login.json()
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
        ticket = (
            await admin.post(
                "/auth/runtime-ticket",
                headers={"x-internal-token": INTERNAL},
                json={"target_tenant_id": tenant},
            )
        ).json()["ticket"]
        h = {"Authorization": f"Bearer {ticket}", "content-type": "application/json"}
        thread_id = str(uuid.uuid4())
        async with httpx.AsyncClient(base_url=NATIVE, timeout=60) as lg:
            created = await lg.post(
                "/threads",
                headers=h,
                json={
                    "thread_id": thread_id,
                    "metadata": {"tenant_id": tenant, "user_id": ctx["user"]["id"]},
                },
            )
            out["thread_create"] = created.status_code
            await lg.post(
                "/internal/threads/claim-confirmation",
                headers={**h, "x-internal-token": INTERNAL},
                json={"thread_id": thread_id},
            )

            # 1) 可信受理（admin 台账）
            submission_key = uuid.uuid4().hex
            admit = await admin.post(
                "/agent/requests",
                headers={
                    "x-tenant-id": tenant,
                    "x-tenant-context-version": str(ctx["context_version"]),
                },
                json={
                    "thread_id": thread_id,
                    "submission_key": submission_key,
                    "graph_id": GRAPH,
                    "operation_kind": "initial",
                    "content": "台账链路验证问题",
                    "user_message_id": "um-e2e-1",
                },
            )
            out["admit_status"] = admit.status_code
            if admit.status_code != 200:
                out["admit_body"] = admit.text[:300]
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return
            admitted = admit.json()
            out["admit"] = {
                "request_id": admitted["request_id"],
                "attempt_id": admitted["attempt_id"],
                "reused": admitted["reused"],
                "admission_status": admitted["admission_status"],
            }

            # 幂等重放：同键同内容必须复用同一 request/attempt
            replay = await admin.post(
                "/agent/requests",
                headers={
                    "x-tenant-id": tenant,
                    "x-tenant-context-version": str(ctx["context_version"]),
                },
                json={
                    "thread_id": thread_id,
                    "submission_key": submission_key,
                    "graph_id": GRAPH,
                    "operation_kind": "initial",
                    "content": "台账链路验证问题",
                    "user_message_id": "um-e2e-1",
                },
            )
            out["replay_reused"] = replay.json().get("reused")
            out["replay_same_attempt"] = (
                replay.json().get("attempt_id") == admitted["attempt_id"]
            )

            # 2) 受控创建真实 run（注入台账关联 metadata）
            run = await lg.post(
                f"/threads/{thread_id}/runs",
                headers=h,
                json={
                    "assistant_id": GRAPH,
                    "input": {"messages": [{"role": "user", "content": "台账链路验证问题"}]},
                    "metadata": {
                        "ledger_request_id": admitted["request_id"],
                        "ledger_attempt_id": admitted["attempt_id"],
                        "ledger_marker": admitted["ledger_marker"],
                    },
                    "config": {
                        "configurable": {
                            "ledger_request_id": admitted["request_id"],
                            "ledger_attempt_id": admitted["attempt_id"],
                        }
                    },
                },
            )
            out["run_create_status"] = run.status_code
            run_id = str(run.json().get("run_id", "")) if run.status_code < 300 else ""
            out["platform_run_id"] = run_id

            # 3) 回读平台 run，校验关联 metadata 已持久化
            detail = await lg.get(f"/threads/{thread_id}/runs/{run_id}", headers=h)
            meta = (detail.json().get("metadata") or {}) if detail.status_code == 200 else {}
            out["platform_metadata_persisted"] = (
                meta.get("ledger_request_id") == admitted["request_id"]
                and meta.get("ledger_attempt_id") == admitted["attempt_id"]
            )
            out["platform_metadata"] = {
                k: meta.get(k) for k in ("ledger_request_id", "ledger_attempt_id")
            }
            out["platform_status"] = detail.json().get("status") if detail.status_code == 200 else None

            # 4) 确认受理（内部端点）
            confirm = await admin.post(
                "/agent/runs/admission-confirmed",
                headers={
                    "x-internal-token": INTERNAL,
                    "x-tenant-id": tenant,
                    "x-tenant-context-version": str(ctx["context_version"]),
                },
                json={
                    "request_id": admitted["request_id"],
                    "attempt_id": admitted["attempt_id"],
                    "outcome": "confirmed",
                    "platform_run_id": run_id,
                },
            )
            out["confirm_status"] = confirm.status_code
            out["confirm_state"] = confirm.json().get("state") if confirm.status_code == 200 else None

            # 5) 台账回读：关联一致
            readback = await admin.get(f"/agent/requests/{admitted['request_id']}")
            body = readback.json()
            out["readback_status"] = readback.status_code
            out["readback_attempt"] = body["attempts"][0]["platform_run_id"]
            out["readback_state"] = body["attempts"][0]["state"]
            out["readback_content_included"] = body["content_included"]
            out["run_matches_ledger"] = body["attempts"][0]["platform_run_id"] == run_id

            # 6) 受理歧义：新提交不确认 → 保持待确认且不重发
            amb_key = uuid.uuid4().hex
            amb = (
                await admin.post(
                    "/agent/requests",
                    headers={
                        "x-tenant-id": tenant,
                        "x-tenant-context-version": str(ctx["context_version"]),
                    },
                    json={
                        "thread_id": thread_id,
                        "submission_key": amb_key,
                        "graph_id": GRAPH,
                        "operation_kind": "retry",
                        "content": "歧义路径",
                    },
                )
            ).json()
            unknown = await admin.post(
                "/agent/runs/admission-confirmed",
                headers={
                    "x-internal-token": INTERNAL,
                    "x-tenant-id": tenant,
                    "x-tenant-context-version": str(ctx["context_version"]),
                },
                json={
                    "request_id": amb["request_id"],
                    "attempt_id": amb["attempt_id"],
                    "outcome": "admission_unknown",
                    "reason": "upstream_response_lost",
                },
            )
            out["unknown_state"] = unknown.json().get("state")
            amb_replay = (
                await admin.post(
                    "/agent/requests",
                    headers={
                        "x-tenant-id": tenant,
                        "x-tenant-context-version": str(ctx["context_version"]),
                    },
                    json={
                        "thread_id": thread_id,
                        "submission_key": amb_key,
                        "graph_id": GRAPH,
                        "operation_kind": "retry",
                        "content": "歧义路径",
                    },
                )
            ).json()
            out["unknown_not_retried"] = (
                amb_replay["attempt_id"] == amb["attempt_id"] and amb_replay["reused"] is True
            )

            # 清理
            for rid in (run_id,):
                if rid:
                    await lg.post(f"/threads/{thread_id}/runs/{rid}/cancel?wait=false", headers=h)
            out["thread_delete"] = (await lg.delete(f"/threads/{thread_id}", headers=h)).status_code
        await admin.post("/auth/logout")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
