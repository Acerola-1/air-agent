"""Agent 侧台账存储/worker/校准测试（需要专用 PG；缺失即 skip.

    LEDGER_TEST_TENANT_ID=... RUNTIME_DATABASE_URI=... LEDGER_WORKER_DATABASE_URI=... \
      PYTHONPATH=src .venv/bin/python -m pytest tests/unit_tests/test_event_ledger_store.py -q

专用库要求：迁移到 0007、已 bootstrap 运行角色与受限调度角色。
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import psycopg
import pytest

PG_READY = bool(os.environ.get("RUNTIME_DATABASE_URI")) and bool(
    os.environ.get("LEDGER_TEST_TENANT_ID")
)

pytestmark = pytest.mark.skipif(not PG_READY, reason="需要专用台账 PG 环境")

TENANT = os.environ.get("LEDGER_TEST_TENANT_ID", "")
USER = os.environ.get("LEDGER_TEST_USER_ID", TENANT)


def _scope():
    from common.event_ledger.store import LedgerScope

    return LedgerScope(tenant_id=TENANT, user_id=USER, membership_id=USER)


async def _seed_request() -> tuple[str, str]:
    """建一个 request+attempt（走 owner 连接，绕过 RLS），返回 (request_id, attempt_id)."""

    owner_uri = os.environ["LEDGER_TEST_OWNER_URI"]
    request_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(owner_uri) as conn:
        await conn.execute(
            "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id,"
            " graph_id, operation_kind, admitted_at, created_at)"
            " VALUES (%s,%s,%s,%s,%s,'basic-qa','initial',now(),now())",
            (request_id, TENANT, USER, USER, "t-" + request_id[:8]),
        )
        await conn.execute(
            "INSERT INTO agent_run_attempts (id, tenant_id, user_id, membership_id, request_id,"
            " attempt_index, graph_id, state, state_revision, admitted_at, last_progress_at,"
            " updated_at) VALUES (%s,%s,%s,%s,%s,1,'basic-qa','admitting',1,now(),now(),now())",
            (attempt_id, TENANT, USER, USER, request_id),
        )
        await conn.commit()
    return request_id, attempt_id


async def _bind_platform_run(attempt_id: str, run_id: str | None = None) -> None:
    """把尝试置为 running 且已绑平台 run，使其成为校准候选."""

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET state='running', evidence_source='runtime_callback',"
            " platform_run_id=%s, last_progress_at=now() - interval '10 minutes' WHERE id=%s",
            (run_id, attempt_id),
        )
        await conn.commit()


# ------------------------------------------------------------------ 事件写入


@pytest.mark.anyio
async def test_write_event_dedup_and_conflict() -> None:
    from common.event_ledger.store import write_event

    scope = _scope()
    event_id = str(uuid.uuid4())
    payload = {"provider": "langfuse", "trace_id": "t-1", "primary": True}
    first = await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload=payload,
        event_id=event_id,
    )
    assert first["written"] is True
    # 同 ID 同载荷 → 复用（幂等）
    dupe = await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload=payload,
        event_id=event_id,
    )
    assert dupe["written"] is False and dupe["reason"] == "duplicate"
    # 同 ID 不同载荷 → 冲突，不覆盖
    conflict = await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "t-2", "primary": False},
        event_id=event_id,
    )
    assert conflict["written"] is False and conflict["reason"] == "payload_conflict"


@pytest.mark.anyio
async def test_write_event_rejects_reserved_and_forbidden() -> None:
    from common.event_ledger.store import write_event

    scope = _scope()
    reserved = await write_event(
        scope,
        event_type="runtime.model_usage",
        source="runtime",
        payload={"attempt_id": str(uuid.uuid4()), "model": "m"},
    )
    assert reserved["written"] is False and reserved["reason"] == "type_not_enabled"
    forged = await write_event(
        scope,
        event_type="run.state_observed",
        source="browser",
        payload={
            "state": "succeeded",
            "evidence_source": "browser_observed",
            "state_revision": 1,
        },
    )
    assert forged["written"] is False and forged["reason"] == "source_not_allowed"


@pytest.mark.anyio
async def test_write_event_creates_outbox_in_same_tx() -> None:

    from common.event_ledger.store import write_event

    scope = _scope()
    event_id = str(uuid.uuid4())
    result = await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "t-tx", "primary": False},
        event_id=event_id,
    )
    assert result["written"]
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM agent_outbox WHERE event_id = %s", (event_id,)
        )
        assert (await cur.fetchone())[0] >= 1


# ------------------------------------------------------------ 状态转换（秩）


@pytest.mark.anyio
async def test_observe_state_terminal_not_regressed() -> None:
    from common.event_ledger.store import observe_state

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    ok = await observe_state(
        scope,
        attempt_id=attempt_id,
        state="succeeded",
        evidence_source="platform_read",
        platform_status="success",
    )
    assert ok["applied"] is True and ok["state"] == "succeeded"
    late = await observe_state(
        scope,
        attempt_id=attempt_id,
        state="running",
        evidence_source="browser_observed",
    )
    assert late["applied"] is False
    assert late["state"] == "succeeded"
    assert late["note"] == "lower_rank_after_terminal"


@pytest.mark.anyio
async def test_observe_state_same_rank_conflict_kept() -> None:
    from common.event_ledger.store import observe_state

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    await observe_state(
        scope, attempt_id=attempt_id, state="succeeded", evidence_source="platform_read"
    )
    conflict = await observe_state(
        scope, attempt_id=attempt_id, state="failed", evidence_source="platform_read"
    )
    # 同秩终态冲突：保留原事实，不静默覆盖
    assert conflict["note"] == "terminal_conflict_kept"
    assert conflict["state"] == "succeeded"


@pytest.mark.anyio
async def test_resume_keeps_attempt_and_adds_mapping() -> None:
    from common.event_ledger.store import record_resume

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    first = await record_resume(
        scope, attempt_id=attempt_id, platform_run_id=f"run-r-{uuid.uuid4().hex[:8]}"
    )
    assert first["recorded"] and first["resume_index"] == 1
    second = await record_resume(
        scope, attempt_id=attempt_id, platform_run_id=f"run-r-{uuid.uuid4().hex[:8]}"
    )
    assert second["resume_index"] == 2  # 不新建提问/尝试，只追加恢复记录


# ------------------------------------------------------------------ worker


async def _isolate_event(event_id: str) -> None:
    """把其它待投递行推后，使本用例的目标事件成为唯一可领取项（确定性）."""
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        await conn.execute(
            "UPDATE agent_outbox SET available_at = now() + interval '1 hour'"
            " WHERE event_id <> %s AND status IN ('pending','leased')",
            (event_id,),
        )
        await conn.commit()


async def _drain_until(worker, event_id: str, *, tries: int = 12) -> None:
    """反复处理批次直到目标事件离开 pending/leased（避免受库内遗留任务影响）."""
    import psycopg

    for _ in range(tries):
        await worker.run_once()
        async with await psycopg.AsyncConnection.connect(
            os.environ["LEDGER_TEST_OWNER_URI"]
        ) as conn:
            cur = await conn.execute(
                "SELECT status FROM agent_outbox WHERE event_id=%s", (event_id,)
            )
            row = await cur.fetchone()
            if row and row[0] in ("done", "dead_letter"):
                return


@pytest.mark.anyio
async def test_worker_claims_and_completes_with_receipt() -> None:
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")

    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "w-1", "primary": False},
        event_id=event_id,
    )
    await _isolate_event(event_id)
    worker = OutboxWorker(owner="test-worker-1")
    await _drain_until(worker, event_id)
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT status, attempt_count FROM agent_outbox WHERE event_id = %s",
            (event_id,),
        )
        row = await cur.fetchone()
        assert row[0] == "done" and row[1] == 1
        cur = await conn.execute(
            "SELECT count(*) FROM agent_consumer_receipts WHERE event_id = %s",
            (event_id,),
        )
        assert (await cur.fetchone())[0] == 1


@pytest.mark.anyio
async def test_two_workers_do_not_double_apply() -> None:
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    import asyncio as aio

    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "w-2", "primary": False},
        event_id=event_id,
    )
    w1, w2 = OutboxWorker(owner="w1"), OutboxWorker(owner="w2")
    stats = await aio.gather(w1.run_once(), w2.run_once())
    total_done = sum(s["done"] for s in stats)
    # 同一 outbox 行只被一个 worker 有效处理
    assert total_done <= 1 + sum(s["claimed"] for s in stats)  # 允许其它遗留行
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM agent_consumer_receipts WHERE event_id = %s",
            (event_id,),
        )
        assert (await cur.fetchone())[0] == 1


@pytest.mark.anyio
async def test_poison_event_reaches_dead_letter() -> None:
    """毒事件超过重试预算进死信；不阻塞其它事件."""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")

    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "poison", "primary": False},
        event_id=event_id,
    )
    await _isolate_event(event_id)
    worker = OutboxWorker(owner="poison-worker", max_attempts=2)

    class Boom(Exception):
        pass

    async def _boom(*_a, **_k):
        raise Boom("poison")

    worker._handle = _boom  # type: ignore[method-assign]
    # 退避归零：同一行在连续批次内可再次领取（测试只关注死信判定）
    import common.event_ledger.worker as worker_mod

    original_backoff = worker_mod._backoff_seconds
    worker_mod._backoff_seconds = lambda *a, **k: 0.0
    try:
        await _drain_until(worker, event_id, tries=6)
    finally:
        worker_mod._backoff_seconds = original_backoff
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT status, error_class, attempt_count FROM agent_outbox WHERE event_id = %s",
            (event_id,),
        )
        status, error_class, attempts = await cur.fetchone()
        assert status == "dead_letter"
        assert error_class == "Boom" and attempts >= 2


@pytest.mark.anyio
async def test_replay_dry_run_does_not_call_tools() -> None:
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import replay

    result = await replay(tenant_id=TENANT, dry_run=True)
    assert result["dry_run"] is True
    assert "would_replay" in result and "cursor" in result


# ------------------------------------------------------------------ 校准


@pytest.mark.anyio
async def test_reconciler_keeps_unknown_when_platform_unavailable() -> None:
    from common.event_ledger.reconcile import PlatformUnavailable, Reconciler

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    await _bind_platform_run(attempt_id)

    class FailingProbe:
        async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
            raise PlatformUnavailable("down")

    reconciler = Reconciler(stalled_seconds=1, probe=FailingProbe())
    outcomes = await reconciler.reconcile_scope(scope)
    mine = [o for o in outcomes if o.attempt_id == attempt_id]
    assert mine and mine[0].action == "kept_unknown"
    assert mine[0].detail == "platform_unavailable"
    # 状态未被改成成功/取消
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT state FROM agent_run_attempts WHERE id=%s", (attempt_id,)
        )
        assert (await cur.fetchone())[0] == "running"


@pytest.mark.anyio
async def test_reconciler_calibrates_real_terminal_from_platform() -> None:
    from common.event_ledger.reconcile import Reconciler

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    await _bind_platform_run(attempt_id)

    class OkProbe:
        async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
            return "success"

    reconciler = Reconciler(stalled_seconds=0, probe=OkProbe())
    outcomes = await reconciler.reconcile_scope(scope)
    mine = [o for o in outcomes if o.attempt_id == attempt_id]
    assert mine and mine[0].action == "calibrated"
    assert mine[0].state == "succeeded"


@pytest.mark.anyio
async def test_reconciler_timeout_requires_platform_evidence() -> None:
    from common.event_ledger.reconcile import Reconciler

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    await _bind_platform_run(attempt_id)

    class TimeoutProbe:
        async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
            return "timeout"

    reconciler = Reconciler(stalled_seconds=0, probe=TimeoutProbe())
    outcomes = await reconciler.reconcile_scope(scope)
    mine = [o for o in outcomes if o.attempt_id == attempt_id]
    assert mine and mine[0].state == "timed_out"


@pytest.mark.anyio
async def test_unknown_platform_status_is_status_unknown() -> None:
    from common.event_ledger.lifecycle import business_state_from_native

    assert business_state_from_native("weird-status") == "status_unknown"
    assert business_state_from_native(None) == "status_unknown"
    assert business_state_from_native("interrupted") == "waiting_input"
    _ = dt.datetime.now()


# ------------------------------------------------------- 5.4 崩溃/关停与租约恢复


@pytest.mark.anyio
async def test_expired_lease_reclaimed_after_worker_crash() -> None:
    """worker 崩溃（未确认）后租约到期：另一 worker 可重领并完成，计数只一次。"""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "crash-1", "primary": False},
        event_id=event_id,
    )
    await _isolate_event(event_id)
    # 模拟 A 已领取后崩溃：租约已过期、状态仍为 leased
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        await conn.execute(
            "UPDATE agent_outbox SET status='leased', lease_owner='worker-A',"
            " lease_deadline = now() - interval '5 seconds' WHERE event_id=%s",
            (event_id,),
        )
        await conn.commit()

    worker_b = OutboxWorker(owner="worker-B")
    await _drain_until(worker_b, event_id)
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT status, lease_owner FROM agent_outbox WHERE event_id=%s",
            (event_id,),
        )
        status, owner = await cur.fetchone()
        assert status == "done" and owner is None
        cur = await conn.execute(
            "SELECT count(*) FROM agent_consumer_receipts WHERE event_id=%s",
            (event_id,),
        )
        assert (await cur.fetchone())[0] == 1  # 崩溃重领不产生重复消费


@pytest.mark.anyio
async def test_live_lease_not_stolen_by_other_worker() -> None:
    """未过期租约不被其它 worker 抢走（独占）。"""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "live-lease-1", "primary": False},
        event_id=event_id,
    )
    # A 领取且租约未到期
    worker_a = OutboxWorker(owner="worker-A", lease_seconds=120)
    claimed = await worker_a._claim()
    assert any(t.event_id == event_id for t in claimed)

    worker_b = OutboxWorker(owner="worker-B")
    stolen = [t for t in await worker_b._claim() if t.event_id == event_id]
    assert stolen == []

    # B 也不能替 A 确认（旧持有者确认被拒）
    task = next(t for t in claimed if t.event_id == event_id)
    await worker_b._finish(task)
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT status, lease_owner FROM agent_outbox WHERE event_id=%s",
            (event_id,),
        )
        status, owner = await cur.fetchone()
        assert status == "leased" and owner == "worker-A"


@pytest.mark.anyio
async def test_inflight_shutdown_returns_lease() -> None:
    """关停时在途任务归还租约（不丢事实，等下次重领）。"""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.store import write_event
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "langfuse", "trace_id": "shutdown-1", "primary": False},
        event_id=event_id,
    )
    worker = OutboxWorker(owner="worker-stop")
    await _isolate_event(event_id)
    worker.request_stop()
    stats = await worker.run_once()
    async with await psycopg.AsyncConnection.connect(
        os.environ["LEDGER_TEST_OWNER_URI"]
    ) as conn:
        cur = await conn.execute(
            "SELECT status, error_class FROM agent_outbox WHERE event_id=%s",
            (event_id,),
        )
        status, error_class = await cur.fetchone()
        # 归还为 pending（非 done、非死信），事实未丢
        assert status == "pending" and error_class == "worker_stopping"
    assert stats["claimed"] >= 1


# ------------------------------------------------ 原生入口：台账受理预留校验


@pytest.mark.anyio
async def test_attempt_reservation_accepts_active_owned_attempt() -> None:
    from common.event_ledger.store import attempt_reservation

    scope = _scope()
    _request_id, attempt_id = await _seed_request()
    row = await attempt_reservation(
        scope, attempt_id=attempt_id, thread_id="t-" + _request_id[:8]
    )
    assert row is not None and str(row["state"]) == "admitting"


@pytest.mark.anyio
async def test_attempt_reservation_rejects_unknown_wrong_owner_and_wrong_thread() -> (
    None
):
    """伪造/越权/错线程的预留一律返回 None（原生入口据此拒绝创建运行）."""
    from common.event_ledger.store import LedgerScope, attempt_reservation

    scope = _scope()
    request_id, attempt_id = await _seed_request()
    thread_id = "t-" + request_id[:8]
    # 不存在
    assert (
        await attempt_reservation(
            scope, attempt_id=str(uuid.uuid4()), thread_id=thread_id
        )
        is None
    )
    # 他人（同租户不同 user）
    other = LedgerScope(
        tenant_id=scope.tenant_id,
        user_id=str(uuid.uuid4()),
        membership_id=scope.membership_id,
    )
    assert (
        await attempt_reservation(other, attempt_id=attempt_id, thread_id=thread_id)
        is None
    )
    # 别的线程
    assert (
        await attempt_reservation(
            scope, attempt_id=attempt_id, thread_id="other-thread"
        )
        is None
    )
    # 跨租户
    cross = LedgerScope(
        tenant_id="99999999-9999-4999-8999-999999999999",
        user_id=scope.user_id,
        membership_id=scope.membership_id,
    )
    assert (
        await attempt_reservation(cross, attempt_id=attempt_id, thread_id=thread_id)
        is None
    )


@pytest.mark.anyio
async def test_attempt_reservation_rejects_terminal_attempt() -> None:
    """已终态尝试不得再作为新运行的预留（避免给结束的提问再开运行）."""
    from common.event_ledger.store import attempt_reservation, observe_state

    scope = _scope()
    request_id, attempt_id = await _seed_request()
    thread_id = "t-" + request_id[:8]
    assert (
        await attempt_reservation(scope, attempt_id=attempt_id, thread_id=thread_id)
        is not None
    )
    await observe_state(
        scope, attempt_id=attempt_id, state="succeeded", evidence_source="platform_read"
    )
    assert (
        await attempt_reservation(scope, attempt_id=attempt_id, thread_id=thread_id)
        is None
    )
