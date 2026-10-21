"""协调者业务反例；合成问题/受控模型，不连接真实IAM或模型。"""

import asyncio
import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.demand_insights import classifier, jobs, source_reader, worker
from app.demand_insights.errors import InsightsUnavailable
from app.demand_insights.matching import valid_vector
from app.demand_insights.model_gateway import ModelCallFailed, ModelGateway, parse_intent_payload
from tests.demand_insights.test_db_integration import (
    FakeGateway,
    _discover_and_process,
    _seed_job,
    _seed_profile,
    _seed_request,
    _settings,
)
from tests.demand_insights.test_db_integration import (
    engines as engines,
)


def _payload():
    return {
        "intents": [
            {
                "normalized_text": "查询北京今天AQI",
                "summary": "查询空气质量",
                "action": "query",
                "object": "air_quality",
                "time_kind": "point",
                "negation": False,
                "slots": {"city": "北京"},
                "confidence": 0.9,
            }
        ]
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["intents"][0].update(negation="false"),
        lambda p: p["intents"][0].update(confidence=True),
        lambda p: p["intents"][0].update(normalized_text=""),
        lambda p: p["intents"][0].update(extra="ignore"),
        lambda p: p["intents"][0].update(normalized_text="密码: abc123456789"),
        lambda p: p.update(intents=p["intents"] * 6),
    ],
)
def test_model_output_rejects_invalid_or_sensitive_fields(mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(ModelCallFailed):
        parse_intent_payload(payload, max_intents=5)


def test_model_output_deduplicates_and_redacts():
    payload = _payload()
    payload["intents"][0]["normalized_text"] = "查询手机号13800138000的空气质量"
    payload["intents"] *= 2
    drafts = parse_intent_payload(payload, max_intents=5)
    assert len(drafts) == 1
    assert "13800138000" not in drafts[0].text
    assert drafts[0].negation is False
    assert not valid_vector([True, False], 2)
    assert not valid_vector([1e308, 1e308], 2)


async def test_retry_rechecks_authorization_before_second_send(monkeypatch):
    settings = _settings()
    sent = []
    checks = []

    async def handler(request):
        sent.append(request)
        return httpx.Response(503, json={"error": "synthetic"})

    async def guard():
        checks.append(True)
        if len(checks) > 1:
            raise InsightsUnavailable("撤权", code="authorization_revoked")

    async def no_wait(_):
        pass

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    gateway = ModelGateway(settings, guard=guard)
    with pytest.raises(InsightsUnavailable, match="撤权"):
        await gateway.embed(["查询空气质量"])
    assert len(sent) == 1
    assert len(checks) == 2


async def test_worker_context_does_not_leak_on_connection_reuse(engines):
    _, existing_worker = engines
    engine = create_async_engine(existing_worker.url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = str(uuid.uuid4())
    try:
        async with factory() as session:
            await worker._set_worker(session, tenant)
            assert (
                await session.scalar(text("SELECT current_setting('air_agent.tenant_id',true)"))
                == tenant
            )
            await session.commit()
            # 同一任务的新事务重施上下文。
            assert (
                await session.scalar(text("SELECT current_setting('air_agent.tenant_id',true)"))
                == tenant
            )
            await session.commit()
        async with factory() as session:
            assert await session.scalar(
                text("SELECT current_setting('air_agent.tenant_id',true)")
            ) in (None, "")
            assert await session.scalar(
                text("SELECT current_setting('air_agent.insight_worker',true)")
            ) in (None, "")
    finally:
        await engine.dispose()


async def test_final_lease_guard_rolls_back_classification_results(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    factory = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(factory, tenant)
    row = await _seed_request(owner, tenant=tenant, user=user, membership=member)
    job = await _seed_job(owner, tenant, user, member, profile["id"], uuid.uuid4())
    async with owner() as session:
        await session.execute(
            text(
                "UPDATE agent_insight_jobs SET state='running',lease_owner='current',"
                "fence=2,lease_deadline=now()+interval '60 seconds' WHERE id=:j"
            ),
            {"j": job},
        )
        await session.commit()
    original = classifier.process_source

    async def finalize(session, result):
        await jobs.require_live_lease(session, job_id=job, worker_id="stale", fence=1)

    # 复用确定性来源夹具，保留真实PG提交路径；只增加失效租约回包。
    async def guarded(*args, **kwargs):
        return await original(*args, **kwargs, finalize=finalize)

    from unittest.mock import patch

    with patch.object(classifier, "process_source", guarded):
        with pytest.raises(InsightsUnavailable):
            await _discover_and_process(factory, tenant, profile, row)
    async with owner() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM agent_question_intents WHERE tenant_id=:t"),
                {"t": tenant},
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM agent_question_clusters WHERE tenant_id=:t"),
                {"t": tenant},
            )
            == 0
        )

    # 合法租约为正向控制。
    async def valid(session, result):
        await jobs.require_live_lease(session, job_id=job, worker_id="current", fence=2)
        assert await jobs.update_progress(
            session,
            job_id=job,
            worker_id="current",
            fence=2,
            processed_delta=1,
            classified_delta=result.classified,
            unclassified_delta=result.unclassified,
            failed_delta=0,
            usage_tokens=result.usage_tokens,
            estimated_tokens=80,
            cursor_admitted_at=row["admitted_at"],
            cursor_request_id=row["request_id"],
        )

    async with factory() as session:
        await worker._set_worker(session, str(tenant))
        pending = await source_reader.load_retry_sources(
            session,
            tenant_id=str(tenant),
            profile_id=profile["id"],
            analysis_version=profile["analysis_version"],
            limit=1,
        )
        assert len(pending) == 1
        result = await original(
            session,
            tenant_id=str(tenant),
            profile=profile,
            source=dict(pending[0]),
            settings=_settings(),
            gateway=FakeGateway(),
            finalize=valid,
        )
        assert result.status == "processed"
    async with owner() as session:
        assert (
            await session.scalar(
                text("SELECT processed_sources FROM agent_insight_jobs WHERE id=:j"), {"j": job}
            )
            == 1
        )


@pytest.mark.parametrize("pending_first", [False, True])
async def test_batch_continues_until_all_sources_and_honors_existing_budget(
    engines, monkeypatch, pending_first
):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    factory = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(factory, tenant)
    if pending_first:
        await _seed_request(owner, tenant=tenant, user=user, membership=member, confirmed=False)
    for question in ("查北京今天PM2.5", "为什么上海污染升高"):
        await _seed_request(owner, tenant=tenant, user=user, membership=member, content=question)
    job_id = await _seed_job(owner, tenant, user, member, profile["id"], uuid.uuid4())
    settings = _settings()

    async def allowed(*args):
        return True, "synthetic_identity"

    monkeypatch.setattr(worker, "_check_delegation", allowed)
    async with owner() as session:
        await session.execute(
            text(
                "UPDATE agent_insight_jobs SET state='running',batch_size=1,"
                "lease_owner=:owner,fence=1,lease_deadline=now()+interval '120 seconds' WHERE id=:j"
            ),
            {"owner": worker._worker_id(settings), "j": job_id},
        )
        await session.commit()

    async def current():
        async with owner() as session:
            return dict(
                (
                    await session.execute(
                        text("SELECT * FROM agent_insight_jobs WHERE id=:j"), {"j": job_id}
                    )
                )
                .mappings()
                .one()
            )

    first = await worker._process_batch(factory, await current(), profile, settings, FakeGateway())
    if pending_first:
        assert first[0] == 0 and first[1] == "pending"
        first = await worker._process_batch(
            factory, await current(), profile, settings, FakeGateway()
        )
    assert first[0] == 1 and first[1] == "pending"
    second = await worker._process_batch(factory, await current(), profile, settings, FakeGateway())
    assert second[0] == 1
    third = await worker._process_batch(factory, await current(), profile, settings, FakeGateway())
    assert third[0] == 0
    assert third[1] == ("pending" if pending_first else "completed")
    assert (await current())["processed_sources"] == 2
    async with owner() as session:
        await session.execute(
            text("UPDATE agent_insight_jobs SET estimated_tokens=max_tokens WHERE id=:j"),
            {"j": job_id},
        )
        await session.commit()
    assert await worker._process_batch(
        factory, await current(), profile, settings, FakeGateway()
    ) == (0, "paused_budget", True)


async def test_expired_derived_content_is_hidden_before_cleanup(engines):
    import os

    from app.demand_insights.scope import scope_session
    from tests.demand_insights.test_db_integration import _identity, _normalize

    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    factory = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(factory, tenant)
    row = await _seed_request(owner, tenant=tenant, user=user, membership=member)
    await _discover_and_process(factory, tenant, profile, row)
    await _seed_job(owner, tenant, user, member, profile["id"], uuid.uuid4())
    engine = create_async_engine(_normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]))
    runtime = async_sessionmaker(engine, expire_on_commit=False)
    identity = _identity(
        tenant, user, member, ["agent:metrics:read", "agent:operations:read", "agent:content:read"]
    )

    async def visible():
        async with runtime() as session:
            await scope_session(
                session, identity, require_metrics=True, require_ops=True, require_content=True
            )
            return [
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": tenant}
                )
                for table in (
                    "agent_question_sources",
                    "agent_question_intents",
                    "agent_question_intent_contents",
                )
            ]

    try:
        assert await visible() == [1, 1, 1]
        async with owner() as session:
            await session.execute(
                text(
                    "UPDATE agent_question_sources SET retention_until=now()-interval '1 second' "
                    "WHERE tenant_id=:t"
                ),
                {"t": tenant},
            )
            await session.commit()
        assert await visible() == [0, 0, 0]
        # 无有效人类委托也应完成物理删除，不等待新的分析任务。
        await worker._cleanup_tenant_batch(factory)
        async with owner() as session:
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_sources WHERE tenant_id=:t"),
                    {"t": tenant},
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_zero_revisions_are_valid_but_revoked_permissions_are_not(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import common.identity.remote as remote

    from app.demand_insights import delegations

    user, tenant, member, session_id = [str(uuid.uuid4()) for _ in range(4)]
    token = "synthetic-credential-not-a-real-session"
    session_row = {
        "subject_token": token,
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "audience": "synthetic",
        "iam_session_id": session_id,
    }
    payload = {
        "principal": {"id": user},
        "session": {"id": session_id},
        "current_tenant": {"id": tenant},
        "membership": {"id": member},
        "context_version": 0,
        "revisions": {"user_auth_revision": 0, "tenant_auth_revision": 0, "membership_revision": 0},
        "permissions": list(delegations.REQUIRED_PERMISSIONS),
    }
    monkeypatch.setattr(delegations, "load_session_token", AsyncMock(return_value=session_row))
    monkeypatch.setattr(remote, "subject_payload", lambda *args, **kwargs: payload)
    delegation = {
        "id": uuid.uuid4(),
        "user_id": user,
        "tenant_id": tenant,
        "membership_id": member,
        "session_handle_hash": "synthetic",
        "status": "active",
        "expires_at": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "ctx_version": 0,
        "user_auth_revision": 0,
        "tenant_auth_revision": 0,
        "membership_revision": 0,
    }
    db = SimpleNamespace(execute=AsyncMock())
    assert (await delegations.check_delegation(db, delegation=delegation, settings=_settings())).ok
    payload["permissions"] = []
    denied = await delegations.check_delegation(db, delegation=delegation, settings=_settings())
    assert not denied.ok and "permissions" in denied.reason
