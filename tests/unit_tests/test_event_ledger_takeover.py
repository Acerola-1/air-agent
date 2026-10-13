"""协调者接管反例：服务正文权限、空列表双执行和提交映射漂移."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
import psycopg
import pytest
from langgraph_sdk import Auth
from starlette.requests import Request

from common.event_ledger import reconcile, service_read, store
from common.identity import langgraph_auth

pytestmark = pytest.mark.anyio


def _rs_token() -> str:
    payload = {
        "typ": "rs-v1",
        "tenant": str(uuid.uuid4()),
        "threads": ["thread"],
        "scope": "ledger:reconcile",
        "aud": "ledger-reconcile",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    body = (
        base64.urlsafe_b64encode(json.dumps({"kid": "k", "p": payload}).encode())
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(b"synthetic-key", body.encode(), hashlib.sha256).digest()
    return body + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/threads/thread/state"),
        ("POST", "/threads/thread/history"),
        ("GET", "/threads/thread/runs/run"),
        ("GET", "/threads/thread/runs/run/join"),
        ("POST", "/threads/thread/runs"),
        ("GET", "/assistants/assistant"),
    ],
)
async def test_service_token_cannot_bypass_projection(monkeypatch, method, path):
    monkeypatch.setenv("LEDGER_RECONCILE_KEYS", json.dumps({"k": "synthetic-key"}))
    monkeypatch.setenv("INTERNAL_API_TOKEN", "synthetic-internal")
    token = _rs_token()
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"authorization", ("Bearer " + token).encode())],
        }
    )
    with pytest.raises(Auth.exceptions.HTTPException) as error:
        await langgraph_auth.authenticate(request)
    assert error.value.status_code == 403


def test_projection_cannot_leak_added_platform_fields():
    projected = service_read.project_run(
        {
            "run_id": "r",
            "status": "success",
            "kwargs": {"input": "private-text", "token": "secret"},
            "values": "private-text",
            "future": "private-text",
            "metadata": {
                "ledger_attempt_id": "a",
                "ledger_submission_id": "s",
                "prompt": "private-text",
            },
        }
    )
    assert set(projected) == {"run_id", "status", "metadata"}
    assert projected["metadata"] == {
        "ledger_attempt_id": "a",
        "ledger_submission_id": "s",
    }
    assert "private-text" not in json.dumps(projected)


async def test_lookup_pages_and_filters_exact_submission(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []

    def respond(request):
        seen.append(int(request.url.params.get("offset", 0)))
        if seen[-1] == 0:
            return httpx.Response(
                200,
                json=[
                    {
                        "run_id": "old",
                        "metadata": {
                            "ledger_attempt_id": "a",
                            "ledger_submission_id": "old",
                        },
                    }
                ]
                * 50,
            )
        return httpx.Response(
            200,
            json=[
                {
                    "run_id": "new",
                    "metadata": {
                        "ledger_attempt_id": "a",
                        "ledger_submission_id": "new",
                    },
                }
            ],
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    probe = reconcile.HttpRunStatusProbe("http://synthetic", _rs_token())
    assert (
        await probe.locate_run(thread_id="thread", attempt_id="a", submission_id="new")
        == "new"
    )
    assert seen == [0, 50]


async def test_ambiguous_or_unbounded_lookup_cannot_prove_absence(monkeypatch):
    real_client = httpx.AsyncClient
    mode = ["ambiguous"]

    def respond(request):
        if mode[0] == "ambiguous":
            return httpx.Response(
                200,
                json=[
                    {
                        "run_id": r,
                        "metadata": {
                            "ledger_attempt_id": "a",
                            "ledger_submission_id": "s",
                        },
                    }
                    for r in ["r1", "r2"]
                ],
            )
        return httpx.Response(200, json=[{"run_id": "other", "metadata": {}}] * 50)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    probe = reconcile.HttpRunStatusProbe("http://synthetic", _rs_token())
    with pytest.raises(reconcile.PlatformUnavailable, match="ambiguous"):
        await probe.locate_run(thread_id="thread", attempt_id="a", submission_id="s")
    mode[0] = "bounded"
    with pytest.raises(reconcile.PlatformUnavailable, match="page_bound"):
        await probe.locate_run(thread_id="thread", attempt_id="a", submission_id="s")


async def test_empty_list_does_not_release_after_grace(monkeypatch):
    class EmptyProbe:
        async def locate_run(self, **kwargs):
            return None

    async def candidates(*args, **kwargs):
        return [
            {
                "attempt_id": "a",
                "thread_id": "thread",
                "state": "admission_unknown",
                "platform_run_id": None,
                "stalled_seconds": 999999,
            }
        ]

    async def record(*args, **kwargs):
        return {}

    monkeypatch.setattr(reconcile, "attempts_needing_reconcile", candidates)
    monkeypatch.setattr(reconcile, "record_reconcile_run", record)
    result = await reconcile.Reconciler(
        probe=EmptyProbe(), release_after_seconds=1
    ).reconcile_scope(store.LedgerScope("t", "u", "m"))
    assert [item.action for item in result] == ["kept_unknown"]


async def _resume_submission(attempt_id, request_id, thread_id):
    from test_event_ledger_review02_regressions import OWNER_URI, TENANT, USER

    sid = str(uuid.uuid4())
    digest = await store.content_digest(
        graph_id="basic-qa", operation_kind="resume", content=""
    )
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "INSERT INTO agent_submissions (id,tenant_id,user_id,membership_id,thread_id,"
            "submission_key,operation_kind,content_digest,request_id,attempt_id,admission_status)"
            " VALUES (%s,%s,%s,%s,%s,%s,'resume',%s,%s,%s,'admitting')",
            (sid, TENANT, USER, USER, thread_id, sid, digest, request_id, attempt_id),
        )
        await conn.commit()
    return sid


@pytest.mark.skipif(
    not os.environ.get("LEDGER_TEST_OWNER_URI"), reason="需要专用PG环境"
)
async def test_old_submission_replay_keeps_its_original_run():
    from test_event_ledger_review02_regressions import OWNER_URI, _scope, _seed_attempt

    req, aid, thread, sid = await _seed_attempt()
    scope = _scope()
    assert (
        await store.claim_start(
            scope,
            submission_id=sid,
            attempt_id=aid,
            thread_id=thread,
            graph_id="basic-qa",
            operation_kind="initial",
            content="问题",
            source="native",
        )
    )["status"] == "claimed"
    await store.attach_platform_run(
        scope, attempt_id=aid, platform_run_id="old-" + sid, submission_id=sid
    )
    await store.observe_state(
        scope, attempt_id=aid, state="waiting_input", evidence_source="platform_read"
    )
    resume = await _resume_submission(aid, req, thread)
    assert (
        await store.claim_start(
            scope,
            submission_id=resume,
            attempt_id=aid,
            thread_id=thread,
            graph_id="basic-qa",
            operation_kind="resume",
            content="",
            source="native",
        )
    )["status"] == "claimed"
    await store.record_resume(
        scope, attempt_id=aid, platform_run_id="new-" + sid, submission_id=resume
    )
    await store.observe_state(
        scope, attempt_id=aid, state="succeeded", evidence_source="platform_read"
    )
    for key, kind, content, expected in [
        (sid, "initial", "问题", "old-" + sid),
        (resume, "resume", "", "new-" + sid),
    ]:
        result = await store.claim_start(
            scope,
            submission_id=key,
            attempt_id=aid,
            thread_id=thread,
            graph_id="basic-qa",
            operation_kind=kind,
            content=content,
            source="native",
        )
        assert (
            result["status"] == "already_confirmed"
            and result["platform_run_id"] == expected
        )
    late = await store.attach_platform_run(
        scope, attempt_id=aid, platform_run_id="old-" + sid, submission_id=sid
    )
    assert late["reason"] == "stale_submission"
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        row = await (
            await conn.execute(
                "SELECT platform_run_id FROM agent_run_attempts WHERE id=%s", (aid,)
            )
        ).fetchone()
        assert row[0] == "new-" + sid


@pytest.mark.skipif(
    not os.environ.get("LEDGER_TEST_OWNER_URI"), reason="需要专用PG环境"
)
async def test_distinct_resume_submissions_cannot_both_start():
    from test_event_ledger_review02_regressions import _scope, _seed_attempt

    req, aid, thread, _ = await _seed_attempt()
    scope = _scope()
    await store.observe_state(
        scope, attempt_id=aid, state="waiting_input", evidence_source="platform_read"
    )
    keys = [await _resume_submission(aid, req, thread) for _ in range(2)]
    results = await asyncio.gather(
        *(
            store.claim_start(
                scope,
                submission_id=key,
                attempt_id=aid,
                thread_id=thread,
                graph_id="basic-qa",
                operation_kind="resume",
                content="",
                source="native",
            )
            for key in keys
        )
    )
    assert sorted(item["status"] for item in results) == ["claimed", "mismatch"]


@pytest.mark.skipif(
    not os.environ.get("LEDGER_TEST_OWNER_URI"), reason="需要专用PG环境"
)
async def test_reconcile_cursor_does_not_skip_equal_timestamps(monkeypatch):
    import test_event_ledger_review02_regressions as fixture
    from test_event_ledger_review02_regressions import OWNER_URI, _scope, _seed_attempt

    monkeypatch.setattr(fixture, "TENANT", str(uuid.uuid4()))

    aids = sorted([(await _seed_attempt())[1] for _ in range(2)])
    stamp = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    async with await psycopg.AsyncConnection.connect(OWNER_URI) as conn:
        await conn.execute(
            "UPDATE agent_run_attempts SET last_progress_at=%s WHERE id=ANY(%s::uuid[])",
            (stamp, aids),
        )
        await conn.commit()
    first = await store.attempts_needing_reconcile(_scope(), limit=1)
    assert first[0]["attempt_id"] == aids[0]
    second = await store.attempts_needing_reconcile(
        _scope(), limit=1, after_progress_at=stamp, after_attempt_id=aids[0]
    )
    assert second[0]["attempt_id"] == aids[1]


async def test_internal_status_delegation_never_restores_member_permissions(
    monkeypatch,
):
    monkeypatch.setenv("LEDGER_RECONCILE_KEYS", json.dumps({"k": "synthetic-key"}))
    monkeypatch.setenv("INTERNAL_API_TOKEN", "synthetic-internal")

    async def owner(*args, **kwargs):
        return {"user_id": "owner-for-index", "source": "native"}

    async def forbidden_member_lookup(*args, **kwargs):
        raise AssertionError("服务校准不查询或恢复原成员授权")

    monkeypatch.setattr(service_read, "authorized_owner", owner)
    monkeypatch.setattr(langgraph_auth, "fetch_identity_state", forbidden_member_lookup)
    token = _rs_token()
    path = "/threads/thread/runs/run"
    signature = service_read.projection_signature(token, path)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [
                (b"authorization", ("Bearer " + token).encode()),
                (service_read.PROJECTION_HEADER.encode(), signature.encode()),
            ],
        }
    )
    actor = await langgraph_auth.authenticate(request)
    assert actor["identity"] == "owner-for-index"
    assert actor["service_scope"] == "ledger:reconcile" and actor["permissions"] == []
    assert not actor.get("membership_id") and not actor.get("session_id")
