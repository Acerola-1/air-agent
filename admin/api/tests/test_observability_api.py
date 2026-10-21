"""真实PG组合回归；模型为明确标注的LangChain受控调用，不代替生产模型验收。"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db import OwnerSessionLocal
from tests.test_event_ledger_api import _account_of, _grant_thread, _login
from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_feedback_api import _make_confirmed_answer

pytest.importorskip("common.observability")
from common.observability import store
from common.observability.collector import get_collector, reset_collector_for_tests
from common.observability.context import ObservationContext
from common.observability.model_adapter import ModelObservabilityHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

pytestmark = pytest.mark.asyncio


async def fixture(env):
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    key = "obs-" + uuid.uuid4().hex[:12]
    data = await _make_confirmed_answer(client, env, thread, key, user_message_id=uuid.uuid4().hex)
    async with OwnerSessionLocal() as session:
        membership = (
            await session.execute(
                text("SELECT membership_id FROM agent_run_attempts WHERE id=:a"),
                {"a": data["attempt_id"]},
            )
        ).scalar_one()
    ctx = ObservationContext(
        str(env["tenant_a"]),
        str(env["user_a"]),
        str(membership),
        data["request_id"],
        data["attempt_id"],
        data["submission_id"],
        "basic-qa",
        "initial",
        thread,
        "run-" + key,
    )
    return client, data, ctx


async def count(table, attempt):
    async with OwnerSessionLocal() as s:
        return (
            await s.execute(
                text(f"SELECT count(*) FROM {table} WHERE attempt_id=:a"), {"a": attempt}
            )
        ).scalar_one()


async def test_standard_callback_persists_usage_cost_and_outbox(ledger_env, monkeypatch):
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    reset_collector_for_tests()
    c, d, ctx = await fixture(ledger_env)
    try:
        model = GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="受控回答",
                        usage_metadata={
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                        response_metadata={"model_name": "controlled-fixture"},
                    )
                ]
            )
        )
        h = ModelObservabilityHandler(ctx)
        await model.ainvoke(
            "controlled fixture", config={"callbacks": [h], "metadata": {"obs_role": "main"}}
        )
        stats = await asyncio.to_thread(get_collector().flush, 2)
        assert stats["written"] == 2 and stats["failed"] == 0, stats
        result = await c.get("/agent/observability/runs/" + d["attempt_id"])
        assert result.status_code == 200, result.text
        rows = result.json()["model_observations"]
        assert (
            len(rows) == 1
            and rows[0]["usage"]["total_tokens"] == 120
            and rows[0]["model"] == "controlled-fixture"
        )
        assert await count("agent_cost_revisions", d["attempt_id"]) == 1
        async with OwnerSessionLocal() as s:
            statuses = list(
                (
                    await s.execute(
                        text(
                            "SELECT measurement_status FROM agent_cost_revision"
                            "s WHERE attempt_id=:a"
                        ),
                        {"a": d["attempt_id"]},
                    )
                ).scalars()
            )
            events = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM agent_events e JOIN agent_outbox "
                        "o ON o.tenant_id=e.tenant_id AND o.event_id=e.event_id"
                        " WHERE e.attempt_id=:a AND e.event_type LIKE 'runtime."
                        "%'"
                    ),
                    {"a": d["attempt_id"]},
                )
            ).scalar_one()
        assert statuses == ["price_unknown"] and events >= 3
        met = await c.get("/agent/observability/metrics")
        assert met.status_code == 200, met.text
        assert met.json()["tokens"]["total"] == 120
    finally:
        await c.aclose()
        await get_collector().close()
        reset_collector_for_tests()
        await store.close_pool()


async def test_storage_duplicate_atomic_rollback_and_deletion(ledger_env, monkeypatch):
    c, d, ctx = await fixture(ledger_env)
    oid = str(uuid.uuid4())
    kwargs = dict(
        observation_id=oid,
        logical_call_id=str(uuid.uuid4()),
        role="main",
        model="controlled",
        provider="controlled",
        status="succeeded",
        usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        measurement_status="measured",
        started_at=dt.datetime.now(dt.UTC),
        ended_at=dt.datetime.now(dt.UTC),
    )
    try:
        original = store._write_event_in

        async def fail(*args, **kw):
            raise RuntimeError("controlled failure")

        monkeypatch.setattr(store, "_write_event_in", fail)
        with pytest.raises(RuntimeError):
            await store.record_model_observation(ctx, **kwargs)
        assert await count("agent_model_observations", d["attempt_id"]) == 0
        monkeypatch.setattr(store, "_write_event_in", original)
        first = await store.record_model_observation(ctx, **kwargs)
        second = await store.record_model_observation(ctx, **kwargs)
        assert first["written"] and second["reason"] == "duplicate"
        assert (
            await count("agent_model_observations", d["attempt_id"]) == 1
            and await count("agent_cost_revisions", d["attempt_id"]) == 1
        )
        bad = replace(ctx, request_id=str(uuid.uuid4()))
        with pytest.raises(ValueError):
            await store.record_model_observation(
                bad, **{**kwargs, "observation_id": str(uuid.uuid4())}
            )
        delete = await c.post(
            "/agent/threads/deletion",
            json={"thread_id": ctx.thread_id},
            headers={"origin": "http://localhost:3000"},
        )
        assert delete.status_code == 200, delete.text
        with pytest.raises(store.ThreadDeleted):
            await store.record_model_observation(ctx, **kwargs)
        with pytest.raises(store.ThreadDeleted):
            await store.record_trace_link(ctx, trace_id=uuid.uuid4().hex)
        after = await c.get("/agent/observability/runs/" + d["attempt_id"])
        assert after.status_code == 404 or after.json()["model_observations"] == []
        metrics = await c.get("/agent/observability/metrics")
        assert metrics.json()["totals"]["observations"] == 0
    finally:
        await c.aclose()
        await store.close_pool()


async def test_price_backfill_revision_is_idempotent_and_currencies_separate(ledger_env):
    c, d, ctx = await fixture(ledger_env)
    try:
        async with OwnerSessionLocal() as s:
            await s.execute(
                text(
                    "INSERT INTO role_permissions(role_id,permission_id) SELECT"
                    " mr.role_id,p.id FROM membership_roles mr JOIN permissions"
                    " p ON p.code='agent:pricing:manage' WHERE mr.membership_id"
                    "=:m ON CONFLICT DO NOTHING"
                ),
                {"m": ctx.membership_id},
            )
            await s.commit()
        o = str(uuid.uuid4())
        await store.record_model_observation(
            ctx,
            observation_id=o,
            logical_call_id=str(uuid.uuid4()),
            role="main",
            model="priced-fixture",
            provider="controlled",
            status="succeeded",
            usage={
                "input_tokens": 1000,
                "output_tokens": 200,
                "total_tokens": 1200,
                "cache_read_tokens": 400,
                "reasoning_tokens": 50,
            },
            measurement_status="measured",
            started_at=dt.datetime.now(dt.UTC),
        )
        price = {
            "provider": "controlled",
            "model": "priced-fixture",
            "currency": "CNY",
            "input_per_unit": "2",
            "output_per_unit": "8",
            "cache_read_per_unit": ".2",
            "effective_from": (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat(),
        }
        created = await c.post(
            "/agent/observability/prices", json=price, headers={"origin": "http://localhost:3000"}
        )
        assert created.status_code == 201, created.text
        overlap = await c.post(
            "/agent/observability/prices", json=price, headers={"origin": "http://localhost:3000"}
        )
        assert overlap.status_code == 409, overlap.text
        r1 = await c.post(
            "/agent/observability/prices/recalculate",
            json={"attempt_id": d["attempt_id"]},
            headers={"origin": "http://localhost:3000"},
        )
        r2 = await c.post(
            "/agent/observability/prices/recalculate",
            json={"attempt_id": d["attempt_id"]},
            headers={"origin": "http://localhost:3000"},
        )
        assert r1.status_code == 200 and r1.json()["cost_revisions_written"] == 1, r1.text
        assert r2.status_code == 200 and r2.json()["cost_revisions_written"] == 0, r2.text
        summary = await c.get("/agent/observability/costs")
        assert summary.status_code == 200, summary.text
        assert (
            summary.json()["by_currency"][0]["subtotal"] == "0.0028800000"
            and summary.json()["by_currency"][0]["priced"] == 1
        )
        assert await count("agent_cost_revisions", d["attempt_id"]) == 2
        corrected = await c.post(
            "/agent/observability/prices",
            json={
                **price,
                "input_per_unit": "3",
                "supersedes_price_ref": created.json()["price_ref"],
            },
            headers={"origin": "http://localhost:3000"},
        )
        assert corrected.status_code == 201, corrected.text
        again = await c.post(
            "/agent/observability/prices/recalculate",
            json={"attempt_id": d["attempt_id"]},
            headers={"origin": "http://localhost:3000"},
        )
        assert again.status_code == 200 and again.json()["cost_revisions_written"] == 1, again.text
        summary = await c.get("/agent/observability/costs")
        assert summary.json()["by_currency"][0]["subtotal"] == "0.0034800000", summary.text
        async with OwnerSessionLocal() as session:
            original = (
                (
                    await session.execute(
                        text(
                            "SELECT input_per_unit,retired_at FROM agent_model_"
                            "prices WHERE tenant_id=:t AND price_ref=:ref"
                        ),
                        {"t": ctx.tenant_id, "ref": created.json()["price_ref"]},
                    )
                )
                .mappings()
                .one()
            )
            assert original["input_per_unit"] == 2 and original["retired_at"] is not None
        free = await c.post(
            "/agent/observability/prices",
            json={
                **price,
                "input_per_unit": "0",
                "output_per_unit": "0",
                "cache_read_per_unit": "0",
                "supersedes_price_ref": corrected.json()["price_ref"],
            },
            headers={"origin": "http://localhost:3000"},
        )
        assert free.status_code == 201, free.text
        await c.post(
            "/agent/observability/prices/recalculate",
            json={"attempt_id": d["attempt_id"]},
            headers={"origin": "http://localhost:3000"},
        )
        summary = await c.get("/agent/observability/costs")
        assert (
            Decimal(summary.json()["by_currency"][0]["subtotal"]) == Decimal(0)
            and summary.json()["complete_cost_known"] is True
        ), summary.text

    finally:
        await c.aclose()
        await store.close_pool()


async def test_real_queue_overhead_benchmark(ledger_env, tmp_path):
    """基准必须实际入队并写入，空队列的零开销不可通过。"""
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "aa_benchmark", root / "tests/bench_observability_overhead.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    c, d, ctx = await fixture(ledger_env)
    try:
        result = await module.run_benchmark(ctx)
        assert await count("agent_model_observations", d["attempt_id"]) == 80
        (tmp_path / "overhead-benchmark.json").write_text(json.dumps(result, indent=2) + "\n")
    finally:
        await c.aclose()
        await store.close_pool()


async def test_restricted_worker_projects_and_retries_external_cleanup(ledger_env, monkeypatch):
    """真实受限调度身份：投影闭环，远端故障保留重试，不回滚本地删除。"""
    import os

    import psycopg
    from common.event_ledger.worker import OutboxWorker
    from common.observability import external_cleanup

    if not os.environ.get("LEDGER_WORKER_DATABASE_URI"):
        pytest.skip("需要隔离实例内的受限worker账号")
    c, d, ctx = await fixture(ledger_env)
    try:
        with psycopg.connect(os.environ["LEDGER_WORKER_DATABASE_URI"]) as conn:
            role = conn.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            assert role == (False, False)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT payload FROM agent_events")
        oid = str(uuid.uuid4())
        await store.record_model_observation(
            ctx,
            observation_id=oid,
            logical_call_id=str(uuid.uuid4()),
            role="main",
            model="controlled",
            provider="controlled",
            status="succeeded",
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            measurement_status="measured",
        )
        worker = OutboxWorker(consumers=("observability_projection",), tenant_id=ctx.tenant_id)
        first = await worker.run_once()
        assert (
            first["claimed"] > 0 and first["done"] == first["claimed"] and first["retried"] == 0
        ), first
        async with OwnerSessionLocal() as session:
            projection = (
                await session.execute(
                    text("SELECT count(*) FROM agent_observability_projection WHERE tenant_id=:t"),
                    {"t": ctx.tenant_id},
                )
            ).scalar_one()
            assert projection == 1
        deleted = await c.post("/agent/threads/deletion", json={"thread_id": ctx.thread_id})
        assert deleted.status_code == 200, deleted.text
        async with OwnerSessionLocal() as session:
            tasks_before = (
                (
                    await session.execute(
                        text(
                            "SELECT consumer,status,thread_id,available_at<=clo"
                            "ck_timestamp() AS ready FROM agent_outbox WHERE te"
                            "nant_id=:t AND thread_id=:th"
                        ),
                        {"t": ctx.tenant_id, "th": ctx.thread_id},
                    )
                )
                .mappings()
                .all()
            )
        local_worker = OutboxWorker(consumers=("deletion_cleanup",), tenant_id=ctx.tenant_id)
        for _ in range(20):
            local = await local_worker.run_once()
            if local["claimed"]:
                break
            await asyncio.sleep(0.05)
        assert local["done"] == 1 and local["retried"] == 0, {
            "worker": local,
            "before": [dict(r) for r in tasks_before],
            "deletion_response": deleted.json(),
        }

        async def unavailable(conn, task):
            raise external_cleanup.ExternalTraceCleanupUnavailable("controlled_remote_outage")

        monkeypatch.setattr(external_cleanup, "cleanup_thread_traces", unavailable)
        external = await OutboxWorker(
            consumers=("external_trace_cleanup",), tenant_id=ctx.tenant_id
        ).run_once()
        assert external["retried"] == 1 and external["done"] == 0, external
        own = await c.get("/agent/observability/runs/" + ctx.attempt_id)
        assert own.status_code == 404, own.text
        async with OwnerSessionLocal() as session:
            state = (
                await session.execute(
                    text(
                        "SELECT state FROM agent_thread_deletions WHERE tenant_"
                        "id=:t AND thread_id=:th"
                    ),
                    {"t": ctx.tenant_id, "th": ctx.thread_id},
                )
            ).scalar_one()
            status = (
                await session.execute(
                    text(
                        "SELECT status FROM agent_outbox WHERE tenant_id=:t AND"
                        " thread_id=:th AND consumer='external_trace_cleanup'"
                    ),
                    {"t": ctx.tenant_id, "th": ctx.thread_id},
                )
            ).scalar_one()
        assert state == "completed" and status == "pending"
    finally:
        await c.aclose()
        await store.close_pool()


async def test_feedback_operation_id_race_across_two_threads(ledger_env):
    """同一操作ID跨线程并发，也只能有一次受理，不返回唯一约束500。"""
    a, da, ctxa = await fixture(ledger_env)
    b, db, ctxb = await fixture(ledger_env)
    try:
        op = "cross-thread-" + uuid.uuid4().hex
        results = await asyncio.gather(
            a.put(
                "/agent/feedback/answers/" + da["answer"]["answer_id"],
                json={"rating": "positive", "expected_revision": 0, "operation_id": op},
            ),
            b.put(
                "/agent/feedback/answers/" + db["answer"]["answer_id"],
                json={"rating": "negative", "expected_revision": 0, "operation_id": op},
            ),
        )
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        async with OwnerSessionLocal() as session:
            n = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_feedback_operations WHERE t"
                        "enant_id=:t AND operation_id=:op"
                    ),
                    {"t": ctxa.tenant_id, "op": op},
                )
            ).scalar_one()
        assert n == 1
    finally:
        await a.aclose()
        await b.aclose()


async def test_reprice_over_500_rows_keeps_currencies_and_latest_revision(ledger_env, tmp_path):
    """501条真实事实跨过批次边界；不截断、不混币、不把旧修订重复加总。"""
    c, data, ctx = await fixture(ledger_env)
    try:
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "INSERT INTO role_permissions(role_id,permission_id) SELECT "
                    "mr.role_id,p.id FROM membership_roles mr JOIN permissions "
                    "p ON p.code='agent:pricing:manage' WHERE mr.membership_id=:m "
                    "ON CONFLICT DO NOTHING"
                ),
                {"m": ctx.membership_id},
            )
            await session.commit()
        prices = {}
        for model, currency in [("bulk-usd", "USD"), ("bulk-cny", "CNY")]:
            result = await c.post(
                "/agent/observability/prices",
                json={
                    "provider": "controlled",
                    "model": model,
                    "currency": currency,
                    "input_per_unit": "2",
                    "output_per_unit": "8",
                    "effective_from": "2020-01-01T00:00:00Z",
                    "effective_to": None,
                },
            )
            assert result.status_code == 201, result.text
            prices[model] = result.json()["price_ref"]
        for index in range(501):
            await store.record_model_observation(
                ctx,
                observation_id=str(uuid.uuid4()),
                logical_call_id=str(uuid.uuid4()),
                role="main",
                provider="controlled",
                model="bulk-usd" if index < 500 else "bulk-cny",
                status="succeeded",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                },
                measurement_status="measured",
            )
        correction = await c.post(
            "/agent/observability/prices",
            json={
                "provider": "controlled",
                "model": "bulk-usd",
                "currency": "USD",
                "input_per_unit": "3",
                "output_per_unit": "8",
                "effective_from": "2020-01-01T00:00:00Z",
                "effective_to": None,
                "supersedes_price_ref": prices["bulk-usd"],
            },
        )
        assert correction.status_code == 201, correction.text
        recalculated = await c.post(
            "/agent/observability/prices/recalculate", json={"attempt_id": ctx.attempt_id}
        )
        assert recalculated.status_code == 200, recalculated.text
        assert recalculated.json() == {
            "scanned": 501,
            "cost_revisions_written": 500,
            "unchanged": 1,
        }
        again = await c.post(
            "/agent/observability/prices/recalculate", json={"attempt_id": ctx.attempt_id}
        )
        assert again.json() == {"scanned": 501, "cost_revisions_written": 0, "unchanged": 501}, (
            again.text
        )
        result = await c.get("/agent/observability/costs")
        assert result.status_code == 200, result.text
        summary = result.json()
        by_currency = {row["currency"]: Decimal(row["subtotal"]) for row in summary["by_currency"]}
        assert by_currency == {"USD": Decimal("0.0350000000"), "CNY": Decimal("0.0000600000")}, (
            summary
        )
        assert summary["total_observations"] == 501 and summary["complete_cost_known"]
        assert not summary["by_currency_truncated"]
        for grouping in ["by_user", "by_graph"]:
            assert {
                row["currency"]: Decimal(row["subtotal"]) for row in summary[grouping]
            } == by_currency
        import json

        (tmp_path / "cost-501.json").write_text(
            json.dumps(
                {
                    "recalculated": recalculated.json(),
                    "replay": again.json(),
                    "summary": summary,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        await c.aclose()
        await store.close_pool()
