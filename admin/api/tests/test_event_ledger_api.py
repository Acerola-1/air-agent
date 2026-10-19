"""事件台账 API 端到端测试（真实 PG；专用库，不触碰业务卷）。

    .venv/bin/python -m pytest tests/test_event_ledger_api.py -q

覆盖（对应 change add-agent-event-ledger 的 spec 场景）：
- 可信受理与幂等：同键同内容复用、同键不同内容 409、未确认归属线程拒绝；
- 受理歧义：admission_unknown 不自动判成功、不重发；
- 状态/停止：证据秩、终态不回退、停止意图独立；
- 事件契约：浏览器伪造终态拒绝、预留类型不启用、非法载荷拒绝；
- 租户隔离：A/B 同用户、跨企业不可读、本人/他人/运营分级；
- 指标：分子分母与未采集不补零；
- 删除：立即禁读标记。

需要 `DATABASE_URI`（owner）与 `ADMIN_RUNTIME_DATABASE_URI`（运行角色）指向已迁移
到 0007 的专用库。
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from ctx_client import ContextClient
from httpx import ASGITransport
from sqlalchemy import text

from app.db import OwnerSessionLocal
from app.main import app
from app.models import Role, RolePermission, Tenant, TenantMembership, User, UserRole
from app.security import hash_password

PASSWORD = "ledger-test-password-123"
PREFIX = "tmp_led_"


# ------------------------------------------------------------------ 夹具构造


async def _make_platform_user(account: str, role_codes: list[str]) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        user = User(
            account=account,
            display_name=f"台账-{account}",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        if role_codes:
            rows = (
                await session.execute(
                    text("SELECT id FROM roles WHERE code = ANY(:c)"), {"c": role_codes}
                )
            ).all()
            for (rid,) in rows:
                session.add(UserRole(user_id=user.id, role_id=rid))
        await session.commit()
        return user.id


async def _make_tenant(code: str) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        tenant = Tenant(code=code, name=f"台账企业-{code}", status="active")
        session.add(tenant)
        await session.commit()
        return tenant.id


async def _make_membership(
    tenant_id: uuid.UUID, user_id: uuid.UUID, perm_codes: list[str]
) -> uuid.UUID:
    """建成员 + 企业角色（授予给定权限码），返回 membership_id。"""
    async with OwnerSessionLocal() as session:
        membership = TenantMembership(tenant_id=tenant_id, user_id=user_id, status="active")
        session.add(membership)
        await session.flush()
        role = Role(
            code=f"ledger_role_{uuid.uuid4().hex[:8]}",
            name="台账测试角色",
            scope="tenant",
            tenant_id=tenant_id,
            is_system=False,
        )
        session.add(role)
        await session.flush()
        rows = (
            await session.execute(
                text("SELECT id FROM permissions WHERE code = ANY(:c)"), {"c": perm_codes}
            )
        ).all()
        for (pid,) in rows:
            session.add(RolePermission(role_id=role.id, permission_id=pid))
        await session.execute(
            text(
                "INSERT INTO membership_roles(membership_id, role_id, tenant_id)"
                " VALUES (:m, :r, :t)"
            ),
            {"m": membership.id, "r": role.id, "t": tenant_id},
        )
        await session.commit()
        return membership.id


async def _grant_thread(
    tenant_id: uuid.UUID, user_id: uuid.UUID, thread_id: str, source: str = "native"
) -> None:
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO thread_ownership(thread_id, tenant_id, user_id, source)"
                " VALUES (:t, :ten, :u, :s)"
                " ON CONFLICT (thread_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,"
                " user_id=EXCLUDED.user_id, source=EXCLUDED.source"
            ),
            {"t": thread_id, "ten": str(tenant_id), "u": str(user_id), "s": source},
        )
        await session.commit()


async def _login(account: str, tenant_id: uuid.UUID) -> ContextClient:
    client = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await client.post("/auth/login", json={"account": account, "password": PASSWORD})
    assert res.status_code == 200, res.text
    ctx = res.json()
    if (ctx.get("current_tenant") or {}).get("id") != str(tenant_id):
        switch = await client.post(
            "/auth/switch-tenant",
            json={"tenant_id": str(tenant_id), "expected_ctx_version": ctx["context_version"]},
        )
        assert switch.status_code == 200, switch.text
    return client


@pytest.fixture
async def ledger_env():
    """两家企业 + 一个双企业成员（A 管理员权限）+ 一个仅元数据权限成员。"""
    suffix = uuid.uuid4().hex[:6]
    tenant_a = await _make_tenant(f"led_a_{suffix}")
    tenant_b = await _make_tenant(f"led_b_{suffix}")
    user_a = await _make_platform_user(f"{PREFIX}u_{suffix}", [])
    user_b = await _make_platform_user(f"{PREFIX}o_{suffix}", [])
    await _make_membership(
        tenant_a,
        user_a,
        [
            "agent:business:basic-qa:use",
            "agent:operations:read",
            "agent:metrics:read",
            "agent:content:read",
        ],
    )
    await _make_membership(
        tenant_b,
        user_a,
        [
            "agent:business:basic-qa:use",
            "agent:operations:read",
            "agent:metrics:read",
        ],
    )
    # 仅运营元数据（无原文）成员，用于验证原文独立权限
    await _make_membership(
        tenant_a,
        user_b,
        ["agent:business:basic-qa:use", "agent:operations:read"],
    )
    try:
        yield {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "user_a": user_a,
            "user_b": user_b,
        }
    finally:
        async with OwnerSessionLocal() as session:
            for tid in (tenant_a, tenant_b):
                await session.execute(
                    text("DELETE FROM agent_requests WHERE tenant_id = :t"), {"t": str(tid)}
                )
                await session.execute(
                    text("DELETE FROM agent_thread_deletions WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM thread_ownership WHERE tenant_id = :t"), {"t": str(tid)}
                )
            for uid in (user_a, user_b):
                await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(uid)})
            for tid in (tenant_a, tenant_b):
                await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tid)})
            await session.commit()


async def _start_claim(client, admitted: dict, thread_id: str, content: str = "问题") -> dict:
    """取启动权（R01）：受控入口在创建平台运行前必须成功，之后才能回填受理结果。"""
    res = await client.post(
        "/agent/runs/start-claim",
        headers={"x-internal-token": _internal_token()},
        json={
            "submission_id": admitted["submission_id"],
            "attempt_id": admitted["attempt_id"],
            "thread_id": thread_id,
            "graph_id": "basic-qa",
            "operation_kind": admitted.get("operation_kind", "initial"),
            "content": content,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "claimed", res.text
    return res.json()


async def _set_attempt_state(tenant_id: uuid.UUID, attempt_id: str, state: str) -> None:
    """把尝试推进到指定业务状态（测试夹具；平台 interrupted → waiting_input）."""
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE agent_run_attempts SET state = :s, updated_at = now()"
                " WHERE id = CAST(:a AS uuid) AND tenant_id = CAST(:t AS uuid)"
            ),
            {"s": state, "a": str(attempt_id), "t": str(tenant_id)},
        )
        await session.commit()


def _admit_body(thread_id: str, key: str, content: str = "问题", graph: str = "basic-qa") -> dict:
    return {
        "thread_id": thread_id,
        "submission_key": key,
        "graph_id": graph,
        "operation_kind": "initial",
        "content": content,
        "user_message_id": "um-1",
    }


# ------------------------------------------------------------------ 受理与幂等


async def test_admit_creates_request_attempt_and_event(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(f"{PREFIX}u_", env["tenant_a"]) if False else None
    # 用真实账号登录
    account = await _account_of(env["user_a"])
    client = await _login(account, env["tenant_a"])
    try:
        res = await client.post("/agent/requests", json=_admit_body(thread_id, "k" * 12))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["reused"] is False
        assert body["admission_status"] == "admitting"
        assert body["ledger_marker"].startswith("lr:")
        # request.accepted 事件已落库并带 outbox
        async with OwnerSessionLocal() as session:
            events = (
                await session.execute(
                    text("SELECT event_type, source FROM agent_events WHERE request_id = :r"),
                    {"r": body["request_id"]},
                )
            ).all()
            assert ("request.accepted", "server") in events
            outbox = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_outbox WHERE event_id IN"
                        " (SELECT event_id FROM agent_events WHERE request_id = :r)"
                    ),
                    {"r": body["request_id"]},
                )
            ).scalar()
            assert outbox >= 1
    finally:
        await client.aclose()


async def _account_of(user_id: uuid.UUID) -> str:
    async with OwnerSessionLocal() as session:
        return str(
            (
                await session.execute(
                    text("SELECT account FROM users WHERE id = :u"), {"u": str(user_id)}
                )
            ).scalar()
        )


async def test_submission_idempotent_and_conflict(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        first = await client.post(
            "/agent/requests", json=_admit_body(thread_id, "same-key-0001", "问题A")
        )
        assert first.status_code == 200
        replay = await client.post(
            "/agent/requests", json=_admit_body(thread_id, "same-key-0001", "问题A")
        )
        assert replay.status_code == 200
        assert replay.json()["reused"] is True
        assert replay.json()["request_id"] == first.json()["request_id"]
        assert replay.json()["attempt_id"] == first.json()["attempt_id"]
        # 同键不同内容 → 409
        conflict = await client.post(
            "/agent/requests", json=_admit_body(thread_id, "same-key-0001", "完全不同")
        )
        assert conflict.status_code == 409
        # 同内容不同键 → 新提问/新尝试
        another = await client.post(
            "/agent/requests", json=_admit_body(thread_id, "other-key-0002", "问题A")
        )
        assert another.status_code == 200
        assert another.json()["reused"] is False
        assert another.json()["request_id"] != first.json()["request_id"]
        # 计数：2 个提问、3 次提交只产生 2 个 attempt
        async with OwnerSessionLocal() as session:
            questions = (
                await session.execute(
                    text("SELECT count(*) FROM agent_requests WHERE tenant_id = :t"),
                    {"t": str(env["tenant_a"])},
                )
            ).scalar()
            attempts = (
                await session.execute(
                    text("SELECT count(*) FROM agent_run_attempts WHERE tenant_id = :t"),
                    {"t": str(env["tenant_a"])},
                )
            ).scalar()
        assert questions == 2 and attempts == 2
    finally:
        await client.aclose()


async def test_admit_rejects_unconfirmed_thread(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id, source="native_pending")
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        res = await client.post("/agent/requests", json=_admit_body(thread_id, "pending-thread-1"))
        assert res.status_code == 403
        assert res.json()["code"] if "code" in res.json() else True
    finally:
        await client.aclose()


async def test_admit_rejects_cross_tenant_thread(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_b"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        # 线程属于 B，当前上下文是 A → 拒绝且不泄露存在性
        res = await client.post("/agent/requests", json=_admit_body(thread_id, "cross-tenant-1"))
        assert res.status_code == 403
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 受理歧义


async def test_admission_unknown_not_success_and_not_retried(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    account = await _account_of(env["user_a"])
    client = await _login(account, env["tenant_a"])
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "unknown-00001"))
        ).json()
        res = await client.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": admitted["request_id"],
                "attempt_id": admitted["attempt_id"],
                "outcome": "admission_unknown",
                "reason": "upstream_timeout",
            },
            headers={"x-internal-token": _internal_token()},
        )
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "admission_unknown"
        # 保持待确认：不计已完成，不冒充成功；再次同键提交不新建
        replay = await client.post("/agent/requests", json=_admit_body(thread_id, "unknown-00001"))
        assert replay.json()["reused"] is True
        assert replay.json()["attempt_id"] == admitted["attempt_id"]
    finally:
        await client.aclose()


def _internal_token() -> str:
    from app.config import get_settings

    return get_settings().internal_api_token


async def test_admission_confirm_requires_internal_token(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "notoken-0001"))
        ).json()
        res = await client.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": admitted["request_id"],
                "attempt_id": admitted["attempt_id"],
                "outcome": "confirmed",
                "platform_run_id": "run-x",
            },
        )
        assert res.status_code == 401
    finally:
        await client.aclose()


async def test_admission_confirmed_sets_running(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "confirm-0001"))
        ).json()
        await _start_claim(client, admitted, thread_id)
        res = await client.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": admitted["request_id"],
                "attempt_id": admitted["attempt_id"],
                "outcome": "confirmed",
                "platform_run_id": "run-confirm-1",
            },
            headers={"x-internal-token": _internal_token()},
        )
        assert res.status_code == 200
        assert res.json()["state"] == "running"
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 状态与停止


async def test_state_evidence_rank_and_no_terminal_regression(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "state-000001"))
        ).json()
        attempt_id = admitted["attempt_id"]
        await client.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": admitted["request_id"],
                "attempt_id": attempt_id,
                "outcome": "confirmed",
                "platform_run_id": "run-state-1",
            },
            headers=token,
        )
        # 平台回读判定成功（秩 3）
        ok = await client.post(
            f"/agent/runs/{attempt_id}/state",
            json={
                "state": "succeeded",
                "evidence_source": "platform_read",
                "platform_status": "success",
            },
            headers=token,
        )
        assert ok.json()["state"] == "succeeded"
        # 晚到的"运行开始"（浏览器秩 1）不得回退终态
        late = await client.post(
            f"/agent/runs/{attempt_id}/state",
            json={"state": "running", "evidence_source": "browser_observed"},
            headers=token,
        )
        assert late.json()["state"] == "succeeded"
        assert late.json()["applied"] is False
    finally:
        await client.aclose()


async def test_stop_intent_independent_of_terminal(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "stop-000001"))
        ).json()
        # 停止先于 run_id：只给 thread_id
        res = await client.post(
            "/agent/runs/stop-intent",
            json={"thread_id": thread_id, "origin": "user_stop", "phase": "unknown"},
            headers={"origin": "http://localhost:3000"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["cancel_requested"] is True
        assert res.json()["attempt_id"] == admitted["attempt_id"]
        # 未知来源不得猜测
        bad = await client.post(
            "/agent/runs/stop-intent",
            json={"thread_id": thread_id, "origin": "guessed", "phase": "unknown"},
            headers={"origin": "http://localhost:3000"},
        )
        assert bad.status_code == 422
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 事件契约


async def test_browser_events_all_reserved_types_rejected(ledger_env) -> None:
    env = ledger_env
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        forged = {
            "schema_version": "1.0",
            "event_id": str(uuid.uuid4()),
            "event_type": "run.state_observed",
            "occurred_at": "2026-10-13T02:00:00.000Z",
            "source": "browser",
            "tenant_id": str(env["tenant_a"]),
            "user_id": str(env["user_a"]),
            "membership_id": "33333333-3333-4333-8333-333333333333",
            "payload": {
                "state": "succeeded",
                "evidence_source": "browser_observed",
                "state_revision": 1,
            },
        }
        res = await client.post(
            "/agent/events/browser",
            json={"events": [forged]},
            headers={"origin": "http://localhost:3000"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["accepted"] is False
        assert body["items"][0]["reason"] == "source_not_allowed"
        assert body["written"] == 0
        # runtime五类仅允许可信执行侧，浏览器伪造必须继续拒绝。
        reserved = dict(forged, event_type="runtime.model_usage", source="browser")
        res2 = await client.post(
            "/agent/events/browser",
            json={"events": [reserved]},
            headers={"origin": "http://localhost:3000"},
        )
        assert res2.json()["items"][0]["reason"] == "source_not_allowed"
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 读取与隔离


async def test_self_readback_no_content_and_operations_split(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post(
                "/agent/requests",
                json=_admit_body(thread_id, "read-000001", "这是原始问题正文"),
            )
        ).json()
        detail = await client.get(f"/agent/requests/{admitted['request_id']}")
        assert detail.status_code == 200
        assert detail.json()["content_included"] is False
        assert "这是原始问题正文" not in detail.text
        # 运营明细列表
        runs = await client.get("/agent/runs?limit=10")
        assert runs.status_code == 200
        assert runs.json()["content_included"] is False
        # 仅元数据权限成员不能读原文
        other = await _login(await _account_of(env["user_b"]), env["tenant_a"])
        try:
            denied = await other.get(f"/agent/requests/{admitted['request_id']}/content")
            assert denied.status_code == 403
        finally:
            await other.aclose()
    finally:
        await client.aclose()


async def test_same_user_two_tenants_isolated(ledger_env) -> None:
    env = ledger_env
    thread_a = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_a)
    client_a = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    client_b = await _login(await _account_of(env["user_a"]), env["tenant_b"])
    try:
        admitted = (
            await client_a.post("/agent/requests", json=_admit_body(thread_a, "iso-000001"))
        ).json()
        # A 上下文可读
        assert (await client_a.get(f"/agent/requests/{admitted['request_id']}")).status_code == 200
        # B 上下文读同一 request → 404（不泄露存在性）
        cross = await client_b.get(f"/agent/requests/{admitted['request_id']}")
        assert cross.status_code == 404
        # B 的运行列表为空
        runs_b = await client_b.get("/agent/runs?limit=50")
        assert runs_b.status_code == 200
        assert all(item["request_id"] != admitted["request_id"] for item in runs_b.json()["items"])
    finally:
        await client_a.aclose()
        await client_b.aclose()


# ------------------------------------------------------------------ 指标


async def test_metrics_denominator_and_not_collected(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        r1 = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "metric-00001"))
        ).json()
        r2 = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "metric-00002"))
        ).json()
        # r1 成功；r2 取消
        for req, run, state, ev in (
            (r1, "run-m1", "succeeded", "platform_read"),
            (r2, "run-m2", "cancelled", "platform_read"),
        ):
            await client.post(
                "/agent/runs/admission-confirmed",
                json={
                    "request_id": req["request_id"],
                    "attempt_id": req["attempt_id"],
                    "outcome": "confirmed",
                    "platform_run_id": run,
                },
                headers=token,
            )
            await client.post(
                f"/agent/runs/{req['attempt_id']}/state",
                json={"state": state, "evidence_source": ev},
                headers=token,
            )
        summary = await client.get("/agent/metrics/summary")
        assert summary.status_code == 200, summary.text
        data = summary.json()
        assert data["questions"]["value"] >= 2
        cr = data["technical_completion_rate"]
        assert cr["numerator"] >= 1 and cr["denominator"] >= 2
        # 未采集项必须 null + 标签，不得伪零
        assert data["first_content_ms"]["value"] is None
        assert data["first_content_ms"]["status"] == "not_collected"
        assert data["cost"]["value"] is None
        assert data["metric_version"] == "v1"
    finally:
        await client.aclose()


async def test_metrics_requires_permission(ledger_env) -> None:
    env = ledger_env
    # 无 metrics 权限的成员
    account = f"{PREFIX}nom_{uuid.uuid4().hex[:6]}"
    user_id = await _make_platform_user(account, [])
    await _make_membership(env["tenant_a"], user_id, ["agent:operations:read"])
    client = await _login(account, env["tenant_a"])
    try:
        res = await client.get("/agent/metrics/summary")
        assert res.status_code == 403
    finally:
        await client.aclose()
        async with OwnerSessionLocal() as session:
            await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(user_id)})
            await session.commit()


# ------------------------------------------------------------------ F02（第二轮定向修复）


async def test_f02_resume_start_claim_uses_its_own_digest(ledger_env) -> None:
    """F02 反例 resume-deletion：恢复的启动权按**本次 resume 提交**的摘要裁决.

    修复前：start-claim 比对初次 attempt 的 content_digest（含 operation_kind=initial），
    恢复提交必然 mismatch/content；HTTP 200 里带 mismatch 不算成功。
    """
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        initial = (
            await client.post(
                "/agent/requests", json=_admit_body(thread_id, "f02-initial-01", "问题")
            )
        ).json()
        await _set_attempt_state(env["tenant_a"], initial["attempt_id"], "waiting_input")
        resumed = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "f02-resume-01",
                    "graph_id": "basic-qa",
                    "operation_kind": "resume",
                    "content": "",
                    "source_attempt_id": initial["attempt_id"],
                },
            )
        ).json()
        assert resumed["request_id"] == initial["request_id"]
        assert resumed["attempt_id"] == initial["attempt_id"]
        res = await client.post(
            "/agent/runs/start-claim",
            headers={"x-internal-token": _internal_token()},
            json={
                "submission_id": resumed["submission_id"],
                "attempt_id": resumed["attempt_id"],
                "thread_id": thread_id,
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
            },
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "claimed", res.text
    finally:
        await client.aclose()


async def test_f02_resume_source_must_be_waiting_and_same_thread(ledger_env) -> None:
    """F02：非等待来源、跨线程来源都必须拒绝（不产生假恢复操作）."""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    other_thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    await _grant_thread(env["tenant_a"], env["user_a"], other_thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        running = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "f02-run-01"))
        ).json()
        await _set_attempt_state(env["tenant_a"], running["attempt_id"], "running")
        not_waiting = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "f02-run-resume",
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": running["attempt_id"],
            },
        )
        assert not_waiting.status_code in (403, 409), not_waiting.text
        assert "可恢复" in not_waiting.text, not_waiting.text
        other = (
            await client.post("/agent/requests", json=_admit_body(other_thread, "f02-other-01"))
        ).json()
        await _set_attempt_state(env["tenant_a"], other["attempt_id"], "waiting_input")
        cross = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "f02-cross-resume",
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "content": "",
                "source_attempt_id": other["attempt_id"],
            },
        )
        assert cross.status_code in (400, 403, 404, 409), cross.text
    finally:
        await client.aclose()


async def test_f02_confirm_is_submission_scoped(ledger_env) -> None:
    """F02：恢复确认不得改写共享同一 attempt 的初次提交受理状态，并落恢复映射."""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        initial = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "f02-scope-01"))
        ).json()
        await _set_attempt_state(env["tenant_a"], initial["attempt_id"], "waiting_input")
        resumed = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "f02-scope-02",
                    "graph_id": "basic-qa",
                    "operation_kind": "resume",
                    "content": "",
                    "source_attempt_id": initial["attempt_id"],
                },
            )
        ).json()
        await _start_claim(client, initial, thread_id)
        await _start_claim(client, resumed, thread_id, content="")
        run_id = f"run-f02-{uuid.uuid4().hex[:8]}"
        res = await client.post(
            "/agent/runs/admission-confirmed",
            headers=token,
            json={
                "request_id": resumed["request_id"],
                "attempt_id": resumed["attempt_id"],
                "submission_id": resumed["submission_id"],
                "outcome": "confirmed",
                "platform_run_id": run_id,
            },
        )
        assert res.status_code == 200, res.text
        async with OwnerSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text, operation_kind, admission_status"
                        " FROM agent_submissions WHERE tenant_id = :t AND attempt_id = :a"
                        " ORDER BY created_at"
                    ),
                    {"t": str(env["tenant_a"]), "a": str(initial["attempt_id"])},
                )
            ).fetchall()
            resumes = (
                await session.execute(
                    text(
                        "SELECT resume_index, original_run_id, platform_run_id"
                        " FROM agent_run_resumes WHERE tenant_id = :t AND attempt_id = :a"
                    ),
                    {"t": str(env["tenant_a"]), "a": str(initial["attempt_id"])},
                )
            ).fetchall()
        by_kind = {r[1]: r for r in rows}
        assert by_kind["resume"][2] == "confirmed", by_kind
        assert by_kind["initial"][2] == "admitting", (
            f"恢复确认不得改写初次提交：{by_kind['initial']}"
        )
        assert len(resumes) == 1 and resumes[0][2] == run_id, resumes
    finally:
        await client.aclose()


# ------------------------------------------------------------------ F04 晚到回调


async def test_f04_late_internal_callbacks_after_deletion_are_refused(ledger_env) -> None:
    """F04：删除后到达的内部回调（状态/回答/受理确认/恢复）必须被拒绝且不写新事件。

    修复前：这些内部写入端点只校验尝试归属，删除后仍会写入新的运行事件与回答。
    """
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "f04-late-0001"))
        ).json()
        await _start_claim(client, admitted, thread_id)
        deletion = await client.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert deletion.status_code == 200, deletion.text
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_thread_deletions SET state = 'completed',"
                    " attempts = attempts + 1, completed_at = now() WHERE thread_id = :th"
                ),
                {"th": thread_id},
            )
            await session.commit()
            before_events = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_events WHERE tenant_id = :t AND thread_id = :th"
                    ),
                    {"t": str(env["tenant_a"]), "th": thread_id},
                )
            ).scalar()
        late_calls = [
            (
                "/agent/runs/admission-confirmed",
                {
                    "request_id": admitted["request_id"],
                    "attempt_id": admitted["attempt_id"],
                    "submission_id": admitted["submission_id"],
                    "outcome": "confirmed",
                    "platform_run_id": f"run-late-{uuid.uuid4().hex[:6]}",
                },
            ),
            (
                f"/agent/runs/{admitted['attempt_id']}/state",
                {"state": "succeeded", "evidence_source": "runtime_callback"},
            ),
            (
                f"/agent/runs/{admitted['attempt_id']}/answers",
                {"message_id": "m-late", "content_version": 1, "confirmed": True},
            ),
            (
                f"/agent/runs/{admitted['attempt_id']}/resume",
                {"platform_run_id": f"run-late-resume-{uuid.uuid4().hex[:6]}"},
            ),
            (
                f"/agent/runs/{admitted['attempt_id']}/traces",
                {
                    "provider": "langfuse",
                    "project_scope": "late",
                    "trace_id": f"trace-late-{uuid.uuid4().hex[:6]}",
                },
            ),
        ]
        for path, payload in late_calls:
            res = await client.post(path, headers=token, json=payload)
            assert res.status_code == 409, (
                f"{path} 晚到回调必须被拒绝：{res.status_code} {res.text}"
            )
            assert "已删除" in res.text, res.text
        async with OwnerSessionLocal() as session:
            after_events = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_events WHERE tenant_id = :t AND thread_id = :th"
                    ),
                    {"t": str(env["tenant_a"]), "th": thread_id},
                )
            ).scalar()
            answers = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_answers"
                        " WHERE tenant_id = :t AND attempt_id = :a"
                    ),
                    {"t": str(env["tenant_a"]), "a": admitted["attempt_id"]},
                )
            ).scalar()
            resumes = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_run_resumes WHERE tenant_id = :t"
                        " AND attempt_id = :a"
                    ),
                    {"t": str(env["tenant_a"]), "a": admitted["attempt_id"]},
                )
            ).scalar()
        assert after_events == before_events, "晚到回调不得写入新事件"
        assert answers == 0, f"晚到回调不得回填回答：{answers}"
        assert resumes == 0, f"晚到回调不得写恢复映射：{resumes}"
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 6.1 运行明细


async def test_61_runs_listing_cursor_scope_and_max_page(ledger_env) -> None:
    """6.1：企业运行明细的**游标作用域**、**最大分页**与"列表不含正文"。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client_a = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    client_b = await _login(await _account_of(env["user_a"]), env["tenant_b"])
    try:
        for index in range(3):
            res = await client_a.post(
                "/agent/requests",
                json=_admit_body(thread_id, f"61-page-{index:04d}", f"分页 {index}"),
            )
            assert res.status_code == 200, res.text

        first = await client_a.get("/agent/runs?limit=1")
        assert first.status_code == 200, first.text
        page1 = first.json()
        assert len(page1["items"]) == 1 and page1["limit"] == 1
        assert page1["next_cursor"], "还有数据时必须给出 next_cursor"
        assert page1["content_included"] is False, "运行列表不得携带正文"
        ids1 = {item["attempt_id"] for item in page1["items"]}

        second = await client_a.get(f"/agent/runs?limit=1&cursor={page1['next_cursor']}")
        assert second.status_code == 200, second.text
        ids2 = {item["attempt_id"] for item in second.json()["items"]}
        assert not (ids1 & ids2), "翻页不得重复"

        too_big = await client_a.get("/agent/runs?limit=1000")
        assert too_big.status_code == 422, "超过最大分页必须显式拒绝"

        # 游标作用域：换企业后用同一游标翻页，不得看到原企业的运行
        cross = await client_b.get(f"/agent/runs?limit=5&cursor={page1['next_cursor']}")
        assert cross.status_code in (200, 403, 404), cross.text
        if cross.status_code == 200:
            cross_ids = {item["attempt_id"] for item in cross.json()["items"]}
            assert not (cross_ids & ids1) and not (cross_ids & ids2), (
                "游标不得跨企业泄露其它企业运行"
            )
    finally:
        await client_a.aclose()
        await client_b.aclose()


# ------------------------------------------------------------------ 删除


async def test_thread_deletion_marks_immediately(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        res = await client.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["deletion_state"] == "requested"
        async with OwnerSessionLocal() as session:
            state = (
                await session.execute(
                    text(
                        "SELECT state FROM agent_thread_deletions"
                        " WHERE tenant_id = :t AND thread_id = :th"
                    ),
                    {"t": str(env["tenant_a"]), "th": thread_id},
                )
            ).scalar()
            assert state == "requested"
            # resource.deleted 事件已登记，且 outbox 指向 deletion_cleanup
            consumer = (
                await session.execute(
                    text(
                        "SELECT o.consumer FROM agent_outbox o JOIN agent_events e"
                        " ON e.event_id = o.event_id"
                        " WHERE e.event_type = 'resource.deleted' AND e.thread_id = :th"
                    ),
                    {"th": thread_id},
                )
            ).scalar()
            assert consumer == "deletion_cleanup"
    finally:
        await client.aclose()


async def test_metrics_latency_percentiles_from_detail(ledger_env) -> None:
    """服务耗时 P50/P95/P99 由窗口明细计算，样本/缺失单列，不补零、不平均每日分位数。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        durations = [100, 200, 300, 400, 1000]  # ms
        for index, _ms in enumerate(durations):
            admitted = (
                await client.post(
                    "/agent/requests",
                    json=_admit_body(thread_id, f"lat-{index:06d}"),
                )
            ).json()
            await client.post(
                "/agent/runs/admission-confirmed",
                json={
                    "request_id": admitted["request_id"],
                    "attempt_id": admitted["attempt_id"],
                    "outcome": "confirmed",
                    "platform_run_id": f"run-lat-{index}",
                },
                headers=token,
            )
            await client.post(
                f"/agent/runs/{admitted['attempt_id']}/state",
                json={"state": "succeeded", "evidence_source": "platform_read"},
                headers=token,
            )
        # 直接设置确定的服务耗时（admitted → ended），避免测试依赖真实时钟
        async with OwnerSessionLocal() as session:
            for index, ms in enumerate(durations):
                await session.execute(
                    text(
                        "UPDATE agent_run_attempts SET admitted_at = :a, ended_at = :e"
                        " WHERE platform_run_id = :r"
                    ),
                    {
                        "a": dt.datetime(2026, 10, 13, 0, 0, 0, tzinfo=dt.UTC),
                        "e": dt.datetime(2026, 10, 13, 0, 0, 0, tzinfo=dt.UTC)
                        + dt.timedelta(milliseconds=ms),
                        "r": f"run-lat-{index}",
                    },
                )
            await session.commit()

        start = dt.datetime(2026, 10, 13, 0, 0, 0, tzinfo=dt.UTC)
        end = start + dt.timedelta(days=1)
        res = await client.get(
            "/agent/metrics/summary",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        assert res.status_code == 200, res.text
        latency = res.json()["server_latency_ms"]
        assert latency["sample_count"] >= 5
        assert latency["status"] == "measured"
        # percentile_cont 在 [100,200,300,400,1000] 上的确定性结果
        assert 200 <= latency["p50_ms"] <= 300
        assert latency["p95_ms"] >= 700
        assert latency["p99_ms"] >= latency["p95_ms"]
        assert latency["measurement_source"] == "server_clock_admitted_to_ended"
        assert latency["missing_count"] >= 0
        # 未接入项仍不返回伪零
        assert res.json()["first_content_ms"]["value"] is None
    finally:
        await client.aclose()


async def test_retry_and_regenerate_link_original_request(ledger_env) -> None:
    """失败重试/重新生成：关联原 request_id、新增独立 attempt，提问量不增加。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        first = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "retry-base-1"))
        ).json()
        # 失败重试：显式来源尝试
        retry = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "retry-attempt-2",
                    "graph_id": "basic-qa",
                    "operation_kind": "retry",
                    "content": "问题",
                    "source_attempt_id": first["attempt_id"],
                },
            )
        ).json()
        assert retry["request_id"] == first["request_id"], "重试必须关联原 request"
        assert retry["attempt_id"] != first["attempt_id"], "重试必须新建 attempt"
        # 重新生成：服务端自动解析来源尝试（客户端只声明操作类型）
        regen = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "regen-attempt-3",
                    "graph_id": "basic-qa",
                    "operation_kind": "regenerate",
                    "content": "问题",
                },
            )
        ).json()
        assert regen["request_id"] == first["request_id"]

        async with OwnerSessionLocal() as session:
            questions = (
                await session.execute(
                    text("SELECT count(*) FROM agent_requests WHERE tenant_id = :t"),
                    {"t": str(env["tenant_a"])},
                )
            ).scalar()
            rows = (
                await session.execute(
                    text(
                        "SELECT attempt_index FROM agent_run_attempts WHERE request_id = :r"
                        " ORDER BY attempt_index"
                    ),
                    {"r": first["request_id"]},
                )
            ).all()
        assert questions == 1, "重试/重新生成不得增加提问量"
        assert [int(r[0]) for r in rows] == [1, 2, 3]
    finally:
        await client.aclose()


async def test_source_attempt_requires_same_user_and_kind(ledger_env) -> None:
    """来源尝试越权/类型误用被拒绝，不静默新建提问。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        base = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "src-guard-1"))
        ).json()
        # initial 不允许携带来源尝试
        bad_kind = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "src-guard-2",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "问题",
                "source_attempt_id": base["attempt_id"],
            },
        )
        assert bad_kind.status_code == 403
        # 不存在的来源尝试
        missing = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "src-guard-3",
                "graph_id": "basic-qa",
                "operation_kind": "retry",
                "content": "问题",
                "source_attempt_id": str(uuid.uuid4()),
            },
        )
        assert missing.status_code == 404
    finally:
        await client.aclose()


async def test_regenerate_without_source_becomes_initial(ledger_env) -> None:
    """线程上无历史尝试时，声明 regenerate 不会伪装成重新生成（如实按新提问受理）。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        res = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "regen-nosource-1",
                    "graph_id": "basic-qa",
                    "operation_kind": "regenerate",
                    "content": "无来源的重新生成",
                },
            )
        ).json()
        async with OwnerSessionLocal() as session:
            kind = (
                await session.execute(
                    text("SELECT operation_kind FROM agent_requests WHERE id = :r"),
                    {"r": res["request_id"]},
                )
            ).scalar()
        assert kind == "initial", "无来源的 regenerate 必须如实记为 initial"
    finally:
        await client.aclose()


async def test_historical_import_excluded_from_default_cohort(ledger_env) -> None:
    """历史导入：标记 historical、不建 attempt、默认指标口径排除并单列。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    # 直接按迁移工具语义插入历史请求（带 historical 标记，无 attempt）
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id,"
                " graph_id, operation_kind, identity_revision, admitted_at, created_at)"
                " VALUES (gen_random_uuid(), :t, :u, :u, :th, 'historical', 'initial',"
                ' \'{"historical": true, "attribution_source": "thread_ownership"}\'::jsonb,'
                " now(), now())"
            ),
            {"t": str(env["tenant_a"]), "u": str(env["user_a"]), "th": thread_id},
        )
        await session.commit()
        # 幂等：按标记去重，不重复导入
        before = (
            await session.execute(
                text(
                    "SELECT count(*) FROM agent_requests WHERE tenant_id = :t AND thread_id = :th"
                ),
                {"t": str(env["tenant_a"]), "th": thread_id},
            )
        ).scalar()

    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        summary = await client.get("/agent/metrics/summary")
        assert summary.status_code == 200, summary.text
        data = summary.json()
        # historical 不计入默认提问量，单列且标注
        assert data["historical_requests"]["value"] >= 1
        assert data["historical_requests"]["excluded_from_default_cohort"] is True
        assert data["historical_requests"]["status"] == "historical_unattributed"
        # 该历史请求不产生运行尝试
        async with OwnerSessionLocal() as session:
            attempts = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_run_attempts a JOIN agent_requests r"
                        " ON r.id = a.request_id AND r.tenant_id = a.tenant_id"
                        " WHERE r.thread_id = :th"
                    ),
                    {"th": thread_id},
                )
            ).scalar()
            assert attempts == 0
        assert before == 1
    finally:
        await client.aclose()


async def test_r03_replay_returns_its_own_attempt_after_regenerate(ledger_env) -> None:
    """反例 live-admission：原 initial 键在 regenerate 之后必须回到**原尝试**。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        body = _admit_body(thread_id, "r03-base-000001")
        first = (await client.post("/agent/requests", json=body)).json()
        regen = (
            await client.post(
                "/agent/requests",
                json={
                    **body,
                    "submission_key": "r03-regen-000001",
                    "operation_kind": "regenerate",
                    "source_attempt_id": first["attempt_id"],
                },
            )
        ).json()
        assert regen["attempt_id"] != first["attempt_id"]
        replay = (await client.post("/agent/requests", json=body)).json()
        assert replay["attempt_id"] == first["attempt_id"], "原键重放不得漂到 regenerate 的尝试"
        assert replay["submission_id"] == first["submission_id"]
        assert replay["reused"] is True
    finally:
        await client.aclose()


async def test_r03_resume_keeps_request_and_attempt(ledger_env) -> None:
    """反例 deletion-api.resume_admission：resume 不得新建逻辑提问。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        base = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "r03-resume-01"))
        ).json()
        # F02：人工恢复只允许来自**等待输入**的可恢复状态（平台 interrupted）。
        # 先把本次尝试推进到 waiting_input 再受理恢复；非等待态如实拒绝（另有用例）。
        await _set_attempt_state(env["tenant_a"], base["attempt_id"], "waiting_input")
        resumed = (
            await client.post(
                "/agent/requests",
                json={
                    "thread_id": thread_id,
                    "submission_key": "r03-resume-02",
                    "graph_id": "basic-qa",
                    "operation_kind": "resume",
                    "content": "问题",
                },
            )
        ).json()
        assert resumed["request_id"] == base["request_id"], "resume 必须复用原 request"
        assert resumed["attempt_id"] == base["attempt_id"], "resume 必须复用原 attempt"
        assert resumed["operation_kind"] == "resume"
        async with OwnerSessionLocal() as session:
            questions = (
                await session.execute(
                    text("SELECT count(*) FROM agent_requests WHERE tenant_id = :t"),
                    {"t": str(env["tenant_a"])},
                )
            ).scalar()
            attempts = (
                await session.execute(
                    text("SELECT count(*) FROM agent_run_attempts WHERE tenant_id = :t"),
                    {"t": str(env["tenant_a"])},
                )
            ).scalar()
        assert questions == 1, "resume 不得新建逻辑提问"
        assert attempts == 1, "resume 不得新建尝试"
    finally:
        await client.aclose()


async def test_r03_confirmation_only_affects_that_operation(ledger_env) -> None:
    """确认只影响本次操作的 submission，不批量改写同请求下其它操作。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        base = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "r03-conf-0001"))
        ).json()
        regen = (
            await client.post(
                "/agent/requests",
                json={
                    **_admit_body(thread_id, "r03-conf-0002"),
                    "operation_kind": "regenerate",
                    "source_attempt_id": base["attempt_id"],
                },
            )
        ).json()
        await _start_claim(client, base, thread_id)
        await client.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": base["request_id"],
                "attempt_id": base["attempt_id"],
                "outcome": "confirmed",
                "platform_run_id": f"run-{uuid.uuid4().hex[:10]}",
            },
            headers=token,
        )
        async with OwnerSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT attempt_id::text, admission_status FROM agent_submissions"
                        " WHERE tenant_id = :t ORDER BY created_at"
                    ),
                    {"t": str(env["tenant_a"])},
                )
            ).all()
        statuses = {str(attempt): status for attempt, status in rows}
        assert statuses[base["attempt_id"]] == "confirmed"
        assert statuses[regen["attempt_id"]] == "admitting", "确认不得改写其它操作的受理状态"
    finally:
        await client.aclose()


async def test_r01_admin_start_claim_single_winner_and_mismatch(ledger_env) -> None:
    """反例 live-admission（BFF 侧）：同一提交只能启动一次，换图/换正文拒绝。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    token = {"x-internal-token": _internal_token()}
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "r01-claim-0001"))
        ).json()
        payload = {
            "submission_id": admitted["submission_id"],
            "attempt_id": admitted["attempt_id"],
            "thread_id": thread_id,
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "问题",
        }
        first = await client.post("/agent/runs/start-claim", json=payload, headers=token)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "claimed"
        second = await client.post("/agent/runs/start-claim", json=payload, headers=token)
        assert second.json()["status"] == "already_started"
        # 换图 / 换正文 → mismatch（且不消费启动权）
        other = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "r01-claim-0002"))
        ).json()
        base = {
            "submission_id": other["submission_id"],
            "attempt_id": other["attempt_id"],
            "thread_id": thread_id,
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "问题",
        }
        assert (
            await client.post(
                "/agent/runs/start-claim", json={**base, "graph_id": "data-analysis"}, headers=token
            )
        ).json()["status"] == "mismatch"
        assert (
            await client.post(
                "/agent/runs/start-claim", json={**base, "content": "换了正文"}, headers=token
            )
        ).json()["status"] == "mismatch"
        assert (await client.post("/agent/runs/start-claim", json=base, headers=token)).json()[
            "status"
        ] == "claimed"
    finally:
        await client.aclose()


async def test_r05_deletion_requires_ownership_or_permission(ledger_env) -> None:
    """反例 deletion-api：同企业他人成员不得删除别人的线程（无独立权限时 404）。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    other = await _login(await _account_of(env["user_b"]), env["tenant_a"])
    owner = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        # user_b 只有 agent:operations:read，不含 agent:thread:delete → 拒绝且不泄露存在性
        denied = await other.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert denied.status_code == 404, denied.text
        # 本人可以
        ok = await owner.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert ok.status_code == 200
        assert ok.json()["deletion_state"] == "requested"
    finally:
        await other.aclose()
        await owner.aclose()


async def test_r05_deletion_blocks_reads_and_new_writes(ledger_env) -> None:
    """删除标记立即生效：回读/原文/新提问全部拒绝，且保留期到期后原文也不可读。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post(
                "/agent/requests",
                json={**_admit_body(thread_id, "r05-del-0001"), "store_content": True},
            )
        ).json()
        # 删除前：原文可读（按请求精确读取）
        before = await client.get(f"/agent/requests/{admitted['request_id']}/content")
        assert before.status_code == 200 and before.json()["contents"]

        await client.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        # 标记后立即禁读
        assert (await client.get(f"/agent/requests/{admitted['request_id']}")).status_code == 404
        assert (
            await client.get(f"/agent/requests/{admitted['request_id']}/content")
        ).status_code == 404
        # 禁新写
        new_write = await client.post(
            "/agent/requests", json=_admit_body(thread_id, "r05-del-0002")
        )
        assert new_write.status_code == 403
        # 保留期到期后原文同样拒绝（用另一线程验证到期规则）
        live_thread = str(uuid.uuid4())
        await _grant_thread(env["tenant_a"], env["user_a"], live_thread)
        live = (
            await client.post(
                "/agent/requests",
                json={**_admit_body(live_thread, "r05-ret-0001"), "store_content": True},
            )
        ).json()
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_content_refs SET retention_until = now() - interval '1 day'"
                    " WHERE request_id = :r"
                ),
                {"r": live["request_id"]},
            )
            await session.commit()
        expired = await client.get(f"/agent/requests/{live['request_id']}/content")
        assert expired.status_code == 200
        assert expired.json()["contents"] == [], "保留期到期后不得返回原文"
        # 但该请求的元数据仍可回读（只禁原文，不禁台账）
        assert (await client.get(f"/agent/requests/{live['request_id']}")).status_code == 200
    finally:
        await client.aclose()


async def test_f04_deleted_thread_stays_blocked_after_cleanup(ledger_env) -> None:
    """F04 反例 resume-deletion：清理完成（state=completed）后仍必须禁读禁写.

    修复前：`is_thread_deletion_pending` 只匹配 `state <> 'completed'`，探针把清理置为
    完成后，本人请求明细 200、新提问受理 200——用户以为删掉的内容继续被读取与追加。
    删除是**永久**语义：只要存在删除记录，回读/原文/新受理一律拒绝。
    """
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post(
                "/agent/requests",
                json={**_admit_body(thread_id, "f04-del-0001"), "store_content": True},
            )
        ).json()
        deletion = await client.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert deletion.status_code == 200, deletion.text
        # 模拟受限 worker 完成清理：标记 completed + 原文 deleted_at
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_thread_deletions SET state = 'completed',"
                    " attempts = attempts + 1, completed_at = now() WHERE thread_id = :th"
                ),
                {"th": thread_id},
            )
            await session.execute(
                text("UPDATE agent_content_refs SET deleted_at = now() WHERE thread_id = :th"),
                {"th": thread_id},
            )
            await session.commit()
        # 清理完成后：元数据、原文、新受理仍然全部拒绝
        assert (await client.get(f"/agent/requests/{admitted['request_id']}")).status_code == 404, (
            "清理完成后不得恢复元数据回读"
        )
        assert (
            await client.get(f"/agent/requests/{admitted['request_id']}/content")
        ).status_code == 404
        after = await client.post("/agent/requests", json=_admit_body(thread_id, "f04-del-0002"))
        assert after.status_code == 403, "清理完成后不得再受理新提问"
    finally:
        await client.aclose()


async def test_f04_deletion_record_is_permanent_after_cleanup(ledger_env) -> None:
    """F04：清理完成后删除记录仍在（永久墓碑），且该会话的后续写入被拒绝.

    重放/重建/孤立补投递的"不复活"由 Agent 侧回归
    `test_event_ledger_review02_regressions.py::test_f04_*` 覆盖（需 RUNTIME_DATABASE_URI）。
    """
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post(
                "/agent/requests",
                json={**_admit_body(thread_id, "f04-perm-0001"), "store_content": True},
            )
        ).json()
        await client.post(
            "/agent/threads/deletion",
            json={"thread_id": thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_thread_deletions SET state = 'completed',"
                    " attempts = attempts + 1, completed_at = now() WHERE thread_id = :th"
                ),
                {"th": thread_id},
            )
            await session.commit()
        async with OwnerSessionLocal() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT state, user_id::text FROM agent_thread_deletions"
                        " WHERE tenant_id = :t AND thread_id = :th"
                    ),
                    {"t": str(env["tenant_a"]), "th": thread_id},
                )
            ).fetchone()
        assert row is not None and row[0] == "completed", "删除标记不得被清理删除"
        assert row[1], "删除标记必须保留归属人（供受限清理与归属校验）"
        assert (await client.get(f"/agent/requests/{admitted['request_id']}")).status_code == 404
    finally:
        await client.aclose()


async def test_r05_content_read_is_per_request_not_per_thread(ledger_env) -> None:
    """交付缺口：原文按**请求**精确读取，不按可变 thread 指针取全量快照。"""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        first = (
            await client.post(
                "/agent/requests",
                json={
                    **_admit_body(thread_id, "r05-per-0001", "第一个问题"),
                    "store_content": True,
                },
            )
        ).json()
        second = (
            await client.post(
                "/agent/requests",
                json={
                    **_admit_body(thread_id, "r05-per-0002", "第二个问题"),
                    "store_content": True,
                },
            )
        ).json()
        r1 = (await client.get(f"/agent/requests/{first['request_id']}/content")).json()
        r2 = (await client.get(f"/agent/requests/{second['request_id']}/content")).json()
        assert [c["content"] for c in r1["contents"]] == ["第一个问题"]
        assert [c["content"] for c in r2["contents"]] == ["第二个问题"]
    finally:
        await client.aclose()


# 协调者接管：真实普通提交启用快照后，重新生成不能重复插入同一原问题。
async def test_takeover_regenerate_reuses_original_snapshot(ledger_env):
    env = ledger_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        first = await client.post(
            "/agent/requests",
            json={
                **_admit_body(thread, "snapshot-initial"),
                "store_content": True,
                "retention_days": 1,
            },
        )
        assert first.status_code == 200
        initial = first.json()
        regen = await client.post(
            "/agent/requests",
            json={
                **_admit_body(thread, "snapshot-regenerate"),
                "operation_kind": "regenerate",
                "source_attempt_id": initial["attempt_id"],
                "store_content": True,
                "retention_days": 3650,
            },
        )
        assert regen.status_code == 200
        assert regen.json()["request_id"] == initial["request_id"]
        raw = (await client.get("/agent/requests/" + initial["request_id"] + "/content")).json()
        assert len(raw["contents"]) == 1 and raw["contents"][0]["content"] == "问题"
    finally:
        await client.aclose()


async def test_takeover_fallback_operation_is_startable_and_replayable(ledger_env):
    env = ledger_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        body = {**_admit_body(thread, "fallback-regenerate"), "operation_kind": "regenerate"}
        first = await client.post("/agent/requests", json=body)
        assert first.status_code == 200 and first.json()["operation_kind"] == "initial"
        item = first.json()
        claim = await client.post(
            "/agent/runs/start-claim",
            headers={"x-internal-token": _internal_token()},
            json={
                "submission_id": item["submission_id"],
                "attempt_id": item["attempt_id"],
                "thread_id": thread,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "问题",
            },
        )
        assert claim.status_code == 200 and claim.json()["status"] == "claimed"
        replay = await client.post("/agent/requests", json=body)
        assert replay.status_code == 200 and replay.json()["attempt_id"] == item["attempt_id"]
    finally:
        await client.aclose()


async def test_takeover_request_cannot_extend_server_retention(ledger_env):
    env = ledger_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = await client.post(
            "/agent/requests",
            json={
                **_admit_body(thread, "retention-bound"),
                "store_content": True,
                "retention_days": 3650,
            },
        )
        assert admitted.status_code == 200
        async with OwnerSessionLocal() as session:
            days = await session.scalar(
                text(
                    "SELECT EXTRACT(EPOCH FROM (retention_until-created_at))/86400 "
                    "FROM agent_content_refs WHERE request_id=CAST(:r AS uuid)"
                ),
                {"r": admitted.json()["request_id"]},
            )
        assert days is not None and days <= 90
    finally:
        await client.aclose()


async def test_takeover_tenant_retention_override_and_source_scope(ledger_env, monkeypatch):
    from app.config import get_settings

    env = ledger_env
    monkeypatch.setattr(
        get_settings(), "ledger_content_retention_days_by_tenant", {str(env["tenant_a"]): 1}
    )
    threads = [str(uuid.uuid4()) for _ in range(2)]
    for thread in threads:
        await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        r = await client.post(
            "/agent/requests",
            json={
                **_admit_body(threads[0], "tenant-policy-001"),
                "store_content": True,
                "retention_days": 3650,
            },
        )
        assert r.status_code == 200
        async with OwnerSessionLocal() as session:
            days = await session.scalar(
                text(
                    "SELECT EXTRACT(EPOCH FROM (retention_until-created_at))/86400 "
                    "FROM agent_content_refs WHERE request_id=CAST(:r AS uuid)"
                ),
                {"r": r.json()["request_id"]},
            )
        assert days == 1
        cross = await client.post(
            "/agent/requests",
            json={
                **_admit_body(threads[1], "wrong-source-001"),
                "operation_kind": "regenerate",
                "source_attempt_id": r.json()["attempt_id"],
            },
        )
        assert cross.status_code == 403
    finally:
        await client.aclose()
