"""协调者独立反例：正文、实际受理计数与元数据隐私。仅用自有测试库。"""

import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from sqlalchemy import text

from app.db import OwnerSessionLocal
from tests.test_console_api import _account_of, _make_member, _seed_answer_content
from tests.test_event_ledger_api import (
    _grant_thread,
    _login,
)
from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_feedback_api import _make_confirmed_answer


async def _answer(env):
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    data = await _make_confirmed_answer(client, env, thread, str(uuid.uuid4()))
    return client, data


async def test_original_question_positive_read(ledger_env):
    client, data = await _answer(ledger_env)
    async with OwnerSessionLocal() as session:
        await session.execute(
            text("""
            INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,
                request_id,content_version,content_snapshot,created_at)
            SELECT gen_random_uuid(),tenant_id,user_id,membership_id,thread_id,id,1,
                'original-question-positive-control',now() FROM agent_requests WHERE id=:r
        """),
            {"r": data["request_id"]},
        )
        await session.commit()
    try:
        response = await client.get(f"/agent/console/requests/{data['request_id']}/content")
        assert response.status_code == 200
        assert response.json()["status"] == "available"
        assert response.json()["text"]
    finally:
        await client.aclose()


async def test_expired_answer_must_not_return_summary(ledger_env):
    client, data = await _answer(ledger_env)
    marker = "expired-private-body"
    await _seed_answer_content(ledger_env["tenant_a"], ledger_env["user_a"], data, marker)
    async with OwnerSessionLocal() as session:
        await session.execute(
            text("UPDATE agent_answer_contents SET retention_until=:t WHERE answer_id=:a"),
            {"t": dt.datetime.now(dt.UTC) - dt.timedelta(days=1), "a": data["answer"]["answer_id"]},
        )
        await session.commit()
    try:
        response = await client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "expired"
        assert marker not in response.text
        detail = await client.get(f"/agent/console/runs/{data['attempt_id']}")
        assert detail.status_code == 200
        assert marker not in detail.text
    finally:
        await client.aclose()


async def test_unconfirmed_reservation_is_not_a_question(ledger_env):
    client, data = await _answer(ledger_env)
    try:
        before = (await client.get("/agent/console/overview")).json()["requests"]["questions"][
            "value"
        ]
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_submissions SET admission_status='admission_unknown' "
                    "WHERE request_id=:r"
                ),
                {"r": data["request_id"]},
            )
            await session.commit()
        response = await client.get("/agent/console/overview")
        assert response.status_code == 200
        assert response.json()["requests"]["questions"]["value"] == before - 1
    finally:
        await client.aclose()


async def test_cross_member_metadata_does_not_require_body_permission(ledger_env):
    owner, data = await _answer(ledger_env)
    await _seed_answer_content(ledger_env["tenant_a"], ledger_env["user_a"], data, "private-body")
    _, stats = await _make_member(ledger_env["tenant_a"], ["agent:metrics:read"])
    ops = await _login(await _account_of(ledger_env["user_b"]), ledger_env["tenant_a"])
    try:
        overview = await stats.get("/agent/console/overview")
        assert overview.status_code == 200, overview.text
        assert overview.json()["content"]["captured"]["value"] == 1
        detail = await ops.get(f"/agent/console/runs/{data['attempt_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["answers"][0]["availability"] == "available"
        assert "private-body" not in detail.text
        forbidden = await ops.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1},
        )
        assert forbidden.status_code == 403
    finally:
        await owner.aclose()
        await stats.aclose()
        await ops.aclose()


async def test_business_filters_and_unknown_filters(ledger_env):
    client, _ = await _answer(ledger_env)
    try:
        for endpoint in ("overview", "performance", "costs"):
            response = await client.get(
                f"/agent/console/{endpoint}", params={"business_code": "basic-qa"}
            )
            assert response.status_code == 200, response.text
        for endpoint in ("overview", "users", "performance"):
            response = await client.get(f"/agent/console/{endpoint}", params={"unrecognized": "x"})
            assert response.status_code == 422
        empty = await client.get("/agent/console/overview", params={"graph_id": "missing-graph"})
        assert empty.status_code == 200
        assert empty.json()["requests"]["questions"]["value"] == 0
    finally:
        await client.aclose()


async def test_runtime_metadata_is_body_free_immutable_and_cleanup_scoped(ledger_env):
    client, data = await _answer(ledger_env)
    await _seed_answer_content(ledger_env["tenant_a"], ledger_env["user_a"], data, "hidden-marker")
    answer_id = data["answer"]["answer_id"]
    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ["ADMIN_RUNTIME_DATABASE_URI"]
        ) as conn:
            await conn.execute(
                "SELECT set_config('air_agent.tenant_id',%s,true),"
                "set_config('air_agent.user_id',%s,true),"
                "set_config('air_agent.agent_operations','on',true)",
                (str(ledger_env["tenant_a"]), str(ledger_env["user_b"])),
            )
            assert (
                await (
                    await conn.execute(
                        "SELECT count(*) FROM agent_answer_contents WHERE answer_id=%s",
                        (answer_id,),
                    )
                ).fetchone()
            )[0] == 0
            metadata = await (
                await conn.execute(
                    "SELECT to_jsonb(m)::text FROM agent_answer_content_metadata m "
                    "WHERE answer_id=%s",
                    (answer_id,),
                )
            ).fetchone()
            assert metadata is not None and "hidden-marker" not in metadata[0]
            changed = await conn.execute(
                "UPDATE agent_answer_content_metadata SET capture_status='capture_failed' "
                "WHERE answer_id=%s",
                (answer_id,),
            )
            assert changed.rowcount == 0
            deleted = await conn.execute(
                "DELETE FROM agent_answer_contents WHERE answer_id=%s", (answer_id,)
            )
            assert deleted.rowcount == 0
            await conn.execute("SELECT set_config('air_agent.deletion_cleanup','on',true)")
            # 与worker一致：清理范围还必须绑定墓碑的实际归属人。
            await conn.execute(
                "SELECT set_config('air_agent.user_id',%s,true)",
                (str(ledger_env["user_a"]),),
            )
            deleted = await conn.execute(
                "DELETE FROM agent_answer_contents WHERE answer_id=%s", (answer_id,)
            )
            assert deleted.rowcount == 1
            assert (
                await (
                    await conn.execute(
                        "SELECT count(*) FROM agent_answer_content_metadata WHERE answer_id=%s",
                        (answer_id,),
                    )
                ).fetchone()
            )[0] == 0
    finally:
        await client.aclose()


async def test_snapshot_rejects_same_tenant_forged_thread(ledger_env):
    client, data = await _answer(ledger_env)
    await _seed_answer_content(ledger_env["tenant_a"], ledger_env["user_a"], data, "positive")
    try:
        async with OwnerSessionLocal() as session:
            with pytest.raises(Exception) as failure:
                await session.execute(
                    text(
                        "UPDATE agent_answer_contents SET thread_id=:thread WHERE answer_id=:answer"
                    ),
                    {"thread": str(uuid.uuid4()), "answer": data["answer"]["answer_id"]},
                )
            assert "console source mismatch" in str(failure.value)
            await session.rollback()
    finally:
        await client.aclose()


async def test_uncollected_feedback_is_not_reported_as_zero(ledger_env, monkeypatch):
    from app.config import get_settings

    client, _ = await _answer(ledger_env)
    monkeypatch.setattr(get_settings(), "agent_interactions_enabled", False)
    try:
        response = await client.get("/agent/console/overview")
        assert response.status_code == 200, response.text
        for key in ("positive", "negative", "copy_events", "copy_rate", "rating_coverage"):
            metric = response.json()["feedback"][key]
            assert metric["value"] is None
            assert metric["status"] == "not_collected"
            assert metric["not_collected_count"] == 1
    finally:
        await client.aclose()


async def test_browser_timing_dedup_and_graph_filter(ledger_env):
    from app.services.event_contract import canonical_digest

    client, data = await _answer(ledger_env)
    try:
        async with OwnerSessionLocal() as session:
            for measurement, first, complete in [
                ("one", 100, 200),
                ("one", 100, 200),
                ("bad", 100, 50),
            ]:
                payload = dict(
                    attempt_id=data["attempt_id"],
                    measurement_id=measurement,
                    first_visible_answer_ms=first,
                    display_complete_ms=complete,
                    clock="monotonic",
                    completion_state="complete",
                )
                await session.execute(
                    text("""
                    INSERT INTO agent_events(event_id,tenant_id,user_id,membership_id,
                        event_type,source,occurred_at,received_at,thread_id,request_id,
                        attempt_id,payload,payload_digest,created_at)
                    SELECT gen_random_uuid(),tenant_id,user_id,membership_id,
                        'browser.timing','browser',now(),now(),thread_id,id,
                        :attempt,CAST(:payload AS jsonb),:digest,now()
                    FROM agent_requests WHERE id=:request
                """),
                    dict(
                        attempt=data["attempt_id"],
                        request=data["request_id"],
                        payload=json.dumps(payload),
                        digest=canonical_digest(payload),
                    ),
                )
            await session.commit()
        response = await client.get("/agent/console/performance", params={"graph_id": "basic-qa"})
        assert response.status_code == 200, response.text
        browser = response.json()["partitions"]["browser"]
        assert browser["sample_count"] == 1
        assert browser["p95_ms"] == 100
        assert browser["invalid_count"] == 1
        assert browser["display_complete"]["p95_ms"] == 200
        excluded = await client.get(
            "/agent/console/performance", params={"graph_id": "data-analysis"}
        )
        assert excluded.status_code == 200, excluded.text
        assert excluded.json()["partitions"]["browser"]["sample_count"] == 0
    finally:
        await client.aclose()


async def test_console_whole_request_has_bounded_deadline(ledger_env, monkeypatch):
    import asyncio

    from app.routers import console

    client, _ = await _answer(ledger_env)
    monkeypatch.setattr(console, "CONSOLE_REQUEST_TIMEOUT_SECONDS", 0.05)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(console.metrics, "overview", slow)
    try:
        response = await client.get("/agent/console/overview")
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "query_timeout"
    finally:
        await client.aclose()


async def test_historical_comment_ref_remains_exact(ledger_env):
    from tests.test_console_api import _seed_feedback

    client, data = await _answer(ledger_env)
    try:
        old_ref = await _seed_feedback(
            ledger_env["tenant_a"], ledger_env["user_a"], data, comment="历史意见"
        )
        await _seed_feedback(
            ledger_env["tenant_a"], ledger_env["user_a"], data, revision=2, comment="当前意见"
        )
        path = f"/agent/console/feedback/{data['answer']['answer_id']}/content"
        current = await client.get(path, params={"kind": "comment"})
        assert current.status_code == 200, current.text
        assert current.json()["text"] == "当前意见"
        old = await client.get(path, params={"kind": "comment", "ref": old_ref})
        assert old.status_code == 200, old.text
        assert old.json()["text"] == "历史意见"
        wrong = await client.get(path, params={"kind": "comment", "ref": str(uuid.uuid4())})
        assert wrong.status_code == 404
    finally:
        await client.aclose()


async def test_tool_retry_pagination_does_not_skip_attempts(ledger_env):
    client, data = await _answer(ledger_env)
    logical = uuid.uuid4()
    physical = [uuid.uuid4() for _ in range(3)]
    try:
        async with OwnerSessionLocal() as session:
            await session.execute(
                text("""
                INSERT INTO agent_tool_calls(id,tenant_id,user_id,attempt_id,logical_call_id,
                    tool_name,service_key,started_at)
                VALUES(gen_random_uuid(),:t,:u,:a,:l,'pagination-probe','c6-local',now())
            """),
                {
                    "t": ledger_env["tenant_a"],
                    "u": ledger_env["user_a"],
                    "a": data["attempt_id"],
                    "l": logical,
                },
            )
            for index, physical_id in enumerate(physical):
                await session.execute(
                    text("""
                    INSERT INTO agent_tool_call_attempts(id,tenant_id,user_id,attempt_id,
                        logical_call_id,observation_id,retry_index,status,network_attempted)
                    VALUES(:id,:t,:u,:a,:l,gen_random_uuid(),:i,'succeeded',true)
                """),
                    {
                        "id": physical_id,
                        "t": ledger_env["tenant_a"],
                        "u": ledger_env["user_a"],
                        "a": data["attempt_id"],
                        "l": logical,
                        "i": index,
                    },
                )
            await session.commit()
        cursor = None
        seen = []
        for _ in range(3):
            params = {"kind": "tool_call", "tool_id": "pagination-probe", "limit": 1}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/agent/console/capabilities/details", params=params)
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body["items"]) == 1
            seen.append(body["items"][0]["id"])
            cursor = body["next_cursor"]
        assert set(seen) == set(map(str, physical))
        assert cursor is None
    finally:
        await client.aclose()


async def test_case_history_signed_cursor_and_old_note(ledger_env):
    from tests.test_console_api import _action_body, _seed_feedback

    owner, data = await _answer(ledger_env)
    _, manager = await _make_member(
        ledger_env["tenant_a"],
        ["agent:operations:read", "agent:content:read", "agent:feedback:manage"],
    )
    answer_id = data["answer"]["answer_id"]
    base = f"/agent/console/feedback/{answer_id}"
    try:
        await _seed_feedback(ledger_env["tenant_a"], ledger_env["user_a"], data)
        first = await manager.post(
            base + "/actions", json=_action_body(action="start", note="旧说明")
        )
        assert first.status_code == 200, first.text
        second = await manager.post(
            base + "/actions",
            json=_action_body(action="resolve", expected_revision=1, note="处理结果"),
        )
        assert second.status_code == 200, second.text
        history = await manager.get(base + "/history", params={"kind": "case", "limit": 1})
        assert history.status_code == 200, history.text
        cursor = history.json()["next_cursor"]
        assert cursor and "." in cursor
        page2 = await manager.get(
            base + "/history", params={"kind": "case", "limit": 1, "cursor": cursor}
        )
        assert page2.status_code == 200, page2.text
        assert page2.json()["items"][0]["id"] != history.json()["items"][0]["id"]
        assert page2.json()["meta"]["as_of"] == history.json()["meta"]["as_of"]
        for params in ({"kind": "rating", "cursor": cursor}, {"kind": "case", "cursor": "1"}):
            rejected = await manager.get(base + "/history", params=params)
            assert rejected.status_code == 400
        ref = first.json()["note_ref"]
        async with OwnerSessionLocal() as session:
            # 52 newer notes put the original beyond the default reference-list page.
            await session.execute(
                text("""
                INSERT INTO agent_feedback_case_notes(id,tenant_id,case_id,answer_id,
                    user_id,thread_id,actor_user_id,note_text,created_at,retention_until)
                SELECT gen_random_uuid(),n.tenant_id,n.case_id,n.answer_id,n.user_id,
                    n.thread_id,n.actor_user_id,'较新说明',now(),n.retention_until
                FROM agent_feedback_case_notes n CROSS JOIN generate_series(1,52)
                WHERE n.id=:ref
            """),
                {"ref": ref},
            )
            await session.commit()
        note = await manager.get(base + "/content", params={"kind": "note", "ref": ref})
        assert note.status_code == 200, note.text
        assert note.json()["text"] == "旧说明"
    finally:
        await owner.aclose()
        await manager.aclose()


async def test_read_releases_identity_connection_before_snapshot(ledger_env, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as db
    from app.config import get_settings
    from app.routers import console

    client, _ = await _answer(ledger_env)
    engine = create_async_engine(
        get_settings().runtime_sqlalchemy_uri, pool_size=1, max_overflow=0, pool_timeout=0.5
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(console, "SessionLocal", factory)
    try:
        response = await client.get("/agent/console/overview")
        assert response.status_code == 200, response.text
        assert response.json()["requests"]["questions"]["value"] >= 1
    finally:
        await client.aclose()
        await engine.dispose()
