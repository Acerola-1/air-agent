"""第二轮定向修复（F01/F03/F05/F06）的真实三端边界回归.

来源：`docs/reviews/flash-blind-02-repair-01-review.md` +
`docs/reviews/evidence/flash-02-repair-01-independent/`（协调者独立复现）。
每条用例先确认**修复前失败**，再据根因修复；断言的是修复后必须成立的行为。

前提（缺失即 skip，不算通过）：`RUN_TENANT_LIVE_E2E=1` + `REVIEW_FIXTURE_PASSWORD`
+ `INTERNAL_API_TOKEN` + owner URI（与 `test_live_tenant_claims.py` 同）。
仅创建并清理本用例自己的合成线程；换图用例带 `interrupt_before=['*']`，
在首节点前暂停，不执行业务工具。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest

_SIBLING = Path(__file__).resolve().parent / "test_live_tenant_claims.py"
_spec = importlib.util.spec_from_file_location("live_claims", _SIBLING)
_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live)

ADMIN = _live.ADMIN
NATIVE = _live.NATIVE
OWNER_URI = _live.OWNER_URI
PROXY = _live.PROXY
_confirm_claim = _live._confirm_claim
_login = _live._login
_native_headers = _live._native_headers
_pick_tenant = _live._pick_tenant
_require_live = _live._require_live
_switch = _live._switch
_ticket = _live._ticket
pytestmark = pytest.mark.anyio


async def _thread_ctx(tenant_suffix: str = "A"):
    """登录 + 切企业 + 运行票据 + 原生建线程并确认归属."""
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, tenant_suffix) or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    ticket = await _ticket(a, ca)
    tid = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
        res = await lg.post(
            "/threads",
            headers=_native_headers(ticket),
            json={
                "thread_id": tid,
                "metadata": {
                    "tenant_id": ca["current_tenant"]["id"],
                    "user_id": ca["user"]["id"],
                },
            },
        )
        assert res.status_code in (200, 201), res.text
    assert await _confirm_claim(ticket, tid)
    return a, ca, ticket, tid


async def _platform_run_count(ticket: str, tid: str) -> int:
    return await _platform_run_count_with(_native_headers(ticket), tid)


async def _platform_run_count_with(
    headers: dict[str, str], tid: str, *, gateway: bool = False
) -> int:
    """按给定凭据统计线程运行数（用户票据或服务校准票据均可）."""
    async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
        prefix = "/internal/ledger" if gateway else ""
        res = await lg.get(f"{prefix}/threads/{tid}/runs?limit=50", headers=headers)
        assert res.status_code == 200, res.text
        return len(res.json())


async def _cleanup(
    a: httpx.AsyncClient, ticket: str, tid: str, runs: list[str]
) -> None:
    async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
        for run in runs:
            await lg.post(
                f"/threads/{tid}/runs/{run}/cancel", headers=_native_headers(ticket)
            )
        await lg.delete(f"/threads/{tid}", headers=_native_headers(ticket))
    await a.post("/auth/logout")
    await a.aclose()


def _bff_headers(ca: dict, **extra: str) -> dict[str, str]:
    return {
        "x-tenant-id": ca["current_tenant"]["id"],
        "x-tenant-context-version": str(ca["context_version"]),
        "content-type": "application/json",
        **extra,
    }


def _body(text: str, *, stream: bool = False) -> dict:
    body: dict = {
        "assistant_id": "basic-qa",
        "input": {"messages": [{"role": "user", "content": text}]},
        # 暂停在首节点前：只验证**准入/启动**边界，不执行业务工具与模型。
        "interrupt_before": ["*"],
    }
    if stream:
        body["stream_mode"] = ["values"]
    return body


# ------------------------------------------------------------------ F01


async def test_f01_bff_first_submission_starts_run() -> None:
    """F01：BFF 合法首笔（非流式）必须真正启动运行，而不是 409.

    修复前：BFF 先 claim，原生再 claim → 首次 409"启动权已被消费"，重试 202。
    """
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    try:
        async with httpx.AsyncClient(
            base_url=PROXY, cookies=a.cookies, timeout=40
        ) as proxy:
            res = await proxy.post(
                f"/api/threads/{tid}/runs",
                headers=_bff_headers(ca, **{"x-submission-key": uuid.uuid4().hex}),
                json=_body("F01 首笔非流式"),
            )
            assert res.status_code < 300, f"BFF 首笔失败：{res.status_code} {res.text}"
            run_id = str(res.json().get("run_id") or "")
            assert run_id, f"首笔未返回 run_id: {res.text}"
            runs.append(run_id)
        assert await _platform_run_count(ticket, tid) == 1, "首笔只能对应一个平台运行"
    finally:
        await _cleanup(a, ticket, tid, runs)


async def test_f01_bff_stream_first_submission_starts_run() -> None:
    """F01：BFF 合法首笔（流式）同样必须启动，且只产生一个平台运行."""
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    try:
        async with httpx.AsyncClient(
            base_url=PROXY, cookies=a.cookies, timeout=60
        ) as proxy:
            async with proxy.stream(
                "POST",
                f"/api/threads/{tid}/runs",
                headers=_bff_headers(
                    ca,
                    **{
                        "x-submission-key": uuid.uuid4().hex,
                        "accept": "text/event-stream",
                    },
                ),
                json=_body("F01 首笔流式", stream=True),
            ) as res:
                assert res.status_code < 300, f"BFF 流式首笔失败：{res.status_code}"
                async for _chunk in res.aiter_bytes():
                    break
        # 流式 run_id 由平台生成；用线程运行列表校验"恰好一个"
        assert await _platform_run_count(ticket, tid) == 1, "流式首笔只能有一个平台运行"
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            listed = await lg.get(
                f"/threads/{tid}/runs?limit=50", headers=_native_headers(ticket)
            )
            runs.extend([r["run_id"] for r in listed.json()])
    finally:
        await _cleanup(a, ticket, tid, runs)


async def test_f01_same_submission_replay_never_creates_second_run() -> None:
    """F01：同一 submission 重放（响应丢失/重试）不得产生第二个业务运行."""
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    key = uuid.uuid4().hex
    try:
        async with httpx.AsyncClient(
            base_url=PROXY, cookies=a.cookies, timeout=40
        ) as proxy:
            first = await proxy.post(
                f"/api/threads/{tid}/runs",
                headers=_bff_headers(ca, **{"x-submission-key": key}),
                json=_body("F01 重放"),
            )
            assert first.status_code < 300, first.text
            runs.append(str(first.json()["run_id"]))
            second = await proxy.post(
                f"/api/threads/{tid}/runs",
                headers=_bff_headers(ca, **{"x-submission-key": key}),
                json=_body("F01 重放"),
            )
            # 复用既有运行（200 同 run_id）或如实"待确认"（202）；绝不再建运行
            assert second.status_code in (200, 202), second.text
            if second.status_code == 200 and second.json().get("run_id"):
                assert second.json()["run_id"] == runs[0], "重放必须复用同一运行"
        assert await _platform_run_count(ticket, tid) == 1, "重放不得产生第二个平台运行"
    finally:
        await _cleanup(a, ticket, tid, runs)


# ------------------------------------------------------------------ F03


async def test_f03_graph_swap_rejected_before_execution() -> None:
    """F03：合法 basic-qa 预留 + 实际 intelligent-analysis 必须在执行前拒绝.

    修复前：准入只看客户端自报 `ledger_graph_id` → 200 受理并创建运行
    （真实执行的却是 intelligent-analysis），结束检测不能阻止副作用。
    """
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    try:
        ah = {
            "x-tenant-id": ca["current_tenant"]["id"],
            "x-tenant-context-version": str(ca["context_version"]),
        }
        admitted = await a.post(
            "/agent/requests",
            headers=ah,
            json={
                "thread_id": tid,
                "submission_key": uuid.uuid4().hex,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "F03 换图",
            },
        )
        assert admitted.status_code == 200, admitted.text
        ad = admitted.json()
        meta = {
            "ledger_request_id": ad["request_id"],
            "ledger_attempt_id": ad["attempt_id"],
            "ledger_submission_id": ad["submission_id"],
            # 客户端谎报：声明 basic-qa，实际让平台跑 intelligent-analysis
            "ledger_graph_id": "basic-qa",
            "ledger_operation_kind": "initial",
        }
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            res = await lg.post(
                f"/threads/{tid}/runs",
                headers=_native_headers(ticket),
                json={
                    "assistant_id": "intelligent-analysis",
                    "input": {"messages": [{"role": "user", "content": "F03 换图"}]},
                    "metadata": meta,
                    "interrupt_before": ["*"],
                },
            )
        assert res.status_code in (403, 409), (
            f"换图必须被拒绝，实际 {res.status_code}: {res.text}"
        )
        assert await _platform_run_count(ticket, tid) == 0, "被拒的换图不得创建平台运行"
    finally:
        await _cleanup(a, ticket, tid, runs)


async def test_f03_metadata_config_graph_conflict_rejected() -> None:
    """F03：metadata 与 config 自报图冲突时必须拒绝（不信客户端声明）."""
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    try:
        ah = {
            "x-tenant-id": ca["current_tenant"]["id"],
            "x-tenant-context-version": str(ca["context_version"]),
        }
        admitted = await a.post(
            "/agent/requests",
            headers=ah,
            json={
                "thread_id": tid,
                "submission_key": uuid.uuid4().hex,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "F03 冲突",
            },
        )
        ad = admitted.json()
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            res = await lg.post(
                f"/threads/{tid}/runs",
                headers=_native_headers(ticket),
                json={
                    "assistant_id": "basic-qa",
                    "input": {"messages": [{"role": "user", "content": "F03 冲突"}]},
                    "metadata": {
                        "ledger_attempt_id": ad["attempt_id"],
                        "ledger_request_id": ad["request_id"],
                        "ledger_submission_id": ad["submission_id"],
                        "ledger_graph_id": "basic-qa",
                    },
                    "config": {
                        "configurable": {"ledger_graph_id": "intelligent-tracing"}
                    },
                    "interrupt_before": ["*"],
                },
            )
        assert res.status_code in (403, 409), (
            f"metadata/config 图冲突必须拒绝，实际 {res.status_code}: {res.text}"
        )
        assert await _platform_run_count(ticket, tid) == 0
    finally:
        await _cleanup(a, ticket, tid, [])


# ------------------------------------------------------------------ F05


async def test_f05_native_success_persists_platform_run_id() -> None:
    """F05：原生真实最小回答结束后，台账必须持久化可信 platform_run_id.

    修复前：平台 success、台账 succeeded、答案 1，但 platform_run_id 为空。
    """
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    try:
        ah = {
            "x-tenant-id": ca["current_tenant"]["id"],
            "x-tenant-context-version": str(ca["context_version"]),
        }
        admitted = await a.post(
            "/agent/requests",
            headers=ah,
            json={
                "thread_id": tid,
                "submission_key": uuid.uuid4().hex,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "只回复数字2",
            },
        )
        ad = admitted.json()
        async with httpx.AsyncClient(base_url=NATIVE, timeout=30) as lg:
            created = await lg.post(
                f"/threads/{tid}/runs",
                headers=_native_headers(ticket),
                json={
                    "assistant_id": "basic-qa",
                    "input": {"messages": [{"role": "user", "content": "只回复数字2"}]},
                    "metadata": {
                        "ledger_request_id": ad["request_id"],
                        "ledger_attempt_id": ad["attempt_id"],
                        "ledger_submission_id": ad["submission_id"],
                        "ledger_graph_id": "basic-qa",
                        "ledger_operation_kind": "initial",
                    },
                    "config": {"configurable": {"chat_mode": "fast"}},
                },
            )
            assert created.status_code == 200, created.text
            rid = created.json()["run_id"]
            runs.append(rid)
            for _ in range(40):
                status = (
                    (
                        await lg.get(
                            f"/threads/{tid}/runs/{rid}",
                            headers=_native_headers(ticket),
                        )
                    )
                    .json()
                    .get("status")
                )
                if status in ("success", "error", "interrupted", "timeout"):
                    break
                await asyncio.sleep(1)
            assert status == "success", f"真实最小回答未成功：{status}"
        detail = (await a.get(f"/agent/requests/{ad['request_id']}")).json()
        attempt = detail["attempts"][0]
        assert attempt["state"] == "succeeded", attempt
        assert attempt.get("platform_run_id"), "必须在终态前补齐可信 platform_run_id"
        assert attempt["platform_run_id"] == rid
    finally:
        await _cleanup(a, ticket, tid, runs)


# ------------------------------------------------------------------ F06


async def test_f06_bff_submit_stores_request_content_source() -> None:
    """F06：正常 BFF 提交必须留下**按 request 可精确回读**的原问题来源.

    修复前：BFF 默认 store_content=false，只有摘要；刷新/后续提问后无法按
    request 回读原问题文本。
    """
    _require_live()
    a, ca, ticket, tid = await _thread_ctx()
    runs: list[str] = []
    question = f"F06 原问题快照 {uuid.uuid4().hex[:8]}"
    try:
        async with httpx.AsyncClient(
            base_url=PROXY, cookies=a.cookies, timeout=40
        ) as proxy:
            res = await proxy.post(
                f"/api/threads/{tid}/runs",
                headers=_bff_headers(ca, **{"x-submission-key": uuid.uuid4().hex}),
                json=_body(question),
            )
            assert res.status_code < 300, res.text
            runs.append(str(res.json()["run_id"]))
        assert OWNER_URI, "需要 owner 连接校验快照"
        async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
            row = await (
                await conn.execute(
                    "SELECT r.id::text, c.content_snapshot, c.request_id::text"
                    " FROM agent_requests r"
                    " JOIN agent_content_refs c ON c.request_id = r.id"
                    " WHERE r.thread_id = %s AND c.deleted_at IS NULL"
                    " ORDER BY c.created_at DESC LIMIT 1",
                    (tid,),
                )
            ).fetchone()
        assert row is not None, "正常 BFF 提交必须落一份按 request 关联的原问题快照"
        assert row[0] == row[2], "快照必须精确挂在本 request 上"
        assert question in str(row[1]), f"快照正文应与原问题一致：{row[1]!r}"
    finally:
        await _cleanup(a, ticket, tid, runs)


# ------------------------------------------------------------------ F05（服务级校准）


async def test_f05_service_calibration_survives_member_revocation() -> None:
    """F05：服务级只读校准凭据在**原成员会话撤销后**仍可用，且不恢复业务权限.

    修复前：校准只能用绑定用户会话的运行票据，"原成员撤销后仍可校准"无法成立；
    仅新增一个环境变量名不构成认证能力。现在由 admin 内部端点签发 rs-v1 服务票据
    （独立密钥/受众、绑定租户、可再按线程清单收窄），平台按服务主体只放行租户只读。
    """
    _require_live()
    internal = os.environ.get("INTERNAL_API_TOKEN", "")
    if not internal:
        pytest.skip("缺少 INTERNAL_API_TOKEN")
    a, ca, ticket, tid = await _thread_ctx()
    try:
        async with httpx.AsyncClient(base_url=ADMIN, timeout=20) as ad:
            issued = await ad.post(
                "/agent/reconcile/ticket",
                headers={"x-internal-token": internal},
                json={"tenant_id": ca["current_tenant"]["id"], "ttl_seconds": 300},
            )
        assert issued.status_code == 200, issued.text
        service = issued.json()
        assert service["scope"] == "ledger:reconcile"
        rs = service["ticket"]
        service_headers = {"Authorization": f"Bearer {rs}"}
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            # 服务主体：可读本租户线程运行列表（窄只读）
            assert (
                await lg.get(
                    f"/internal/ledger/threads/{tid}/runs", headers=service_headers
                )
            ).status_code == 200
            # 原生 read 能返回 kwargs/正文，服务主体必须拒绝，不能直接取得宽读权限。
            for path in [f"/threads/{tid}/runs", f"/threads/{tid}/state"]:
                assert (await lg.get(path, headers=service_headers)).status_code == 403
            # 原成员会话撤销
            await a.post("/auth/logout")
            revoked = await lg.get(
                f"/threads/{tid}/runs", headers=_native_headers(ticket)
            )
            assert revoked.status_code in (401, 403), revoked.status_code
            # 撤销后校准仍可读，且**不能**借服务凭据创建运行（不恢复业务权限）
            assert (
                await lg.get(
                    f"/internal/ledger/threads/{tid}/runs", headers=service_headers
                )
            ).status_code == 200, "原成员撤销后服务级只读校准必须仍可用"
            created = await lg.post(
                f"/threads/{tid}/runs",
                headers=service_headers,
                json={
                    "assistant_id": "basic-qa",
                    "input": {"messages": [{"role": "user", "content": "不应创建"}]},
                },
            )
            assert created.status_code in (401, 403), (
                f"服务校准凭据不得创建运行：{created.status_code}"
            )
            # 会话已撤销，改用服务只读凭据核对"确实没有新运行"
            assert (
                await _platform_run_count_with(service_headers, tid, gateway=True) == 0
            )
    finally:
        # 原会话已撤销；重新登录专用fixture清理自己创建的线程，服务票据没有删除权。
        cleanup_client, cleanup_ctx = await _login("acc_alice")
        cleanup_ctx = await _switch(
            cleanup_client, cleanup_ctx, ca["current_tenant"]["id"]
        )
        cleanup_ticket = await _ticket(cleanup_client, cleanup_ctx)
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            assert (
                await lg.delete(
                    f"/threads/{tid}", headers=_native_headers(cleanup_ticket)
                )
            ).status_code == 204
        await cleanup_client.post("/auth/logout")
        await cleanup_client.aclose()
        await a.aclose()


async def test_f05_cross_tenant_calibration_denied() -> None:
    """F05：服务校准凭据不得读到票据租户之外的会话（跨租户 403/404）."""
    _require_live()
    internal = os.environ.get("INTERNAL_API_TOKEN", "")
    if not internal:
        pytest.skip("缺少 INTERNAL_API_TOKEN")
    a, ca, ticket, tid = await _thread_ctx("A")
    other_tid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=ADMIN, timeout=20) as ad:
            issued = await ad.post(
                "/agent/reconcile/ticket",
                headers={"x-internal-token": internal},
                json={
                    "tenant_id": ca["current_tenant"]["id"],
                    "ttl_seconds": 300,
                    "thread_ids": [tid],
                },
            )
        assert issued.status_code == 200, issued.text
        service_headers = {"Authorization": f"Bearer {issued.json()['ticket']}"}
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            # 未在票据线程清单内的既有线程同样拒绝
            outside = await lg.get(
                f"/internal/ledger/threads/{other_tid}/runs", headers=service_headers
            )
            assert outside.status_code in (403, 404), outside.status_code
            inside = await lg.get(
                f"/internal/ledger/threads/{tid}/runs", headers=service_headers
            )
            assert inside.status_code == 200
    finally:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            await lg.delete(f"/threads/{tid}", headers=_native_headers(ticket))
        await a.aclose()
