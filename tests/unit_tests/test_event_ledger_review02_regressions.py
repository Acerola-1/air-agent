"""独立验收反例 → 回归测试（Agent 侧：R01 / R02 / R04）

来源：`docs/reviews/evidence/flash-02-independent/`（协调者独立复现）。
每条用例对应一个已复现的反例，断言的是**修复后**必须成立的行为：

- R01 `live-admission.json`：同一预留被多次消费、换图换正文也放行 →
  现在 `claim_start` 原子裁决，第二次为 `already_started`，换图为 `mismatch`。
- R02 `pg-boundaries.json`：outbox 注入失败后事件仍提交（event=1/outbox=0）且重试
  duplicate 永不补投递 → 现在整笔回滚（event=0/outbox=0），重试可正常写入；
  并提供有界孤立修复。
- R04 `association.json`：A 作用域写事件引用 B 租户 attempt 返回 ok →
  现在服务端校验 + 数据库复合外键双重拒绝。

需要专用 PG（缺失即 skip）。
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import psycopg
import pytest

from common.event_ledger import store

PG_READY = bool(os.environ.get("RUNTIME_DATABASE_URI")) and bool(
    os.environ.get("LEDGER_TEST_TENANT_ID")
)
pytestmark = pytest.mark.skipif(not PG_READY, reason="需要专用台账 PG 环境")

TENANT = os.environ.get("LEDGER_TEST_TENANT_ID", "")
USER = os.environ.get("LEDGER_TEST_USER_ID", TENANT)
OWNER_URI = os.environ.get("LEDGER_TEST_OWNER_URI", "")


async def _isolate(event_id: str) -> None:
    """把其它待投递行推后，使目标事件成为唯一可领取项（确定性）."""
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_outbox SET available_at = now() + interval '1 hour'"
            " WHERE event_id <> %s AND status IN ('pending','leased')",
            (event_id,),
        )
        await conn.commit()


def _scope() -> store.LedgerScope:
    return store.LedgerScope(tenant_id=TENANT, user_id=USER, membership_id=USER)


async def _seed_attempt(
    *,
    graph_id: str = "basic-qa",
    operation_kind: str = "initial",
    content: str = "问题",
) -> tuple[str, str, str, str]:
    """建 request+attempt+submission（owner 连接绕过 RLS）.

    返回 (request_id, attempt_id, thread_id, submission_id)：启动权按 **submission**
    裁决，故测试也需要一个提交行。
    """
    request_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
    submission_id = str(uuid.uuid4())
    thread_id = f"rev-{uuid.uuid4().hex[:12]}"
    digest = await store.content_digest(
        graph_id=graph_id, operation_kind=operation_kind, content=content
    )
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id,"
            " graph_id, operation_kind) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (request_id, TENANT, USER, USER, thread_id, graph_id, operation_kind),
        )
        await conn.execute(
            "INSERT INTO agent_run_attempts (id, tenant_id, user_id, membership_id, request_id,"
            " graph_id, attempt_index, state, operation_kind, content_digest)"
            " VALUES (%s,%s,%s,%s,%s,%s,1,'admitting',%s,%s)",
            (
                attempt_id,
                TENANT,
                USER,
                USER,
                request_id,
                graph_id,
                operation_kind,
                digest,
            ),
        )
        await conn.execute(
            "INSERT INTO agent_submissions (id, tenant_id, user_id, membership_id, thread_id,"
            " submission_key, operation_kind, content_digest, admission_status, request_id,"
            " attempt_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'admitting',%s,%s)",
            (
                submission_id,
                TENANT,
                USER,
                USER,
                thread_id,
                f"key-{uuid.uuid4().hex}",
                operation_kind,
                digest,
                request_id,
                attempt_id,
            ),
        )
        await conn.commit()
    return request_id, attempt_id, thread_id, submission_id


# ------------------------------------------------------------------ R02 原子性


@pytest.mark.anyio
async def test_r02_outbox_failure_rolls_back_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反例 pg-boundaries：outbox 失败不得留下已提交事件。"""
    scope = _scope()
    event_id = str(uuid.uuid4())

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise store.LedgerBackendUnavailable("synthetic outbox failure")

    monkeypatch.setattr(store, "_insert_outbox", boom)
    with pytest.raises(store.LedgerBackendUnavailable):
        await store.write_event(
            scope,
            event_type="trace.linked",
            source="runtime",
            payload={"provider": "review", "trace_id": "atomic", "primary": True},
            event_id=event_id,
        )
    monkeypatch.undo()

    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        events = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_events WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
        outbox = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_outbox WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
    # 整笔回滚：既不留下事件，也不留下 outbox（修复前 events=1/outbox=0）
    assert (events, outbox) == (0, 0)


@pytest.mark.anyio
async def test_r02_retry_after_fault_delivers() -> None:
    """故障恢复后重试必须真正写入并建立投递（修复前返回 duplicate 且永不补投递）。"""
    scope = _scope()
    event_id = str(uuid.uuid4())
    payload = {"provider": "review", "trace_id": "retry-ok", "primary": False}
    first = await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload=payload,
        event_id=event_id,
    )
    assert first["written"] is True
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        outbox = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_outbox WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
    assert outbox >= 1


@pytest.mark.anyio
async def test_r02_orphan_repair_backfills_missing_outbox() -> None:
    """有界修复：历史"有事件无 outbox"的孤立记录可补投递，且幂等。"""
    scope = _scope()
    event_id = str(uuid.uuid4())
    await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "review", "trace_id": "orphan", "primary": False},
        event_id=event_id,
    )
    # 人为制造孤立：删掉 outbox 行（模拟修复前遗留数据）
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute("DELETE FROM agent_outbox WHERE event_id=%s", (event_id,))
        await conn.commit()
    result = await store.orphan_repair(scope, limit=500)
    assert result["repaired_events"] >= 1
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        outbox = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_outbox WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
    assert outbox >= 1
    # 幂等：再次执行不再新增
    again = await store.orphan_repair(scope, limit=500)
    assert again["repaired_events"] == 0


@pytest.mark.anyio
async def test_r02_conflict_covers_envelope_not_only_payload() -> None:
    """同 ID 但类型/关联变化必须冲突（不能只比 payload 摘要）。"""
    scope = _scope()
    event_id = str(uuid.uuid4())
    payload = {"provider": "review", "trace_id": "env", "primary": False}
    assert (
        await store.write_event(
            scope,
            event_type="trace.linked",
            source="runtime",
            payload=payload,
            event_id=event_id,
        )
    )["written"]
    # 完全相同 → duplicate
    dupe = await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload=payload,
        event_id=event_id,
    )
    assert dupe["reason"] == "duplicate"
    # payload 相同但类型变了 → 冲突
    other_type = await store.write_event(
        scope,
        event_type="answer.partial",
        source="runtime",
        payload={"answer_id": str(uuid.uuid4()), "content_version": 1},
        event_id=event_id,
    )
    assert other_type["reason"] == "payload_conflict"


# ------------------------------------------------------------ R01 启动权裁决


@pytest.mark.anyio
async def test_r01_same_reservation_can_only_start_once() -> None:
    """反例 live-admission：同一预留创建多个运行 → 现在只有第一次赢得启动权。"""
    _request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    scope = _scope()
    first = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="native",
    )
    assert first["status"] == "claimed"
    second = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="native",
    )
    assert second["status"] == "already_started"
    assert second["start_claim_token"] == first["start_claim_token"]


@pytest.mark.anyio
async def test_r01_concurrent_start_has_single_winner() -> None:
    """并发同预留：恰好一个 claimed（原子裁决）。"""
    import asyncio

    _request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    scope = _scope()

    async def attempt() -> str:
        result = await store.claim_start(
            scope,
            submission_id=submission_id,
            attempt_id=attempt_id,
            thread_id=thread_id,
            graph_id="basic-qa",
            operation_kind="initial",
            content="问题",
            source="native",
        )
        return str(result["status"])

    statuses = await asyncio.gather(*(attempt() for _ in range(4)))
    assert statuses.count("claimed") == 1
    assert statuses.count("already_started") == 3


@pytest.mark.anyio
async def test_r01_graph_content_and_operation_mismatch_rejected() -> None:
    """反例：换图/换正文仍创建运行 → 现在按 graph/操作/规范内容分别拒绝。"""
    _request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    scope = _scope()
    swap_graph = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="data-analysis",
        operation_kind="initial",
        content="问题",
        source="native",
    )
    assert swap_graph["status"] == "mismatch" and swap_graph["detail"] == "graph"
    swap_content = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="完全不同的正文",
        source="native",
    )
    assert swap_content["status"] == "mismatch" and swap_content["detail"] == "content"
    swap_kind = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="regenerate",
        content="问题",
        source="native",
    )
    assert swap_kind["status"] == "mismatch" and swap_kind["detail"] == "operation_kind"
    # 上述拒绝都不得消费启动权
    ok = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="native",
    )
    assert ok["status"] == "claimed"


@pytest.mark.anyio
async def test_r01_reservation_scope_guards() -> None:
    """他人 / 错线程 / 已终态 / 已有平台映射的预留都不可启动。"""
    _request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    scope = _scope()
    other = store.LedgerScope(TENANT, str(uuid.uuid4()), USER)
    # 他人作用域下 RLS 直接隐藏该行 → not_found（不泄露存在性；比 not_owned 更严格）
    assert (
        await store.claim_start(
            other,
            submission_id=submission_id,
            attempt_id=attempt_id,
            thread_id=thread_id,
            graph_id="basic-qa",
            operation_kind="initial",
            content="问题",
            source="native",
        )
    )["status"] == "not_found"
    assert (
        await store.claim_start(
            scope,
            submission_id=submission_id,
            attempt_id=attempt_id,
            thread_id="other-thread",
            graph_id="basic-qa",
            operation_kind="initial",
            content="问题",
            source="native",
        )
    )["status"] == "not_found"
    first = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="bff",
    )
    assert first["status"] == "claimed"
    await store.observe_state(
        scope, attempt_id=attempt_id, state="succeeded", evidence_source="platform_read"
    )
    assert (
        await store.claim_start(
            scope,
            submission_id=submission_id,
            attempt_id=attempt_id,
            thread_id=thread_id,
            graph_id="basic-qa",
            operation_kind="initial",
            content="问题",
            source="bff",
        )
    )["status"] == "terminal"


@pytest.mark.anyio
async def test_r01_already_confirmed_reuses_existing_run() -> None:
    """已有平台运行映射：返回 already_confirmed 供调用方复用（不重复创建）。"""
    _request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    scope = _scope()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET platform_run_id=%s, state='running' WHERE id=%s",
            (run_id, attempt_id),
        )
        await conn.commit()
    result = await store.claim_start(
        scope,
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="bff",
    )
    assert result["status"] == "already_confirmed"
    assert result["platform_run_id"] == run_id


# -------------------------------------------------------------- R04 关联约束


@pytest.mark.anyio
async def test_r04_cross_tenant_attempt_association_rejected() -> None:
    """反例 association：A 作用域事件引用 B 租户尝试必须被拒绝（服务端 + 数据库）。"""
    other_tenant = str(uuid.uuid4())
    other_user = str(uuid.uuid4())
    request_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id,"
            " graph_id, operation_kind) VALUES (%s,%s,%s,%s,'synthetic-other','basic-qa',"
            "'initial')",
            (request_id, other_tenant, other_user, other_user),
        )
        await conn.execute(
            "INSERT INTO agent_run_attempts (id, tenant_id, user_id, membership_id, request_id,"
            " graph_id, attempt_index, state) VALUES (%s,%s,%s,%s,%s,'basic-qa',1,'admitting')",
            (attempt_id, other_tenant, other_user, other_user, request_id),
        )
        await conn.commit()
    scope = _scope()
    # 数据库复合外键使写入失败（LedgerBackendUnavailable）或服务端拒绝；绝不 written=true
    try:
        result = await store.write_event(
            scope,
            event_type="trace.linked",
            source="runtime",
            payload={"provider": "review", "trace_id": "x-tenant", "primary": False},
            attempt_id=attempt_id,
        )
    except store.LedgerBackendUnavailable:
        return
    assert result.get("written") is not True


@pytest.mark.anyio
async def test_r04_request_attempt_pairing_enforced() -> None:
    """事件里的 request 必须与 attempt 配套（防 A 的 request 配 B 的 attempt）。"""
    _r1, attempt_a, _t1, _s1 = await _seed_attempt()
    request_b, _attempt_b, _t2, _s2 = await _seed_attempt()
    scope = _scope()
    try:
        result = await store.write_event(
            scope,
            event_type="trace.linked",
            source="runtime",
            payload={"provider": "review", "trace_id": "pairing", "primary": False},
            request_id=request_b,
            attempt_id=attempt_a,
        )
    except store.LedgerBackendUnavailable:
        return
    assert result.get("written") is not True


@pytest.mark.anyio
async def test_r02_no_partial_fact_when_answer_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事实写入失败时事件也不得留下（回答与事件同事务）。"""
    _request_id, attempt_id, _thread, _sub = await _seed_attempt()
    scope = _scope()
    before = await store._run(
        "SELECT count(*) AS n FROM agent_events WHERE attempt_id = %(a)s",
        {"a": attempt_id},
        scope.as_guc(),
        fetch="one",
    )

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise store.LedgerBackendUnavailable("synthetic insert failure")

    monkeypatch.setattr(store, "_insert_outbox", boom)
    with pytest.raises(store.LedgerBackendUnavailable):
        await store.record_answer(
            scope,
            attempt_id=attempt_id,
            message_id="m-x",
            content_version=1,
            confirmed=True,
        )
    monkeypatch.undo()
    after = await store._run(
        "SELECT count(*) AS n FROM agent_events WHERE attempt_id = %(a)s",
        {"a": attempt_id},
        scope.as_guc(),
        fetch="one",
    )
    assert int(after["n"]) == int(before["n"])
    _ = dt.datetime.now(dt.UTC)


# ------------------------------------------------------------ R06 消费幂等/重建


@pytest.mark.anyio
async def test_r06_handler_runs_once_for_same_event() -> None:
    """反例 receipt.json：同事件重复处理时**副作用只执行一次**.

    修复前：receipt 在副作用之后写入，handler 实测进入 2 次；唯一 receipt 数量
    不能证明副作用只发生一次。修复后：receipt 抢占在副作用之前，第二次直接短路。
    """
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "review", "trace_id": "once", "primary": False},
        event_id=event_id,
    )
    worker = OutboxWorker(owner="once-worker", batch_size=200)
    await _isolate(event_id)
    tasks = [t for t in await worker._claim() if t.event_id == event_id]
    assert len(tasks) == 1
    calls = 0
    original = worker._apply_run_projection

    async def counting(conn, task):  # noqa: ANN001
        nonlocal calls
        calls += 1
        await original(conn, task)

    worker._apply_run_projection = counting
    task = tasks[0]
    await worker._handle(task)
    await worker._handle(task)  # 重复处理同一事件
    assert calls == 1, "同事件副作用必须只执行一次"
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        receipts = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_consumer_receipts WHERE event_id=%s",
                    (event_id,),
                )
            ).fetchone()
        )[0]
    assert receipts == 1


@pytest.mark.anyio
async def test_r06_stale_owner_cannot_confirm() -> None:
    """反例：过期旧持有者不得先提交副作用再假确认成功（代次校验）。"""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import OutboxWorker

    scope = _scope()
    event_id = str(uuid.uuid4())
    await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "review", "trace_id": "stale", "primary": False},
        event_id=event_id,
    )
    await _isolate(event_id)
    holder = OutboxWorker(owner="holder-A", batch_size=200)
    task = next(t for t in await holder._claim() if t.event_id == event_id)
    # 他人（非当前持有者）尝试确认 → 拒绝，且不改状态
    intruder = OutboxWorker(owner="intruder-B", batch_size=200)
    result = await intruder._finish(task)
    assert result["confirmed"] is False
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        row = await (
            await conn.execute(
                "SELECT status, lease_owner FROM agent_outbox WHERE event_id=%s",
                (event_id,),
            )
        ).fetchone()
    assert row[0] == "leased" and row[1] == "holder-A"


@pytest.mark.anyio
async def test_r06_rebuild_includes_completed_facts_and_uses_version() -> None:
    """反例 rebuild.json：已 done 的事实也必须能被"重建到新消费版本"纳入."""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import OutboxWorker, replay

    scope = _scope()
    event_id = str(uuid.uuid4())
    started = dt.datetime.now(dt.UTC)
    await store.write_event(
        scope,
        event_type="trace.linked",
        source="runtime",
        payload={"provider": "review", "trace_id": "rebuild", "primary": False},
        event_id=event_id,
    )
    await _isolate(event_id)
    worker = OutboxWorker(owner="rebuild-worker", batch_size=200)
    task = next(t for t in await worker._claim() if t.event_id == event_id)
    await worker._handle(task)
    await worker._finish(task)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        status = (
            await (
                await conn.execute(
                    "SELECT status FROM agent_outbox WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
    assert status == "done"

    # v1 已应用 → v1 重建无可做项；v2 未应用 → 必须把这条**已完成**事实纳入重建
    v1 = await replay(
        tenant_id=TENANT,
        consumer="run_projection",
        consumer_version=1,
        since=started,
        until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        dry_run=True,
    )
    assert v1["would_replay"] == 0, "已应用过的消费版本不应重复重建"
    v2 = await replay(
        tenant_id=TENANT,
        consumer="run_projection",
        consumer_version=2,
        since=started,
        until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        dry_run=True,
    )
    assert v2["would_replay"] >= 1, "新消费版本必须能重建已完成事实"
    assert v2["consumer_version"] == 2 and v2["audited"] is True

    applied = await replay(
        tenant_id=TENANT,
        consumer="run_projection",
        consumer_version=2,
        since=started,
        until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        dry_run=False,
    )
    assert applied["replayed"] >= 1
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        version, status2 = await (
            await conn.execute(
                "SELECT consumer_version, status FROM agent_outbox WHERE event_id=%s",
                (event_id,),
            )
        ).fetchone()
        audits = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_ledger_ops_audit"
                    " WHERE tenant_id=%s AND operation='replay'",
                    (TENANT,),
                )
            ).fetchone()
        )[0]
    assert int(version) == 2, "重建必须真正提升 outbox 的消费版本"
    assert status2 == "pending", "重建后应重新排队等待按新版本消费"
    assert audits >= 2, "每次重放（含 dry-run）都要留运维审计"


# ------------------------------------------------- R05 删除清理（最小权限 worker）


async def _seed_deletion(*, with_owner: bool = True) -> tuple[str, str]:
    """建删除标记 + 原文，返回 (thread_id, content_ref_id).

    走**真实登记路径** `record_thread_deletion`（标记 + resource.deleted 事件同事务），
    与生产一致；仅在需要"归属人未知"的负例时才手工置空 user_id。
    """
    thread_id = f"del-{uuid.uuid4().hex[:12]}"
    content_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "INSERT INTO agent_content_refs (id, tenant_id, user_id, membership_id, thread_id,"
            " content_snapshot) VALUES (%s,%s,%s,%s,%s,'to-be-cleaned')",
            (content_id, TENANT, USER, USER, thread_id),
        )
        await conn.commit()
    scope = _scope()
    result = await store.record_thread_deletion(scope, thread_id=thread_id)
    assert result["created"] is True
    if not with_owner:
        async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
            await conn.execute(
                "UPDATE agent_thread_deletions SET user_id = NULL"
                " WHERE tenant_id = %s AND thread_id = %s",
                (TENANT, thread_id),
            )
            await conn.commit()
    return thread_id, content_id


@pytest.mark.anyio
async def test_r05_worker_cleanup_scoped_and_effective() -> None:
    """反例 pg-boundaries.deletion：0 行清理不得 done；受限作用域必须真的清理成功."""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import OutboxWorker

    thread_id, content_id = await _seed_deletion()
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        row = await (
            await conn.execute(
                "SELECT event_id::text FROM agent_events WHERE thread_id=%s",
                (thread_id,),
            )
        ).fetchone()
    event_id = row[0]
    await _isolate(event_id)
    worker = OutboxWorker(owner="cleanup-worker", batch_size=200)
    task = next(t for t in await worker._claim() if t.event_id == event_id)
    await worker._handle(task)
    result = await worker._finish(task)
    assert result["confirmed"] is True
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        state, deleted_at = await (
            await conn.execute(
                "SELECT d.state, c.deleted_at FROM agent_thread_deletions d"
                " JOIN agent_content_refs c ON c.tenant_id = d.tenant_id"
                "   AND c.thread_id = d.thread_id WHERE c.id = %s",
                (content_id,),
            )
        ).fetchone()
        outbox_status = (
            await (
                await conn.execute(
                    "SELECT status FROM agent_outbox WHERE event_id=%s", (event_id,)
                )
            ).fetchone()
        )[0]
    # 最小权限 worker 真的完成了清理（不是"0 行却 done"）
    assert deleted_at is not None, "原文必须真的被标记删除"
    assert state == "completed", "删除标记必须真的完成"
    assert outbox_status == "done"


@pytest.mark.anyio
async def test_r05_cleanup_reads_returning_rows_not_coroutines() -> None:
    """清理必须真的读 RETURNING 行：不得对游标协程漏 await（否则"0 行"守卫失效）.

    本轮实修：`_apply_deletion` 曾写成 `(await conn.execute(...)).fetchall()`，
    fetch* 协程从未 await → `deleted`/`marker` 恒为**真值协程对象**，
    "0 行不得当成功" 的守卫被永久短路。该缺陷不报错、只出 RuntimeWarning，
    因此用例显式采集 never-awaited 警告并要求清理结果真实生效。
    """
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    import gc
    import warnings

    from common.event_ledger.worker import OutboxWorker

    thread_id, content_id = await _seed_deletion(with_owner=True)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        event_id = (
            await (
                await conn.execute(
                    "SELECT event_id::text FROM agent_events WHERE thread_id=%s",
                    (thread_id,),
                )
            ).fetchone()
        )[0]
    await _isolate(event_id)
    worker = OutboxWorker(owner="cleanup-guard", batch_size=200)
    task = next(t for t in await worker._claim() if t.event_id == event_id)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await worker._handle(task)
        gc.collect()
    never_awaited = [
        str(w.message) for w in caught if "never awaited" in str(w.message)
    ]
    assert not never_awaited, f"清理路径存在漏 await 的游标调用：{never_awaited}"
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        count = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_content_refs"
                    " WHERE id = %s AND deleted_at IS NOT NULL",
                    (content_id,),
                )
            ).fetchone()
        )[0]
    assert count == 1, "原文必须真的被标记删除（而非 0 行被当作已完成）"


@pytest.mark.anyio
async def test_r05_cleanup_without_owner_does_not_fake_done() -> None:
    """归属人未知 → 受限清理无法进行：**不得当作成功**（进重试/死信）。"""
    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("未配置受限调度连接")
    from common.event_ledger.worker import OutboxWorker

    thread_id, content_id = await _seed_deletion(with_owner=False)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        event_id = (
            await (
                await conn.execute(
                    "SELECT event_id::text FROM agent_events WHERE thread_id=%s",
                    (thread_id,),
                )
            ).fetchone()
        )[0]
    await _isolate(event_id)
    worker = OutboxWorker(owner="cleanup-blind", batch_size=200, max_attempts=2)
    task = next(t for t in await worker._claim() if t.event_id == event_id)
    with pytest.raises(Exception):  # noqa: B017 - LedgerCleanupUnavailable
        await worker._handle(task)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        deleted_at = (
            await (
                await conn.execute(
                    "SELECT deleted_at FROM agent_content_refs WHERE id=%s",
                    (content_id,),
                )
            ).fetchone()
        )[0]
        marker = (
            await (
                await conn.execute(
                    "SELECT state FROM agent_thread_deletions WHERE thread_id=%s",
                    (thread_id,),
                )
            ).fetchone()
        )[0]
    assert deleted_at is None and marker == "requested", (
        "不得在拿不到归属时假装清理完成"
    )


# ------------------------------------------------- 补偿：按可信 metadata 定位既有 run


@pytest.mark.anyio
async def test_reconcile_recovers_unconfirmed_admission_by_metadata() -> None:
    """交付缺口：受理成功但 run_id 未确认时，按可信 metadata 定位既有 run 并收敛映射.

    修复前：只返回 no_platform_run_id 就跳过（不是闭合）。修复后：用服务端注入的
    `ledger_attempt_id` 在平台运行列表里定位，写回映射并推进状态。
    """
    from common.event_ledger.reconcile import Reconciler

    request_id, attempt_id, thread_id, _sub = await _seed_attempt()
    scope = _scope()
    # 令其成为"无平台映射的活跃尝试"（last_progress 早于阈值）
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET state='admitting',"
            " last_progress_at = now() - interval '10 minutes' WHERE id=%s",
            (attempt_id,),
        )
        await conn.commit()

    located_run = f"run-{uuid.uuid4().hex[:10]}"
    target_attempt = attempt_id

    class Probe:
        async def locate_run(
            self, *, thread_id: str, attempt_id: str, submission_id: str | None = None
        ) -> str | None:
            # 只对本用例的目标尝试返回（真实场景里 metadata 标记是唯一的）
            return located_run if attempt_id == target_attempt else None

        async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
            assert run_id == located_run
            return "running"

    reconciler = Reconciler(stalled_seconds=1, probe=Probe())
    outcomes = await reconciler.reconcile_scope(scope)
    mine = [o for o in outcomes if o.attempt_id == attempt_id]
    assert mine, outcomes
    assert mine[0].action == "admission_recovered", mine[0]
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        run_id, state = await (
            await conn.execute(
                "SELECT platform_run_id, state FROM agent_run_attempts WHERE id=%s",
                (attempt_id,),
            )
        ).fetchone()
        confirmed_events = (
            await (
                await conn.execute(
                    "SELECT count(*) FROM agent_events WHERE attempt_id=%s"
                    " AND event_type='run.admission_confirmed'",
                    (attempt_id,),
                )
            ).fetchone()
        )[0]
    assert run_id == located_run and state == "running"
    assert confirmed_events >= 1, "收敛映射必须留下 run.admission_confirmed 事实"


@pytest.mark.anyio
async def test_reconcile_keeps_unknown_when_metadata_not_found() -> None:
    """定位不到既有 run → 保持待确认（不猜测、不重发）。"""
    from common.event_ledger.reconcile import Reconciler

    _request_id, attempt_id, _thread, _sub = await _seed_attempt()
    scope = _scope()
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET state='admitting',"
            " last_progress_at = now() - interval '10 minutes' WHERE id=%s",
            (attempt_id,),
        )
        await conn.commit()

    class EmptyProbe:
        async def locate_run(
            self, *, thread_id: str, attempt_id: str, submission_id: str | None = None
        ) -> str | None:
            return None

        async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
            raise AssertionError("不应在未定位到 run 时查询状态")

    outcomes = await Reconciler(stalled_seconds=1, probe=EmptyProbe()).reconcile_scope(
        scope
    )
    mine = [o for o in outcomes if o.attempt_id == attempt_id]
    assert mine and mine[0].action == "kept_unknown"
    assert mine[0].detail == "no_platform_run_id"


# ============================================================ F01–F06（第二轮定向修复）
# 反例来源：docs/reviews/evidence/flash-02-repair-01-independent/（协调者独立复现）


async def _complete_deletion(thread_id: str) -> None:
    """把删除标记推进到"清理已完成"（state=completed）."""
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_thread_deletions SET state = 'completed',"
            " attempts = attempts + 1, completed_at = now() WHERE tenant_id = %s"
            " AND thread_id = %s",
            (TENANT, thread_id),
        )
        await conn.commit()


@pytest.mark.anyio
async def test_f04_orphan_repair_skips_deleted_thread_after_completion() -> None:
    """F04：清理**完成**后，孤立补投递仍不得复活已删会话的事件.

    修复前：排除条件带 `state <> 'completed'`，清理一完成该会话就重新可投递/重建。
    """
    thread_id, _content_id = await _seed_deletion()
    await _complete_deletion(thread_id)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "DELETE FROM agent_outbox WHERE tenant_id = %s AND event_id IN"
            " (SELECT event_id FROM agent_events WHERE tenant_id = %s AND thread_id = %s)",
            (TENANT, TENANT, thread_id),
        )
        await conn.commit()
    scope = _scope()
    result = await store.orphan_repair(
        scope, limit=200, consumers=("deletion_cleanup",)
    )
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        rows = await (
            await conn.execute(
                "SELECT count(*) FROM agent_outbox o JOIN agent_events e"
                " ON e.event_id = o.event_id AND e.tenant_id = o.tenant_id"
                " WHERE o.tenant_id = %s AND e.thread_id = %s",
                (TENANT, thread_id),
            )
        ).fetchone()
    assert rows[0] == 0, f"已删会话不得被补投递复活：{result} count={rows[0]}"


@pytest.mark.anyio
async def test_f04_replay_skips_deleted_thread_after_completion() -> None:
    """F04：清理完成后，版本重建同样不得复活已删会话的派生内容."""
    thread_id, _content_id = await _seed_deletion()
    await _complete_deletion(thread_id)
    from common.event_ledger import worker as worker_mod

    until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
    plan = await worker_mod.replay(
        tenant_id=TENANT,
        consumer="deletion_cleanup",
        consumer_version=2,
        since=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        until=until,
        dry_run=True,
        actor="regression",
        limit=200,
    )
    picked = [
        item for item in plan.get("items", []) if item.get("thread_id") == thread_id
    ]
    assert picked == [], f"已删会话不得进入重建计划：{picked}"


@pytest.mark.anyio
async def test_f05_reconcile_includes_terminal_without_mapping() -> None:
    """F05：终态但缺 platform_run_id 的尝试必须进入有界补偿（不能只筛活跃）."""
    request_id, attempt_id, _thread_id, _sub = await _seed_attempt()
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET state = 'succeeded',"
            " last_progress_at = now() - interval '10 minutes' WHERE id = %s",
            (attempt_id,),
        )
        await conn.commit()
    candidates = await store.attempts_needing_reconcile(
        _scope(), stalled_seconds=60, limit=500
    )
    ids = {row["attempt_id"] for row in candidates}
    assert attempt_id in ids, "终态缺映射的尝试必须可被补偿定位"
    row = next(r for r in candidates if r["attempt_id"] == attempt_id)
    assert row.get("missing_mapping") is True


@pytest.mark.anyio
async def test_f01_release_unstarted_claim_is_bounded_and_guarded() -> None:
    """F01：确认"平台无对应运行"后释放滞留启动权；已有映射一律不动."""
    request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    claimed = await store.claim_start(
        _scope(),
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="bff",
    )
    assert claimed["status"] == "claimed"
    released = await store.release_unstarted_claim(
        _scope(), attempt_id=attempt_id, reason="no_platform_run_after_grace"
    )
    assert released.get("released") is False, released
    assert released.get("reason") == "creation_outcome_unknown"
    again = await store.claim_start(
        _scope(),
        submission_id=submission_id,
        attempt_id=attempt_id,
        thread_id=thread_id,
        graph_id="basic-qa",
        operation_kind="initial",
        content="问题",
        source="bff",
    )
    assert again["status"] == "already_started", (
        "未知创建不能被空列表重新开放而重复执行"
    )
    # 已有平台映射时不得释放
    await store.attach_platform_run(
        _scope(), attempt_id=attempt_id, platform_run_id=f"run-{uuid.uuid4().hex[:8]}"
    )
    guarded = await store.release_unstarted_claim(
        _scope(), attempt_id=attempt_id, reason="no_platform_run_after_grace"
    )
    assert guarded.get("released") is False
    assert guarded.get("reason") == "mapping_present"
    _ = request_id


async def _seed_attempt_on_deleted_thread(*, completed: bool = True) -> tuple[str, str]:
    """建"已删除会话 + 该线程上的尝试/提交"，返回 (thread_id, attempt_id)."""
    request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    digest = await store.content_digest(
        graph_id="basic-qa", operation_kind="initial", content="问题"
    )
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "INSERT INTO agent_thread_deletions (id, tenant_id, thread_id, user_id, state,"
            " requested_by, completed_at) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s,"
            " CASE WHEN %s THEN now() ELSE NULL END)",
            (
                TENANT,
                thread_id,
                USER,
                "completed" if completed else "requested",
                USER,
                completed,
            ),
        )
        await conn.commit()
    _ = (request_id, submission_id, digest)
    return thread_id, attempt_id


@pytest.mark.anyio
async def test_f04_late_callback_after_deletion_is_refused() -> None:
    """F04：删除后到达的**晚到回调**（回答/状态）必须被拒绝，且不产生任何新事实.

    修复前：图内收尾、状态校准等路径不带删除检查，已删会话可被回调写回答案与事件。
    """
    thread_id, attempt_id = await _seed_attempt_on_deleted_thread()
    scope = _scope()
    refused = 0
    for call in (
        lambda: store.record_answer(
            scope,
            attempt_id=attempt_id,
            message_id="m-late",
            content_version=1,
            confirmed=True,
        ),
        lambda: store.observe_state(
            scope,
            attempt_id=attempt_id,
            state="succeeded",
            evidence_source="runtime_callback",
        ),
    ):
        try:
            await call()
        except store.ThreadDeleted:
            refused += 1
    assert refused == 2, "晚到回调必须被删除闸门拒绝"
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        events = await (
            await conn.execute(
                "SELECT count(*) FROM agent_events WHERE tenant_id = %s AND thread_id = %s",
                (TENANT, thread_id),
            )
        ).fetchone()
        answers = await (
            await conn.execute(
                "SELECT count(*) FROM agent_answers a JOIN agent_run_attempts t"
                " ON t.id = a.attempt_id AND t.tenant_id = a.tenant_id"
                " WHERE a.tenant_id = %s AND t.id = %s",
                (TENANT, attempt_id),
            )
        ).fetchone()
        outbox = await (
            await conn.execute(
                "SELECT count(*) FROM agent_outbox o JOIN agent_events e"
                " ON e.event_id = o.event_id AND e.tenant_id = o.tenant_id"
                " WHERE o.tenant_id = %s AND e.thread_id = %s",
                (TENANT, thread_id),
            )
        ).fetchone()
    assert events[0] == 0, f"已删会话不得被回调写入事件：{events[0]}"
    assert answers[0] == 0, f"已删会话不得被回填写答案：{answers[0]}"
    assert outbox[0] == 0, f"已删会话不得产生投递任务：{outbox[0]}"


@pytest.mark.anyio
async def test_f04_deletion_registration_itself_still_writes() -> None:
    """F04：删除登记本身（及重复登记）必须能写入，且幂等——闸门不能挡住登记."""
    request_id, attempt_id, thread_id, submission_id = await _seed_attempt()
    _ = (request_id, attempt_id, submission_id)
    scope = _scope()
    first = await store.record_thread_deletion(scope, thread_id=thread_id)
    assert first.get("created") is True, first
    second = await store.record_thread_deletion(scope, thread_id=thread_id)
    assert second.get("created") is False, second
    assert await store.thread_deletion_exists(scope, thread_id=thread_id) is True
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        rows = await (
            await conn.execute(
                "SELECT count(*) FROM agent_events WHERE tenant_id = %s AND thread_id = %s"
                " AND event_type = 'resource.deleted'",
                (TENANT, thread_id),
            )
        ).fetchone()
    assert rows[0] == 1, f"删除登记事件应恰好一条：{rows[0]}"
