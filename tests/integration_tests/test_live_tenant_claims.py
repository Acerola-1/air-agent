"""R01/R02 真实联调回归（轮次01 修复；对本地运行栈的实测证据）.

前提（缺失即 skip，不算通过）：
- `RUN_TENANT_LIVE_E2E=1` 显式开启；
- admin API :8100、聊天代理 :3000、原生 langgraph-api :2024 已运行当前代码；
- `REVIEW_FIXTURE_PASSWORD` 提供本地验收账号（acc_alice/acc_bob）口令；
- `INTERNAL_API_TOKEN` 与 `DATABASE_URI`（owner）在受保护环境提供。

只创建/删除本文件生成的合成空白线程，不读取历史用户对话，不重置业务卷。
对应评审 live_probe 的 R01（未知历史归属认领）与 R02（旧标签页写入）复现输入。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.anyio

LIVE_ENABLED = os.environ.get("RUN_TENANT_LIVE_E2E") == "1"
ADMIN = os.environ.get("LIVE_ADMIN_URL", "http://127.0.0.1:8100")
PROXY = os.environ.get("LIVE_CHAT_PROXY_URL", "http://127.0.0.1:3000")
NATIVE = os.environ.get("LIVE_NATIVE_URL", "http://127.0.0.1:2024")
PASSWORD = os.environ.get("REVIEW_FIXTURE_PASSWORD", "")
INTERNAL = os.environ.get("INTERNAL_API_TOKEN", "")
OWNER_URI = os.environ.get("TENANT_E2E_OWNER_URI") or os.environ.get("DATABASE_URI", "")


def _require_live() -> None:
    if not LIVE_ENABLED:
        pytest.skip("需要 RUN_TENANT_LIVE_E2E=1 显式开启真实联调")
    if not (PASSWORD and INTERNAL and OWNER_URI):
        pytest.skip(
            "缺少受保护环境（REVIEW_FIXTURE_PASSWORD/INTERNAL_API_TOKEN/owner URI）"
        )


async def _login(account: str) -> tuple[httpx.AsyncClient, dict]:
    client = httpx.AsyncClient(base_url=ADMIN, timeout=20)
    res = await client.post(
        "/auth/login", json={"account": account, "password": PASSWORD}
    )
    assert res.status_code == 200, f"本地验收账号不可用: {res.status_code}"
    ctx = res.json()
    return client, ctx


def _pick_tenant(ctx: dict, prefer_suffix: str) -> str | None:
    for item in ctx["tenants"]:
        if item["tenant"]["name"].endswith(prefer_suffix):
            return item["tenant"]["id"]
    return None


async def _switch(client: httpx.AsyncClient, ctx: dict, tenant_id: str) -> dict:
    if ctx["current_tenant"] and ctx["current_tenant"]["id"] == tenant_id:
        return ctx
    res = await client.post(
        "/auth/switch-tenant",
        json={"tenant_id": tenant_id, "expected_ctx_version": ctx["context_version"]},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _ticket(client: httpx.AsyncClient, ctx: dict) -> str:
    res = await client.post(
        "/auth/runtime-ticket",
        headers={"x-internal-token": INTERNAL},
        json={"target_tenant_id": ctx["current_tenant"]["id"]},
    )
    assert res.status_code == 200, res.text
    return res.json()["ticket"]


def _native_headers(ticket: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ticket}", "content-type": "application/json"}


async def _admit(
    client: httpx.AsyncClient, ctx: dict, tenant_id: str, thread_id: str, graph: str
) -> dict:
    """经可信台账受理，取得运行准入预留（change add-agent-event-ledger 契约）。

    原生运行入口现在要求台账预留：先受理拿 request/attempt，再带预留创建运行。
    """
    res = await client.post(
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
            "content": "ping",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _confirm_claim(ticket: str, thread_id: str) -> bool:
    """F01 契约：确认端点只接受 thread_id；作用域/会话由已验票据推导。"""
    async with httpx.AsyncClient(timeout=15) as probe:
        res = await probe.post(
            f"{NATIVE}/internal/threads/claim-confirmation",
            headers={**_native_headers(ticket), "x-internal-token": INTERNAL},
            json={"thread_id": thread_id},
        )
        return res.status_code == 200 and bool(res.json().get("confirmed"))


async def _confirm_claim_scoped_body(
    ticket: str, thread_id: str, tenant_id: str, user_id: str
) -> int:
    """旧形态（请求体自报身份）必须被端点拒绝——F01 契约回归。"""
    async with httpx.AsyncClient(timeout=15) as probe:
        res = await probe.post(
            f"{NATIVE}/internal/threads/claim-confirmation",
            headers={**_native_headers(ticket), "x-internal-token": INTERNAL},
            json={"thread_id": thread_id, "tenant_id": tenant_id, "user_id": user_id},
        )
        return res.status_code


def _drop_ownership(thread_id: str) -> None:
    import psycopg

    with psycopg.connect(OWNER_URI, autocommit=True) as conn:
        conn.execute("DELETE FROM thread_ownership WHERE thread_id = %s", (thread_id,))


def _restore_ownership(thread_id: str, tenant_id: str, user_id: str) -> None:
    import psycopg

    with psycopg.connect(OWNER_URI, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO thread_ownership (thread_id, tenant_id, user_id, source)"
            " VALUES (%s, %s, %s, 'native')"
            " ON CONFLICT (thread_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id,"
            " user_id = EXCLUDED.user_id, source = 'native'",
            (thread_id, tenant_id, user_id),
        )


# ------------------------------------------------------------------ R01 认领协议


async def test_live_unknown_history_claim_denied() -> None:
    """评审 R01 的端到端复现：未知历史归属不得被同 ID 创建吸收。"""
    _require_live()
    a, ca = await _login("acc_alice")
    b, cb = await _login("acc_bob")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    tb = _pick_tenant(cb, "B") or cb["tenants"][-1]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    cb = await _switch(b, cb, tb)
    ha = _native_headers(await _ticket(a, ca))
    hb = _native_headers(await _ticket(b, cb))
    tid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            # B 正常创建 + 确认 + 可读
            res = await lg.post(
                "/threads",
                headers=hb,
                json={
                    "thread_id": tid,
                    "metadata": {
                        "tenant_id": cb["current_tenant"]["id"],
                        "user_id": cb["user"]["id"],
                    },
                },
            )
            assert res.status_code in (200, 201), res.text
            assert await _confirm_claim(
                hb["Authorization"].removeprefix("Bearer "), tid
            )
            assert (await lg.get(f"/threads/{tid}", headers=hb)).status_code == 200

            # 模拟未知历史：只移除本测试刚创建的合成线程 ownership（评审 README 同法）
            _drop_ownership(tid)

            read_before = await lg.get(f"/threads/{tid}", headers=ha)
            assert read_before.status_code == 404  # 未知归属不可访问（修复前已如此）

            do_nothing = await lg.post(
                "/threads",
                headers=ha,
                json={
                    "thread_id": tid,
                    "if_exists": "do_nothing",
                    "metadata": {
                        "tenant_id": ca["current_tenant"]["id"],
                        "user_id": ca["user"]["id"],
                    },
                },
            )
            assert do_nothing.status_code == 403, do_nothing.text  # 修复前：200 吸收

            strict = await lg.post(
                "/threads",
                headers=ha,
                json={
                    "thread_id": tid,
                    "metadata": {
                        "tenant_id": ca["current_tenant"]["id"],
                        "user_id": ca["user"]["id"],
                    },
                },
            )
            assert strict.status_code in (403, 409), strict.text

            read_after = await lg.get(f"/threads/{tid}", headers=ha)
            assert read_after.status_code == 404  # 修复前：200 暴露 B 的私有标记
            assert not (
                read_after.status_code == 200
                and read_after.json().get("metadata", {}).get("tenant_id")
                == ca["current_tenant"]["id"]
            )
    finally:
        _restore_ownership(tid, cb["current_tenant"]["id"], cb["user"]["id"])
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            hb2 = _native_headers(await _ticket(b, cb))
            await lg.delete(f"/threads/{tid}", headers=hb2)
        for c in (a, b):
            await c.post("/auth/logout")
            await c.aclose()


async def test_live_pending_until_confirmed_and_confirmed_flow() -> None:
    """新线程在代理确认前不可读（pending），确认后本人可读、可删。"""
    _require_live()
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    ticket = await _ticket(a, ca)
    ha = _native_headers(ticket)
    tid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            res = await lg.post(
                "/threads",
                headers=ha,
                json={
                    "thread_id": tid,
                    "metadata": {
                        "tenant_id": ca["current_tenant"]["id"],
                        "user_id": ca["user"]["id"],
                    },
                },
            )
            assert res.status_code in (200, 201), res.text
            # 未确认：创建者本人也读不到（pending 不可见）
            assert (await lg.get(f"/threads/{tid}", headers=ha)).status_code == 404
            assert await _confirm_claim(ticket, tid)
            read = await lg.get(f"/threads/{tid}", headers=ha)
            assert read.status_code == 200
            assert read.json()["metadata"]["user_id"] == ca["user"]["id"]
            # 重复确认幂等、无归属线程确认返回 false
            assert await _confirm_claim(ticket, tid)
            assert not await _confirm_claim(ticket, str(uuid.uuid4()))
            # 自报作用域旧形态请求体被拒绝（400）
            bad = await _confirm_claim_scoped_body(
                ticket, tid, ca["current_tenant"]["id"], ca["user"]["id"]
            )
            assert bad == 400
            # 内部端点无凭据必须 401
            async with httpx.AsyncClient(timeout=10) as anon:
                bad = await anon.post(
                    f"{NATIVE}/internal/threads/claim-confirmation",
                    json={
                        "thread_id": tid,
                        "tenant_id": ca["current_tenant"]["id"],
                        "user_id": ca["user"]["id"],
                    },
                )
                assert bad.status_code == 401
            assert (await lg.delete(f"/threads/{tid}", headers=ha)).status_code in (
                200,
                204,
            )
    finally:
        await a.post("/auth/logout")
        await a.aclose()


async def test_live_same_id_concurrent_create_single_owner() -> None:
    """同一新 ID 并发严格创建：恰好一个成功；另一个被归属/平台裁决拒绝。"""
    _require_live()
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    ticket = await _ticket(a, ca)
    ha = _native_headers(ticket)
    tid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            req = {
                "thread_id": tid,
                "metadata": {
                    "tenant_id": ca["current_tenant"]["id"],
                    "user_id": ca["user"]["id"],
                },
            }
            r1, r2 = await asyncio.gather(
                lg.post("/threads", headers=ha, json=req),
                lg.post("/threads", headers=ha, json=req),
            )
            codes = sorted([r1.status_code, r2.status_code])
            ok = [c for c in codes if c in (200, 201)]
            rejected = [c for c in codes if c in (403, 409)]
            assert len(ok) == 1 and len(rejected) == 1, codes
            assert await _confirm_claim(ticket, tid)
            assert (await lg.get(f"/threads/{tid}", headers=ha)).status_code == 200
            await lg.delete(f"/threads/{tid}", headers=ha)
    finally:
        await a.post("/auth/logout")
        await a.aclose()


# ------------------------------------------------------------------ R02 聊天代理旧标签页


async def test_live_chat_proxy_stale_tab_write_denied() -> None:
    """旧页面（持旧企业头）经代理创建线程：409 context_changed；无头 403；不落入新企业。"""
    _require_live()
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    tb = _pick_tenant(ca, "B") or (
        ca["tenants"][-1]["tenant"]["id"] if len(ca["tenants"]) > 1 else None
    )
    assert tb and tb != ta, "验收账号需要 e2e-a/e2e-b 两家企业"
    ca = await _switch(a, ca, ta)
    tid = str(uuid.uuid4())
    created_ok = False
    minted = ""
    try:
        body = {
            "thread_id": tid,
            "metadata": {"user_id": ca["user"]["id"], "tenant_id": ta},
        }
        headers = {
            "x-tenant-id": ta,
            "x-tenant-context-version": str(ca["context_version"]),
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=PROXY, cookies=a.cookies, timeout=30
        ) as proxy:
            good = await proxy.post("/api/threads", headers=headers, json=body)
            assert good.status_code in (200, 201), good.text  # 代理内部确认 → 可读
            created_ok = True
            # F01：代理铸造线程 ID——客户端自选 ID 不被采信、不被创建
            minted = str(good.json().get("thread_id") or "")
            assert minted and minted != tid, "BFF 必须铸造 thread_id"
            orphan_read = await proxy.get(f"/api/threads/{tid}", headers=headers)
            assert orphan_read.status_code == 404, "自选 ID 不应成为可读线程"
            read = await proxy.get(f"/api/threads/{minted}", headers=headers)
            assert read.status_code == 200
            assert read.json()["metadata"]["tenant_id"] == ta

            # 会话切换到 B；旧页面仍持 A 头
            await _switch(a, ca, tb)
            stale = await proxy.post(
                "/api/threads",
                headers={**headers},  # 仍为 A 上下文（含旧版本）
                json={
                    "thread_id": str(uuid.uuid4()),
                    "metadata": {"user_id": ca["user"]["id"], "tenant_id": ta},
                },
            )
            assert stale.status_code == 409
            assert stale.json().get("code") == "context_changed"

            nohdr = await proxy.post(
                "/api/threads",
                headers={"content-type": "application/json"},
                json={"thread_id": str(uuid.uuid4()), "metadata": {}},
            )
            assert nohdr.status_code == 403
            assert nohdr.json().get("code") == "context_missing"
    finally:
        if created_ok:
            # 切回 A 上下文后经代理删除合成线程（跨企业删除会被归属拒绝）
            fresh = (await a.get("/auth/me")).json()
            if (fresh.get("current_tenant") or {}).get("id") != ta:
                fresh = await _switch(a, fresh, ta)
            del_headers = {
                "x-tenant-id": ta,
                "x-tenant-context-version": str(fresh["context_version"]),
            }
            async with httpx.AsyncClient(
                base_url=PROXY, cookies=a.cookies, timeout=30
            ) as proxy:
                await proxy.delete(f"/api/threads/{minted or tid}", headers=del_headers)
        await a.post("/auth/logout")
        await a.aclose()


async def test_live_bff_never_confirms_unowned_ids() -> None:
    """F01 全阶段：他人确认线程/未知历史线程都无法经 BFF"严格创建→自动确认"吸收。

    评审复现路径（live_claim_probe）在修复后必须呈现：代理响应 200 但 thread_id
    为铸造新 ID、不含他人 metadata 标记；对目标旧 ID 的读取（代理与原生）均 404。
    """
    _require_live()
    a, ca = await _login("acc_alice")
    b, cb = await _login("acc_bob")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    tb = _pick_tenant(cb, "B") or cb["tenants"][-1]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    cb = await _switch(b, cb, tb)
    tid = str(uuid.uuid4())
    confirmed = False
    try:
        ha = _native_headers(await _ticket(a, ca))
        hb = _native_headers(await _ticket(b, cb))
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            r = await lg.post(
                "/threads",
                headers=hb,
                json={
                    "thread_id": tid,
                    "metadata": {
                        "tenant_id": cb["current_tenant"]["id"],
                        "user_id": cb["user"]["id"],
                        "review_marker": "synthetic-private-b",
                    },
                },
            )
            assert r.status_code in (200, 201)
            confirmed = True
            assert await _confirm_claim(
                hb["Authorization"].removeprefix("Bearer "), tid
            )
            # 场景 A：未知历史（删归属行）经 BFF 严格创建
            _drop_ownership(tid)
            headers = {
                "x-tenant-id": ta,
                "x-tenant-context-version": str(ca["context_version"]),
                "content-type": "application/json",
            }
            async with httpx.AsyncClient(
                base_url=PROXY, cookies=a.cookies, timeout=30
            ) as proxy:
                atk = await proxy.post(
                    "/api/threads",
                    headers=headers,
                    json={
                        "thread_id": tid,
                        "if_exists": "raise",
                        "metadata": {"graph_id": "basic-qa"},
                    },
                )
                assert atk.status_code in (200, 201)
                body = atk.json()
                minted = str(body.get("thread_id") or "")
                result = {
                    "bff_returns_minted_id": bool(minted) and minted != tid,
                    "bff_exposes_marker": body.get("metadata", {}).get("review_marker")
                    == "synthetic-private-b",
                }
                stale = await proxy.get(f"/api/threads/{tid}", headers=headers)
                result["proxy_read_orphan_status"] = stale.status_code
                native_read = await lg.get(f"/threads/{tid}", headers=ha)
                result["native_read_orphan_status"] = native_read.status_code
                assert result["bff_returns_minted_id"] is True
                assert result["bff_exposes_marker"] is False
                assert stale.status_code == 404
                assert native_read.status_code == 404
                # 清理代理铸造出的合法新线程
                await proxy.delete(f"/api/threads/{minted}", headers=headers)
                # 场景 B：他人已知归属线程经 BFF 同样不可吸收
                headers_b = dict(headers)
                atk2 = await proxy.post(
                    "/api/threads",
                    headers=headers_b,
                    json={"thread_id": tid, "metadata": {"graph_id": "basic-qa"}},
                )
                assert atk2.status_code in (200, 201)
                assert str(atk2.json().get("thread_id") or "") != tid
                await proxy.delete(
                    f"/api/threads/{atk2.json()['thread_id']}", headers=headers_b
                )
    finally:
        _restore_ownership(tid, cb["current_tenant"]["id"], cb["user"]["id"])
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            if confirmed:
                await lg.delete(
                    f"/threads/{tid}",
                    headers=_native_headers(await _ticket(b, cb)),
                )
        for c in (a, b):
            await c.post("/auth/logout")
            await c.aclose()


async def test_live_claim_confirm_requires_same_session() -> None:
    """F01 会话绑定：另一登录会话（新用户）的同归属票据不能确认他人 pending 认领。"""
    _require_live()
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    a2, ca2 = await _login("acc_alice")
    if (ca2.get("current_tenant") or {}).get("id") != ta:
        ca2 = await _switch(a2, ca2, ta)
    t1 = await _ticket(a, ca)
    t2 = await _ticket(a2, ca2)
    tid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
            r = await lg.post(
                "/threads",
                headers=_native_headers(t1),
                json={
                    "thread_id": tid,
                    "metadata": {"tenant_id": ta, "user_id": ca["user"]["id"]},
                },
            )
            assert r.status_code in (200, 201)
            # 另一会话确认 → false（创建事实属于 t1 的那次操作）
            assert not await _confirm_claim(t2, tid)
            assert (
                await lg.get(f"/threads/{tid}", headers=_native_headers(t1))
            ).status_code == 404
            # 原会话确认 → true，随即可读
            assert await _confirm_claim(t1, tid)
            assert (
                await lg.get(f"/threads/{tid}", headers=_native_headers(t1))
            ).status_code == 200
            await lg.delete(f"/threads/{tid}", headers=_native_headers(t1))
    finally:
        for c in (a, a2):
            await c.post("/auth/logout")
            await c.aclose()


def _sign_mt_ticket_live(thread_id: str, tenant_id: str, user_id: str) -> str:
    """mt-v1 签发（与 admin security.py 字节兼容；真实 2024 实测用）。"""
    import base64
    import hashlib
    import hmac
    import json
    import time

    raw = os.environ.get("RUNTIME_TICKET_KEYS", "")
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        pairs = list(parsed.items())
    else:
        pairs = [(f"v{i + 1}", k) for i, k in enumerate(parsed)]
    kid, secret = pairs[0]
    now = int(time.time())
    payload = {
        "typ": "mt-v1",
        "thread": thread_id,
        "tenant": tenant_id,
        "user": user_id,
        "aud": os.environ.get(
            "RUNTIME_TICKET_MIGRATION_AUDIENCE", "langgraph-migration"
        ),
        "iat": now,
        "exp": now + 60,
    }
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(secret.encode(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


async def test_live_migration_ticket_native_api() -> None:
    """F03/F06 复验：真实镜像上 mt-v1 窄更新——提交后 200、越界 403、读取拒绝。"""
    _require_live()
    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    tid = str(uuid.uuid4())
    other = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            r = await lg.post(
                "/threads",
                headers=_native_headers(await _ticket(a, ca)),
                json={
                    "thread_id": tid,
                    "metadata": {"tenant_id": ta, "user_id": ca["user"]["id"]},
                },
            )
            assert r.status_code in (200, 201)
            await _confirm_claim(await _ticket(a, ca), tid)
            # 归属行改写为 migrated（模拟迁移脚本阶段 A 提交后的状态）
            import psycopg

            with psycopg.connect(OWNER_URI, autocommit=True) as c:
                c.execute(
                    "UPDATE thread_ownership SET source='migrated' WHERE thread_id=%s",
                    (tid,),
                )
            mt = _sign_mt_ticket_live(tid, ta, ca["user"]["id"])
            hm = {"Authorization": f"Bearer {mt}", "content-type": "application/json"}
            ok = await lg.patch(
                f"/threads/{tid}",
                headers=hm,
                json={"metadata": {"user_id": "spoof", "tenant_id": "other"}},
            )
            assert ok.status_code == 200, ok.text
            # 可信键被强制为票据作用域，不被请求体伪造
            assert ok.json()["metadata"]["user_id"] == ca["user"]["id"]
            # 票据绑定别的线程 → 403
            bound = await lg.patch(
                f"/threads/{other}",
                headers=hm,
                json={"metadata": {"user_id": ca["user"]["id"]}},
            )
            assert bound.status_code == 403
            # 迁移票据不能读取/检索
            read = await lg.get(f"/threads/{tid}", headers=hm)
            assert read.status_code == 403
            search = await lg.post(
                "/threads/search",
                headers=hm,
                json={"metadata": {"graph_id": "basic-qa"}},
            )
            assert search.status_code == 403
            # 恢复原主归属并清理
            with psycopg.connect(OWNER_URI, autocommit=True) as c:
                c.execute(
                    "UPDATE thread_ownership SET source='native' WHERE thread_id=%s",
                    (tid,),
                )
            assert (
                await lg.delete(
                    f"/threads/{tid}", headers=_native_headers(await _ticket(a, ca))
                )
            ).status_code in (200, 204)
    finally:
        import psycopg

        with psycopg.connect(OWNER_URI, autocommit=True) as c:
            c.execute("DELETE FROM thread_ownership WHERE thread_id = %s", (tid,))
        await a.post("/auth/logout")
        await a.aclose()


PUBLIC_GRAPHS = [
    "basic-qa",
    "data-analysis",
    "intelligent-tracing",
]


async def test_live_public_graphs_run_acceptance() -> None:
    """公开图真实服务联调（F02/5.4 部分闭环）：每图运行在可信身份下被受理，
    跨企业线程拒绝，并验证撤权后取消可用。**不**等同于完整长运行验收。"""
    _require_live()
    a, ca = await _login("acc_alice")
    b, cb = await _login("acc_bob")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    tb = _pick_tenant(cb, "B") or cb["tenants"][-1]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    cb = await _switch(b, cb, tb)
    ha = _native_headers(await _ticket(a, ca))
    hb = _native_headers(await _ticket(b, cb))
    tid = str(uuid.uuid4())
    runs: list[tuple[str, str]] = []
    try:
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            r = await lg.post(
                "/threads",
                headers=ha,
                json={
                    "thread_id": tid,
                    "metadata": {"tenant_id": ta, "user_id": ca["user"]["id"]},
                },
            )
            assert r.status_code in (200, 201)
            assert await _confirm_claim(
                ha["Authorization"].removeprefix("Bearer "), tid
            )
            # 原生入口准入：**无台账预留必须被拒绝**（不能出现未统计的获准运行）
            no_res = await lg.post(
                f"/threads/{tid}/runs",
                headers=ha,
                json={
                    "assistant_id": "basic-qa",
                    "input": {
                        "messages": [{"role": "user", "content": "no reservation"}]
                    },
                },
            )
            assert no_res.status_code == 403, (
                f"缺少台账预留的原生运行必须被拒绝，实际 {no_res.status_code}"
            )
            # 伪造（随机）预留同样拒绝
            forged = await lg.post(
                f"/threads/{tid}/runs",
                headers=ha,
                json={
                    "assistant_id": "basic-qa",
                    "input": {"messages": [{"role": "user", "content": "forged"}]},
                    "metadata": {
                        "ledger_attempt_id": str(uuid.uuid4()),
                        "ledger_submission_id": str(uuid.uuid4()),
                        "ledger_graph_id": "basic-qa",
                    },
                },
            )
            assert forged.status_code in (403, 409), (
                f"伪造台账预留必须被拒绝，实际 {forged.status_code}"
            )

            accepted = {}
            for graph in PUBLIC_GRAPHS:
                admitted = await _admit(a, ca, ta, tid, graph)
                rr = await lg.post(
                    f"/threads/{tid}/runs",
                    headers=ha,
                    json={
                        "assistant_id": graph,
                        "input": {"messages": [{"role": "user", "content": "ping"}]},
                        "config": {
                            "configurable": {
                                "ledger_request_id": admitted["request_id"],
                                "ledger_attempt_id": admitted["attempt_id"],
                                # R01：启动权按**提交**裁决，原生入口同时校验 attempt+submission
                                "ledger_submission_id": admitted["submission_id"],
                                "ledger_operation_kind": "initial",
                                "ledger_graph_id": graph,
                            }
                        },
                    },
                )
                accepted[graph] = rr.status_code
                if rr.status_code in (200, 201):
                    runs.append((tid, rr.json()["run_id"]))
            assert all(code in (200, 201) for code in accepted.values()), (
                f"公开图带台账预留的运行受理失败: {accepted}"
            )
            # 跨企业：Bob 在 Alice 线程上创建 run 必须被拒
            cross = await lg.post(
                f"/threads/{tid}/runs",
                headers=hb,
                json={"assistant_id": "basic-qa", "input": {"messages": []}},
            )
            assert cross.status_code in (403, 404)
            # 撤下所有本次运行（等待各自终态；取消被接受）
            for thread_id, run_id in runs:
                cancel = await lg.post(
                    f"/threads/{thread_id}/runs/{run_id}/cancel?wait=false", headers=ha
                )
                assert cancel.status_code in (200, 202, 404, 409)
            await lg.delete(f"/threads/{tid}", headers=ha)
    finally:
        for c in (a, b):
            await c.post("/auth/logout")
            await c.aclose()


async def test_live_archive_scope_replay_after_logout_is_denied() -> None:
    """已签归档 scope 重放不能绕过会话撤销；只操作本次合成资料。"""
    _require_live()
    import psycopg

    from common.artifact_scope import sign_scope

    a, ca = await _login("acc_alice")
    ta = _pick_tenant(ca, "A") or ca["tenants"][0]["tenant"]["id"]
    ca = await _switch(a, ca, ta)
    tid, aid = str(uuid.uuid4()), "review_" + uuid.uuid4().hex
    user = ca["user"]["id"]
    created = False
    try:
        ticket = await _ticket(a, ca)
        async with httpx.AsyncClient(base_url=NATIVE, timeout=25) as lg:
            r = await lg.post(
                "/threads",
                headers=_native_headers(ticket),
                json={
                    "thread_id": tid,
                    "metadata": {"tenant_id": ta, "user_id": user},
                },
            )
            assert r.status_code in (200, 201)
            created = True
            assert await _confirm_claim(ticket, tid)
            with psycopg.connect(OWNER_URI) as conn:
                conn.execute(
                    "INSERT INTO tool_artifact_storage(artifact_id,tool_call_id,tool_name,char_length,raw_payload,created_at,tenant_id,user_id,thread_id,run_id) VALUES (%s,'synthetic','review',9,'synthetic','2026-10-12',%s,%s,%s,'review')",
                    (aid, ta, user, tid),
                )
            headers = {
                **_native_headers(ticket),
                "x-artifact-scope": sign_scope(ta, user, tid),
            }
            good = await lg.get(f"/archives/{tid}/{aid}", headers=headers)
            assert good.status_code == 200, good.text
            assert good.json()["content"] == "synthetic"
            await a.post("/auth/logout")
            denied = await lg.get(f"/archives/{tid}/{aid}", headers=headers)
            assert denied.status_code in (401, 403), "登出后不得用旧归档scope继续回读"
    finally:
        if created:
            cleanup, current = await _login("acc_alice")
            try:
                current = await _switch(cleanup, current, ta)
                async with httpx.AsyncClient(base_url=NATIVE, timeout=20) as lg:
                    await lg.delete(
                        f"/threads/{tid}",
                        headers=_native_headers(await _ticket(cleanup, current)),
                    )
            finally:
                await cleanup.post("/auth/logout")
                await cleanup.aclose()
        with psycopg.connect(OWNER_URI, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM tool_artifact_storage WHERE artifact_id=%s", (aid,)
            )
        await a.post("/auth/logout")
        await a.aclose()
