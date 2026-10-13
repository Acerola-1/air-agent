"""F02 真实三端端到端：waiting → resume → 完成（真实平台 resume）.

反例来源：`docs/reviews/evidence/flash-02-repair-01-independent/resume-deletion.json`
（首修复验：恢复的 start-claim 返回 200 但业务决定 mismatch/content，HTTP 200 不能算成功）。
本文件补做**真实平台恢复**的端到端验证，核对提问 1 / 尝试 1 / 恢复事实按实际次数：

1. 真实原生运行被**用户取消**（产品里的"停止"路径）→ 平台状态 `interrupted`，线程仍可恢复；
2. 受理结果按产品路径回填（BFF 拿到的真实 run_id）→ 取消后由**服务级只读校准**
   （rs-v1，按 run_id 读取真实平台状态）把台账收敛到 `waiting_input`
   —— 同时验证 F05 的校准能力（不恢复业务权、不重执行模型、不查平台私有表）；
3. 受理 resume（保留原 request/attempt）→ 原生带 `command.resume` 创建真实恢复运行；
4. 校验：提问 1、尝试 1、恢复事实 1、`agent_run_resumes` 记录原 run 关系、
   平台出现 2 个运行（初次中断 + 恢复）但**只对应一次逻辑提问**；
5. 重放同一 resume 提交 → 复用/待确认，绝不产生第三个运行。

前提（缺失即 skip，不算通过）：`RUN_TENANT_LIVE_E2E=1` + `REVIEW_FIXTURE_PASSWORD`
+ `INTERNAL_API_TOKEN` + `ADMIN_RUNTIME_DATABASE_URI`（Agent 侧台账库，本机运行）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest

_SIBLING = Path(__file__).resolve().parent / "test_live_tenant_claims.py"
_spec = importlib.util.spec_from_file_location("live_claims_resume", _SIBLING)
_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live)

ADMIN = _live.ADMIN
NATIVE = _live.NATIVE
PROXY = _live.PROXY
_confirm_claim = _live._confirm_claim
_login = _live._login
_native_headers = _live._native_headers
_pick_tenant = _live._pick_tenant
_require_live = _live._require_live
_switch = _live._switch
_ticket = _live._ticket

pytestmark = pytest.mark.anyio

# 台账库（Agent 侧）：优先使用**显式的联调变量**，避免与隔离测试库变量同名冲突
# （全量套件会把 ADMIN_RUNTIME_DATABASE_URI/RUNTIME_DATABASE_URI 指向专用测试库，
#  而真实联调写入的是业务库，二者不是同一个库）。
LEDGER_URI = (
    os.environ.get("LEDGER_E2E_RUNTIME_URI")
    or os.environ.get("ADMIN_RUNTIME_DATABASE_URI")
    or os.environ.get("RUNTIME_DATABASE_URI", "")
)


async def _run_status(ticket: str, tid: str, run_id: str) -> str:
    async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
        res = await lg.get(
            f"/threads/{tid}/runs/{run_id}", headers=_native_headers(ticket)
        )
        assert res.status_code == 200, res.text
        return str(res.json().get("status") or "")


async def _wait_status(ticket: str, tid: str, run_id: str, wanted: set[str]) -> str:
    status = ""
    for _ in range(45):
        status = await _run_status(ticket, tid, run_id)
        if status in wanted:
            return status
        await asyncio.sleep(1)
    return status


async def _issue_service_ticket(tenant_id: str) -> str:
    """由内部端点签发 rs-v1 服务级只读校准票据（不依赖任何用户会话）."""
    internal = os.environ.get("INTERNAL_API_TOKEN", "")
    async with httpx.AsyncClient(base_url=ADMIN, timeout=20) as ad:
        res = await ad.post(
            "/agent/reconcile/ticket",
            headers={"x-internal-token": internal},
            json={"tenant_id": tenant_id, "ttl_seconds": 600},
        )
        assert res.status_code == 200, res.text
        return str(res.json()["ticket"])


def _calibrate(
    tenant_id: str, user_id: str, service_ticket: str, thread_id: str
) -> dict:
    """有界只读校准（真实平台状态读取）→ 台账收敛到 waiting_input.

    通过 CLI 子进程执行，避免与测试进程共用连接池；票据只经环境变量传递、不落盘、不打印。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["LEDGER_RECONCILE_TICKET"] = service_ticket
    env["RUNTIME_DATABASE_URI"] = LEDGER_URI
    proc = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "common.event_ledger",
            "reconcile",
            "--tenant",
            tenant_id,
            "--user",
            user_id,
            "--base-url",
            NATIVE,
            "--stalled-seconds",
            "0",
            "--batch",
            "20",
            "--max-batches",
            "2",
            "--thread",
            thread_id,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    # emit() 输出的是缩进 JSON（可多行）；日志走 stderr，因此 stdout 整体即结果。
    return json.loads(proc.stdout)


async def _ledger_counts(tenant_id: str, thread_id: str) -> dict:
    """按线程统计提问/尝试/恢复事实/回答（owner 连接，只读）."""
    uri = os.environ.get("TENANT_E2E_OWNER_URI") or os.environ.get("DATABASE_URI", "")
    async with await psycopg.AsyncConnection.connect(uri) as conn:
        row = await (
            await conn.execute(
                "SELECT (SELECT count(*) FROM agent_requests WHERE tenant_id=%s AND thread_id=%s),"
                " (SELECT count(*) FROM agent_run_attempts a JOIN agent_requests r"
                "   ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                "   WHERE a.tenant_id=%s AND r.thread_id=%s),"
                " (SELECT count(*) FROM agent_run_resumes x JOIN agent_run_attempts a"
                "   ON a.id=x.attempt_id AND a.tenant_id=x.tenant_id"
                "   JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                "   WHERE x.tenant_id=%s AND r.thread_id=%s),"
                " (SELECT count(*) FROM agent_answers an JOIN agent_run_attempts a"
                "   ON a.id=an.attempt_id AND a.tenant_id=an.tenant_id"
                "   JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                "   WHERE an.tenant_id=%s AND r.thread_id=%s)",
                (
                    tenant_id,
                    thread_id,
                    tenant_id,
                    thread_id,
                    tenant_id,
                    thread_id,
                    tenant_id,
                    thread_id,
                ),
            )
        ).fetchone()
        attempt = await (
            await conn.execute(
                "SELECT a.id::text, a.state, a.platform_run_id,"
                " (SELECT original_run_id FROM agent_run_resumes x WHERE x.attempt_id=a.id"
                "   ORDER BY resume_index DESC LIMIT 1) AS original_run_id"
                " FROM agent_run_attempts a JOIN agent_requests r"
                "   ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                " WHERE a.tenant_id=%s AND r.thread_id=%s ORDER BY a.admitted_at LIMIT 1",
                (tenant_id, thread_id),
            )
        ).fetchone()
    return {
        "requests": int(row[0]),
        "attempts": int(row[1]),
        "resumes": int(row[2]),
        "answers": int(row[3]),
        "attempt_id": str(attempt[0]) if attempt else None,
        "attempt_state": str(attempt[1]) if attempt else None,
        "platform_run_id": str(attempt[2]) if attempt and attempt[2] else None,
        "original_run_id": str(attempt[3]) if attempt and attempt[3] else None,
    }


async def _prepare_waiting_thread(
    a: httpx.AsyncClient, ca: dict, ticket: str, tid: str
) -> dict:
    """真实运行 → 取消（平台 interrupted）→ 服务级校准 → 台账 waiting_input.

    返回初始受理结果与（被取消的）初次平台 run id。
    """
    tenant_id = ca["current_tenant"]["id"]
    user_id = ca["user"]["id"]
    headers_tenant = {
        "x-tenant-id": tenant_id,
        "x-tenant-context-version": str(ca["context_version"]),
    }
    prompt = "F02 恢复往返：请写一篇 800 字的空气质量治理长文"
    admitted = await a.post(
        "/agent/requests",
        headers=headers_tenant,
        json={
            "thread_id": tid,
            "submission_key": uuid.uuid4().hex,
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": prompt,
        },
    )
    assert admitted.status_code == 200, admitted.text
    init = admitted.json()
    async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
        created = await lg.post(
            f"/threads/{tid}/runs",
            headers=_native_headers(ticket),
            json={
                "assistant_id": "basic-qa",
                "input": {"messages": [{"role": "user", "content": prompt}]},
                "metadata": {
                    "ledger_request_id": init["request_id"],
                    "ledger_attempt_id": init["attempt_id"],
                    "ledger_submission_id": init["submission_id"],
                    "ledger_graph_id": "basic-qa",
                    "ledger_operation_kind": "initial",
                },
                "multitask_strategy": "enqueue",
            },
        )
        assert created.status_code == 200, created.text
        run1 = str(created.json()["run_id"])
    confirmed = await a.post(
        "/agent/runs/admission-confirmed",
        headers={"x-internal-token": os.environ.get("INTERNAL_API_TOKEN", "")},
        json={
            "request_id": init["request_id"],
            "attempt_id": init["attempt_id"],
            "submission_id": init["submission_id"],
            "outcome": "confirmed",
            "platform_run_id": run1,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
        await lg.post(
            f"/threads/{tid}/runs/{run1}/cancel", headers=_native_headers(ticket)
        )
        status1 = await _wait_status(
            ticket, tid, run1, {"interrupted", "success", "error"}
        )
        assert status1 == "interrupted", f"取消后平台应为 interrupted：{status1}"
    calibrated = _calibrate(
        tenant_id, user_id, await _issue_service_ticket(tenant_id), tid
    )
    assert calibrated["status"] == "ok", calibrated
    before = await _ledger_counts(tenant_id, tid)
    assert before["attempt_state"] == "waiting_input", f"校准后应为等待态：{before}"
    return {
        "init": init,
        "run1": run1,
        "before": before,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }


async def test_f02_live_waiting_resume_completion_roundtrip() -> None:
    """真实 waiting → resume → 完成：提问 1 / 尝试 1 / 恢复事实 1."""
    _require_live()
    assert os.environ.get("LEDGER_E2E_RUNTIME_URI"), (
        "需要 LEDGER_E2E_RUNTIME_URI 显式指向真实联调台账库（Agent 侧）"
    )
    a, ca = await _login("acc_alice")
    ca = await _switch(a, ca, _pick_tenant(ca, "A"))
    ticket = await _ticket(a, ca)
    tenant_id = ca["current_tenant"]["id"]
    user_id = ca["user"]["id"]
    tid = str(uuid.uuid4())
    runs: list[str] = []
    headers_tenant = {
        "x-tenant-id": tenant_id,
        "x-tenant-context-version": str(ca["context_version"]),
    }
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            created = await lg.post(
                "/threads",
                headers=_native_headers(ticket),
                json={
                    "thread_id": tid,
                    "metadata": {"tenant_id": tenant_id, "user_id": user_id},
                },
            )
            assert created.status_code in (200, 201), created.text
        assert await _confirm_claim(ticket, tid)

        # 1) 初次受理 + 真实运行（长文任务；随后取消以产生可恢复的 interrupted）
        admitted = await a.post(
            "/agent/requests",
            headers=headers_tenant,
            json={
                "thread_id": tid,
                "submission_key": uuid.uuid4().hex,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "F02 恢复往返：请写一篇 800 字的空气质量治理长文",
            },
        )
        assert admitted.status_code == 200, admitted.text
        init = admitted.json()
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            first = await lg.post(
                f"/threads/{tid}/runs",
                headers=_native_headers(ticket),
                json={
                    "assistant_id": "basic-qa",
                    "input": {
                        "messages": [
                            {
                                "role": "user",
                                "content": "F02 恢复往返：请写一篇 800 字的空气质量治理长文",
                            }
                        ]
                    },
                    "metadata": {
                        "ledger_request_id": init["request_id"],
                        "ledger_attempt_id": init["attempt_id"],
                        "ledger_submission_id": init["submission_id"],
                        "ledger_graph_id": "basic-qa",
                        "ledger_operation_kind": "initial",
                    },
                    "multitask_strategy": "enqueue",
                },
            )
            assert first.status_code == 200, first.text
            run1 = str(first.json()["run_id"])
            runs.append(run1)
        # 2) 按产品路径回填受理结果（真实 run_id），再取消运行 → 平台 interrupted
        confirmed = await a.post(
            "/agent/runs/admission-confirmed",
            headers={"x-internal-token": os.environ.get("INTERNAL_API_TOKEN", "")},
            json={
                "request_id": init["request_id"],
                "attempt_id": init["attempt_id"],
                "submission_id": init["submission_id"],
                "outcome": "confirmed",
                "platform_run_id": run1,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            cancelled = await lg.post(
                f"/threads/{tid}/runs/{run1}/cancel", headers=_native_headers(ticket)
            )
            assert cancelled.status_code in (200, 202, 204, 404), cancelled.text
            status1 = await _wait_status(
                ticket, tid, run1, {"interrupted", "success", "error"}
            )
            assert status1 == "interrupted", f"取消后平台应为 interrupted：{status1}"

        # 3) 服务级只读校准（rs-v1 按 run_id 读取真实状态）→ 台账收敛到 waiting_input
        calibrated = _calibrate(
            tenant_id, user_id, await _issue_service_ticket(tenant_id), tid
        )
        assert calibrated["status"] == "ok", calibrated
        before = await _ledger_counts(tenant_id, tid)
        mine = [
            o
            for o in (calibrated.get("outcomes") or [])
            if o.get("attempt_id") == init["attempt_id"]
        ]
        assert before["attempt_state"] == "waiting_input", (
            f"校准后尝试应进入可恢复等待态：{before}，校准结论={mine}"
        )

        # 3) 受理恢复：保留原 request/attempt
        resume_key = uuid.uuid4().hex
        resumed = await a.post(
            "/agent/requests",
            headers=headers_tenant,
            json={
                "thread_id": tid,
                "submission_key": resume_key,
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": before["attempt_id"],
            },
        )
        assert resumed.status_code == 200, resumed.text
        res = resumed.json()
        assert res["request_id"] == init["request_id"], "恢复必须复用原 request"
        assert res["attempt_id"] == init["attempt_id"], "恢复必须复用原 attempt"
        assert res["operation_kind"] == "resume"

        # 4) 真实平台恢复运行（command.resume），执行到完成
        async with httpx.AsyncClient(base_url=NATIVE, timeout=60) as lg:
            second = await lg.post(
                f"/threads/{tid}/runs",
                headers=_native_headers(ticket),
                json={
                    "assistant_id": "basic-qa",
                    "command": {"resume": "continue"},
                    "metadata": {
                        "ledger_request_id": res["request_id"],
                        "ledger_attempt_id": res["attempt_id"],
                        "ledger_submission_id": res["submission_id"],
                        "ledger_graph_id": "basic-qa",
                        "ledger_operation_kind": "resume",
                    },
                    "config": {"configurable": {"chat_mode": "fast"}},
                },
            )
            assert second.status_code == 200, second.text
            run2 = str(second.json()["run_id"])
            runs.append(run2)
            assert run2 != run1, "恢复必须是新的平台运行"
            status2 = await _wait_status(
                ticket, tid, run2, {"success", "error", "interrupted", "timeout"}
            )
            assert status2 == "success", f"恢复运行未完成：{status2}"

        # 5) 计数与关系核对：提问 1 / 尝试 1 / 恢复事实 1 / 回答 1；平台 2 个运行对应 1 次提问
        after = await _ledger_counts(tenant_id, tid)
        assert after["requests"] == 1, f"人工恢复不得新建逻辑提问：{after}"
        assert after["attempts"] == 1, f"人工恢复不得新建尝试：{after}"
        assert after["resumes"] == 1, f"恢复事实应按实际恢复次数记录：{after}"
        assert after["answers"] == 1, f"恢复完成后应记录 1 条可见回答：{after}"
        assert after["attempt_state"] == "succeeded", after
        assert after["platform_run_id"] == run2, "尝试应指向本次恢复运行"
        assert after["original_run_id"] == run1, "恢复映射必须保留原运行关系"

        # 6) 重放同一恢复提交：绝不产生第三个运行
        replay = await a.post(
            "/agent/requests",
            headers=headers_tenant,
            json={
                "thread_id": tid,
                "submission_key": resume_key,
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": before["attempt_id"],
            },
        )
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            listed = await lg.get(
                f"/threads/{tid}/runs?limit=50", headers=_native_headers(ticket)
            )
            assert listed.status_code == 200
            platform_runs = len(listed.json())
        assert replay.status_code == 200, replay.text
        assert replay.json()["submission_id"] == res["submission_id"], (
            "同键重放必须复用同一提交"
        )
        assert platform_runs == 2, f"重放不得产生新运行：{platform_runs}"
        final = await _ledger_counts(tenant_id, tid)
        assert final["requests"] == 1 and final["attempts"] == 1, final
    finally:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            for run in runs:
                await lg.post(
                    f"/threads/{tid}/runs/{run}/cancel", headers=_native_headers(ticket)
                )
            await lg.delete(f"/threads/{tid}", headers=_native_headers(ticket))
        await a.post("/auth/logout")
        await a.aclose()


async def test_f02_resume_concurrent_and_lost_response() -> None:
    """F02：恢复的**并发**与**回包丢失**——只对应一个业务运行，且能自愈映射.

    1. 并发：两个同提交的恢复创建同时发出 → 启动权单点裁决只放行一个；
    2. 回包丢失：**不调用任何确认端点**（调用方没收到响应）→ 由图内可信 run_id 回调
       把映射补齐（F05 的修复），不需要外部补偿即可收敛；
    3. 全程提问 1 / 尝试 1 / 恢复事实 1，平台运行数 = 2（初次被取消 + 一次恢复），
       重放同键不产生第三个运行。
    """
    _require_live()
    assert os.environ.get("LEDGER_E2E_RUNTIME_URI"), (
        "需要 LEDGER_E2E_RUNTIME_URI 显式指向真实联调台账库（Agent 侧）"
    )
    a, ca = await _login("acc_alice")
    ca = await _switch(a, ca, _pick_tenant(ca, "A"))
    ticket = await _ticket(a, ca)
    tenant_id = ca["current_tenant"]["id"]
    tid = str(uuid.uuid4())
    headers_tenant = {
        "x-tenant-id": tenant_id,
        "x-tenant-context-version": str(ca["context_version"]),
    }
    runs: list[str] = []
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            created = await lg.post(
                "/threads",
                headers=_native_headers(ticket),
                json={
                    "thread_id": tid,
                    "metadata": {
                        "tenant_id": tenant_id,
                        "user_id": ca["user"]["id"],
                        "graph_id": "basic-qa",
                    },
                },
            )
            assert created.status_code in (200, 201), created.text
        assert await _confirm_claim(ticket, tid)
        prep = await _prepare_waiting_thread(a, ca, ticket, tid)
        runs.append(prep["run1"])
        init = prep["init"]

        resume_key = uuid.uuid4().hex
        resumed = await a.post(
            "/agent/requests",
            headers=headers_tenant,
            json={
                "thread_id": tid,
                "submission_key": resume_key,
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": prep["before"]["attempt_id"],
            },
        )
        assert resumed.status_code == 200, resumed.text
        res = resumed.json()
        resume_body = {
            "assistant_id": "basic-qa",
            "command": {"resume": "continue"},
            "metadata": {
                "ledger_request_id": res["request_id"],
                "ledger_attempt_id": res["attempt_id"],
                "ledger_submission_id": res["submission_id"],
                "ledger_graph_id": "basic-qa",
                "ledger_operation_kind": "resume",
            },
            "config": {"configurable": {"chat_mode": "fast"}},
        }

        # 1) 并发：两个同提交的恢复创建同时发出
        async with httpx.AsyncClient(base_url=NATIVE, timeout=60) as lg:
            first, second = await asyncio.gather(
                lg.post(
                    f"/threads/{tid}/runs",
                    headers=_native_headers(ticket),
                    json=resume_body,
                ),
                lg.post(
                    f"/threads/{tid}/runs",
                    headers=_native_headers(ticket),
                    json=resume_body,
                ),
            )
            codes = sorted([first.status_code, second.status_code])
            assert codes[0] == 200, f"并发恢复必须恰好一个成功：{codes}"
            assert codes[1] in (409, 202), f"另一个必须被拒绝或待确认：{codes}"
            winner = first if first.status_code == 200 else second
            loser = second if winner is first else first
            run2 = str(winner.json()["run_id"])
            runs.append(run2)
            assert "启动权" in loser.text or "已有关联运行" in loser.text, loser.text

        # 2) 回包丢失：不调用确认端点，等图内回调把映射补齐
        async with httpx.AsyncClient(base_url=NATIVE, timeout=60) as lg:
            status2 = await _wait_status(
                ticket, tid, run2, {"success", "error", "interrupted", "timeout"}
            )
            assert status2 == "success", f"恢复运行未完成：{status2}"
        after = await _ledger_counts(tenant_id, tid)
        assert after["requests"] == 1, after
        assert after["attempts"] == 1, after
        assert after["resumes"] == 1, f"恢复事实应为 1：{after}"
        assert after["answers"] == 1, after
        assert after["platform_run_id"] == run2, (
            f"回包丢失后应由图内回调补齐映射：{after}"
        )
        assert after["original_run_id"] == prep["run1"], after

        # 3) 平台运行数：初次被取消 + 一次恢复 = 2
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            listed = await lg.get(
                f"/threads/{tid}/runs?limit=50", headers=_native_headers(ticket)
            )
            assert listed.status_code == 200
            assert len(listed.json()) == 2, (
                f"并发+丢失不得产生额外运行：{listed.json()}"
            )
        replay = await a.post(
            "/agent/requests",
            headers=headers_tenant,
            json={
                "thread_id": tid,
                "submission_key": resume_key,
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": prep["before"]["attempt_id"],
            },
        )
        assert (
            replay.status_code == 200
            and replay.json()["submission_id"] == res["submission_id"]
        )
        _ = init
    finally:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            for run in runs:
                await lg.post(
                    f"/threads/{tid}/runs/{run}/cancel", headers=_native_headers(ticket)
                )
            await lg.delete(f"/threads/{tid}", headers=_native_headers(ticket))
        await a.post("/auth/logout")
        await a.aclose()
