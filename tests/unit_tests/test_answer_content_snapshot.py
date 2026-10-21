"""确认终答正文快照：幂等、冲突、截断、删除墓碑与 RLS（专用 PG）.

    LEDGER_TEST_TENANT_ID=... LEDGER_TEST_USER_ID=... \
      LEDGER_TEST_OWNER_URI=... RUNTIME_DATABASE_URI=... \
      PYTHONPATH=src .venv/bin/python -m pytest tests/unit_tests/test_answer_content_snapshot.py -q
"""

from __future__ import annotations

import hashlib
import os
import uuid

import psycopg
import pytest

PG_READY = (
    bool(os.environ.get("RUNTIME_DATABASE_URI"))
    and bool(os.environ.get("LEDGER_TEST_TENANT_ID"))
    and bool(os.environ.get("LEDGER_TEST_OWNER_URI"))
)

pytestmark = pytest.mark.skipif(not PG_READY, reason="需要专用 PG 环境")

TENANT = os.environ.get("LEDGER_TEST_TENANT_ID", "")
USER = os.environ.get("LEDGER_TEST_USER_ID", TENANT)


def _scope():
    from common.event_ledger.store import LedgerScope

    return LedgerScope(tenant_id=TENANT, user_id=USER, membership_id=USER)


def _owner_uri() -> str:
    return os.environ["LEDGER_TEST_OWNER_URI"]


async def _seed_answer(thread_id: str | None = None) -> dict[str, str]:
    request_id, attempt_id, answer_id = (
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    thread_id = thread_id or f"c6-{request_id[:8]}"
    async with await psycopg.AsyncConnection.connect(_owner_uri()) as conn:
        await conn.execute(
            "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id,"
            " graph_id, operation_kind, admitted_at, created_at)"
            " VALUES (%s,%s,%s,%s,%s,'basic-qa','initial',now(),now())",
            (request_id, TENANT, USER, USER, thread_id),
        )
        await conn.execute(
            "INSERT INTO agent_run_attempts (id, tenant_id, user_id, membership_id, request_id,"
            " attempt_index, graph_id, state, state_revision, admitted_at, last_progress_at,"
            " updated_at) VALUES (%s,%s,%s,%s,%s,1,'basic-qa','succeeded',1,now(),now(),now())",
            (attempt_id, TENANT, USER, USER, request_id),
        )
        await conn.execute(
            "INSERT INTO agent_answers (id, tenant_id, user_id, attempt_id, message_id,"
            " content_version, partial, confirmed, confirmed_at, created_at)"
            " VALUES (%s,%s,%s,%s,%s,1,false,true,now(),now())",
            (answer_id, TENANT, USER, attempt_id, f"msg-{answer_id[:8]}"),
        )
        await conn.commit()
    return {
        "request_id": request_id,
        "attempt_id": attempt_id,
        "answer_id": answer_id,
        "thread_id": thread_id,
    }


async def _delete_thread(thread_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(_owner_uri()) as conn:
        await conn.execute(
            "INSERT INTO agent_thread_deletions"
            " (id, tenant_id, thread_id, user_id, state, attempts, requested_at)"
            " VALUES (gen_random_uuid(),%s,%s,%s,'requested',0,now())"
            " ON CONFLICT (tenant_id,thread_id) DO NOTHING",
            (TENANT, thread_id, USER),
        )
        await conn.commit()


async def _fetch_snapshot(answer_id: str, version: int):
    async with await psycopg.AsyncConnection.connect(_owner_uri()) as conn:
        cur = await conn.execute(
            "SELECT content_sha256, content_text, total_chars, truncated, capture_status"
            " FROM agent_answer_contents WHERE tenant_id=%s AND answer_id=%s"
            " AND content_version=%s",
            (TENANT, answer_id, version),
        )
        return await cur.fetchone()


@pytest.mark.anyio
async def test_capture_is_idempotent_and_rejects_same_version_conflict() -> None:
    from common.event_ledger.answer_content import capture_answer_content

    data = await _seed_answer()
    first = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=f"msg-{data['answer_id'][:8]}",
        content_version=1,
        text_value="最终回答正文",
    )
    assert first["captured"] is True
    assert first["replayed"] is False

    replay = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=f"msg-{data['answer_id'][:8]}",
        content_version=1,
        text_value="最终回答正文",
    )
    assert replay["captured"] is True
    assert replay["replayed"] is True

    conflict = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=f"msg-{data['answer_id'][:8]}",
        content_version=1,
        text_value="不同正文",
    )
    assert conflict["captured"] is False
    assert conflict["reason"] == "content_conflict"

    row = await _fetch_snapshot(data["answer_id"], 1)
    assert row[1] == "最终回答正文"
    assert row[0] == hashlib.sha256("最终回答正文".encode()).hexdigest()


@pytest.mark.anyio
async def test_long_answer_is_truncated_not_claimed_complete() -> None:
    from common.event_ledger.answer_content import capture_answer_content

    data = await _seed_answer()
    text_value = "字" * 70000
    result = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=None,
        content_version=1,
        text_value=text_value,
    )
    assert result["captured"] is True
    assert result["truncated"] is True
    assert result["total_chars"] == 70000
    row = await _fetch_snapshot(data["answer_id"], 1)
    assert row[2] == 70000
    assert row[3] is True
    assert len(row[1]) == 65536


@pytest.mark.anyio
async def test_capture_failed_is_visible_without_overwriting_success() -> None:
    from common.event_ledger.answer_content import (
        capture_answer_content,
        mark_capture_failed,
    )

    data = await _seed_answer()
    failed = await mark_capture_failed(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        content_version=1,
        reason="test_failure",
    )
    assert failed["recorded"] is True
    row = await _fetch_snapshot(data["answer_id"], 1)
    assert row[4] == "capture_failed"
    repaired = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=None,
        content_version=1,
        text_value="恢复采集",
    )
    assert repaired["captured"] is True
    assert (await _fetch_snapshot(data["answer_id"], 1))[1] == "恢复采集"

    # 已成功快照不被失败标记覆盖
    other = await _seed_answer()
    await capture_answer_content(
        _scope(),
        attempt_id=other["attempt_id"],
        request_id=other["request_id"],
        answer_id=other["answer_id"],
        message_id=None,
        content_version=1,
        text_value="成功正文",
    )
    retry = await mark_capture_failed(
        _scope(),
        attempt_id=other["attempt_id"],
        request_id=other["request_id"],
        answer_id=other["answer_id"],
        content_version=1,
        reason="late_failure",
    )
    assert retry["recorded"] is False
    still = await _fetch_snapshot(other["answer_id"], 1)
    assert still[4] == "captured"
    assert still[1] == "成功正文"


@pytest.mark.anyio
async def test_deleted_thread_rejects_late_snapshot_and_hides_rows() -> None:
    from common.event_ledger.answer_content import capture_answer_content

    data = await _seed_answer()
    await _delete_thread(data["thread_id"])
    late = await capture_answer_content(
        _scope(),
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=None,
        content_version=1,
        text_value="晚到正文",
    )
    assert late["captured"] is False
    assert late["reason"] == "answer_not_confirmed_or_deleted"
    async with await psycopg.AsyncConnection.connect(_owner_uri()) as conn:
        count = await (
            await conn.execute(
                "SELECT count(*) FROM agent_answer_contents WHERE tenant_id=%s"
                " AND answer_id=%s",
                (TENANT, data["answer_id"]),
            )
        ).fetchone()
    assert count[0] == 0

    # RLS：无 user/ops/content GUC 的运行角色看不到本企业快照。
    live = await _seed_answer()
    await capture_answer_content(
        _scope(),
        attempt_id=live["attempt_id"],
        request_id=live["request_id"],
        answer_id=live["answer_id"],
        message_id=None,
        content_version=1,
        text_value="可见正文",
    )
    async with await psycopg.AsyncConnection.connect(
        os.environ["RUNTIME_DATABASE_URI"]
    ) as conn:
        await conn.execute(
            "SELECT set_config('air_agent.tenant_id', %s, true)", (TENANT,)
        )
        hidden = await (
            await conn.execute(
                "SELECT count(*) FROM agent_answer_contents WHERE tenant_id=%s",
                (TENANT,),
            )
        ).fetchone()
        assert hidden[0] == 0
        await conn.execute("SELECT set_config('air_agent.user_id', %s, true)", (USER,))
        visible = await (
            await conn.execute(
                "SELECT count(*) FROM agent_answer_contents WHERE tenant_id=%s"
                " AND answer_id=%s",
                (TENANT, live["answer_id"]),
            )
        ).fetchone()
        assert visible[0] == 1
        await conn.rollback()


@pytest.mark.anyio
async def test_snapshot_rejects_wrong_lineage_and_expired_answer() -> None:
    from common.event_ledger.answer_content import capture_answer_content

    data = await _seed_answer()
    arguments = dict(
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        answer_id=data["answer_id"],
        message_id=None,
        content_version=1,
        text_value="final-answer",
    )
    wrong = await capture_answer_content(
        _scope(), **(arguments | {"attempt_id": str(uuid.uuid4())})
    )
    assert wrong["captured"] is False
    assert await _fetch_snapshot(data["answer_id"], 1) is None
    async with await psycopg.AsyncConnection.connect(_owner_uri()) as conn:
        await conn.execute(
            "UPDATE agent_answers SET confirmed_at=now()-interval '91 days' WHERE id=%s",
            (data["answer_id"],),
        )
    expired = await capture_answer_content(_scope(), **arguments)
    assert expired["captured"] is False
    assert await _fetch_snapshot(data["answer_id"], 1) is None


def test_invalid_retention_policy_cannot_extend_content_lifetime(monkeypatch) -> None:
    from common.event_ledger.answer_content import _retention_days

    monkeypatch.setenv("LEDGER_CONTENT_RETENTION_DAYS", "14")
    monkeypatch.delenv("LEDGER_CONTENT_RETENTION_DAYS_BY_TENANT", raising=False)
    assert _retention_days(TENANT) == 14
    monkeypatch.setenv("LEDGER_CONTENT_RETENTION_DAYS", "-1")
    with pytest.raises(ValueError):
        _retention_days(TENANT)
    monkeypatch.setenv("LEDGER_CONTENT_RETENTION_DAYS", "14")
    monkeypatch.setenv("LEDGER_CONTENT_RETENTION_DAYS_BY_TENANT", '{"bad":')
    with pytest.raises(ValueError):
        _retention_days(TENANT)
