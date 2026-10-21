"""真实 PG/RLS/worker 集成测试（自有空库；缺环境时显式跳过）.

运行前置（不连接正式 15432）：
    INSIGHTS_TEST_OWNER_URI=postgresql://air_agent:...@127.0.0.1:15433/air_agent_insights_test
    ADMIN_RUNTIME_DATABASE_URI=postgresql://air_agent_runtime:...@127.0.0.1:15433/...
    INSIGHTS_TEST_WORKER_URI=postgresql://air_agent_insights_worker:...@127.0.0.1:15433/...
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.demand_insights import (
    classifier,
    governance,
    jobs,
    privacy,
    source_reader,
    statistics,
    storage,
)
from app.demand_insights.config import InsightsSettings
from app.demand_insights.model_gateway import EmbeddingResult
from app.demand_insights.scope import parse_window, previous_window, scope_session

OWNER_URI = os.environ.get("INSIGHTS_TEST_OWNER_URI", "")
WORKER_URI = os.environ.get("INSIGHTS_TEST_WORKER_URI", "")

pytestmark = pytest.mark.skipif(
    not OWNER_URI or not WORKER_URI, reason="需要自有空 PG 与 worker 角色"
)


def _normalize(uri: str) -> str:
    return (
        uri.replace("postgresql://", "postgresql+psycopg://", 1)
        if uri.startswith("postgresql://")
        else uri
    )


def _settings() -> InsightsSettings:
    return InsightsSettings(
        embedding_model="fake",
        embedding_dimension=8,
        embedding_profile="fake",
        embedding_api_key="fake",
        embedding_base_url="http://127.0.0.1:1",
        internal_api_token="test-token",
        max_input_chars=8000,
    )


class FakeGateway:
    async def embed(self, texts):
        vectors = []
        for text_value in texts:
            vector = [0.0] * 8
            if "为什么" in text_value:
                vector[2] = 1.0
            elif "预测" in text_value or "明天" in text_value:
                vector[3] = 1.0
            elif "建议" in text_value or "注意" in text_value:
                vector[4] = 1.0
            elif "趋势" in text_value or "近7天" in text_value:
                vector[1] = 1.0
            else:
                vector[0] = 1.0
            vector[5] = 1.0
            vectors.append(tuple(vector))
        return EmbeddingResult(tuple(vectors), 10, "fake", "fake", 8)

    async def split_intents(self, *args, **kwargs):
        from app.demand_insights.model_gateway import ModelNotConfigured

        raise ModelNotConfigured()

    async def review_match(self, *args, **kwargs):
        from app.demand_insights.model_gateway import ModelNotConfigured

        raise ModelNotConfigured()


@pytest.fixture(scope="module")
async def engines():
    owner = create_async_engine(_normalize(OWNER_URI), pool_pre_ping=True)
    worker = create_async_engine(_normalize(WORKER_URI), pool_pre_ping=True)
    yield owner, worker
    await owner.dispose()
    await worker.dispose()


async def _guc(session, *, tenant=None, user=None, worker=False):
    if worker:
        from app.demand_insights.worker import _set_worker

        await _set_worker(session, str(tenant) if tenant else None)
        return
    if tenant:
        await session.execute(
            text("select set_config('air_agent.tenant_id',:t,false)"), {"t": str(tenant)}
        )
    if user:
        await session.execute(
            text("select set_config('air_agent.user_id',:u,false)"), {"u": str(user)}
        )
    await session.execute(text("select set_config('air_agent.agent_operations','on',false)"))


async def _seed_request(
    owner,
    *,
    tenant,
    user,
    membership,
    content="查北京今天PM2.5",
    confirmed=True,
    content_present=True,
    deleted=False,
    thread=None,
):
    thread = thread or f"t-{uuid.uuid4().hex}"
    request_id, submission_id, ref_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    async with owner() as session:
        await session.execute(
            text(
                "INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,"
                "operation_kind,role_snapshot,identity_revision,admitted_at,created_at)"
                " VALUES (:id,:t,:u,:m,:th,'basic-qa','initial','[]'::jsonb,'{}'::jsonb,:now,:now)"
            ),
            {
                "id": request_id,
                "t": tenant,
                "u": user,
                "m": membership,
                "th": thread,
                "now": now,
            },
        )
        await session.execute(
            text(
                "INSERT INTO agent_submissions(id,tenant_id,user_id,membership_id,thread_id,"
                "submission_key,operation_kind,content_digest,admission_status,request_id,"
                "digest_version,created_at,updated_at)"
                " VALUES (:id,:t,:u,:m,:th,:key,'initial','d',:status,:req,1,:now,:now)"
            ),
            {
                "id": submission_id,
                "t": tenant,
                "u": user,
                "m": membership,
                "th": thread,
                "key": uuid.uuid4().hex,
                "status": "confirmed" if confirmed else "admitting",
                "req": request_id,
                "now": now,
            },
        )
        if content_present:
            await session.execute(
                text(
                    "INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,"
                    "request_id,message_id,content_version,content_snapshot,retention_until,"
                    "deleted_at,created_at)"
                    " VALUES (:id,:t,:u,:m,:th,:req,'m1',1,:snap,:ret,:deleted,:now)"
                ),
                {
                    "id": ref_id,
                    "t": tenant,
                    "u": user,
                    "m": membership,
                    "th": thread,
                    "req": request_id,
                    "snap": content,
                    "ret": now + dt.timedelta(days=30),
                    "deleted": now if deleted else None,
                    "now": now,
                },
            )
        await session.commit()
    return {
        "tenant": tenant,
        "user": user,
        "membership": membership,
        "thread": thread,
        "request_id": request_id,
        "submission_id": submission_id,
        "ref_id": ref_id,
        "admitted_at": now,
    }


async def _seed_profile(worker, tenant):
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        state = await storage.ensure_default_profile(
            session,
            tenant_id=str(tenant),
            settings=_settings(),
            embedding_override={"profile": "fake", "model": "fake", "dimension": 8},
        )
        await session.commit()
        return state


async def _discover_and_process(worker, tenant, profile, request_row):
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        rows = await source_reader.discover_requests(
            session,
            tenant_id=str(tenant),
            profile_id=profile["active_profile_id"],
            analysis_version=profile["analysis_version"],
            window_start=request_row["admitted_at"] - dt.timedelta(days=1),
            window_end=request_row["admitted_at"] + dt.timedelta(days=1),
            cursor_admitted_at=None,
            cursor_request_id=None,
            limit=10,
        )
        assert rows
        source, reason = source_reader.classify_discovered(rows[0], now=dt.datetime.now(dt.UTC))
        assert source is not None, reason
        source_id = await source_reader.save_source(
            session,
            tenant_id=str(tenant),
            profile_id=profile["active_profile_id"],
            analysis_version=profile["analysis_version"],
            source=source,
        )
        await session.commit()
        source_row = dict(rows[0])
        source_row["id"] = source_id
        source_row["source_content_version"] = source.content_version
        result = await classifier.process_source(
            session,
            tenant_id=str(tenant),
            profile=profile,
            source=source_row,
            settings=_settings(),
            gateway=FakeGateway(),
        )
        await session.commit()
        return result


def _identity(tenant, user, membership, permissions):
    return SimpleNamespace(
        user=SimpleNamespace(id=user, account="tester", auth_revision=1),
        tenant=SimpleNamespace(id=tenant, auth_revision=1),
        membership=SimpleNamespace(id=membership, revision=1),
        session=SimpleNamespace(id=uuid.uuid4(), ctx_version=1),
        permissions=frozenset(permissions),
        tenant_status="active",
        membership_status="active",
        context_version=1,
    )


async def test_runtime_rls_isolates_tenants_and_content(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    profile_a = await _seed_profile(worker, tenant_a)
    profile_b = await _seed_profile(worker, tenant_b)
    row_a = await _seed_request(
        owner, tenant=tenant_a, user=user_a, membership=uuid.uuid4(), content="查北京今天PM2.5"
    )
    row_b = await _seed_request(
        owner, tenant=tenant_b, user=user_b, membership=uuid.uuid4(), content="查上海今天AQI"
    )
    await _discover_and_process(worker, tenant_a, profile_a, row_a)
    await _discover_and_process(worker, tenant_b, profile_b, row_b)

    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    try:
        async with factory() as session:
            identity = _identity(
                tenant_a,
                user_a,
                uuid.uuid4(),
                {"agent:metrics:read", "agent:operations:read", "agent:content:read"},
            )
            await scope_session(
                session, identity, require_metrics=True, require_ops=True, require_content=True
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_sources WHERE tenant_id=:t"),
                    {"t": tenant_a},
                )
                == 1
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_sources WHERE tenant_id=:t"),
                    {"t": tenant_b},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_intent_contents WHERE tenant_id=:t"),
                    {"t": tenant_a},
                )
                == 1
            )
            await session.rollback()
        async with factory() as session:
            identity = _identity(tenant_a, user_a, uuid.uuid4(), {"agent:metrics:read"})
            await scope_session(session, identity, require_metrics=True)
            # metrics 可读聚合元数据，但不能读原文/向量内容
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_intents WHERE tenant_id=:t"),
                    {"t": tenant_a},
                )
                == 1
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM agent_question_intent_contents WHERE tenant_id=:t"),
                    {"t": tenant_a},
                )
                == 0
            )
            await session.rollback()
    finally:
        await runtime.dispose()


async def test_classification_idempotent_and_statistics(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查北京今天PM2.5"
    )
    result = await _discover_and_process(worker, tenant, profile, row)
    assert result.status == "processed" and result.classified == 1
    # 同一来源再处理一次：有效归属仍只有一条，不重复计数
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        source_row = (
            (
                await session.execute(
                    text(
                        "SELECT id, request_id, user_id, membership_id, thread_id, graph_id,"
                        " business_code, admitted_at, source_message_id, source_content_version,"
                        " retention_until FROM agent_question_sources WHERE tenant_id=:t"
                    ),
                    {"t": tenant},
                )
            )
            .mappings()
            .first()
        )
        await classifier.process_source(
            session,
            tenant_id=str(tenant),
            profile=profile,
            source=dict(source_row),
            settings=_settings(),
            gateway=FakeGateway(),
        )
        await session.commit()
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    try:
        async with factory() as session:
            identity = _identity(
                tenant,
                user,
                membership,
                {"agent:metrics:read", "agent:operations:read", "agent:content:read"},
            )
            await scope_session(session, identity, require_metrics=True)
            window = parse_window(
                window="last_7_days",
                start=None,
                end=None,
                as_of=dt.datetime.now(dt.UTC),
            )
            counts = await statistics.load_counts(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                window=window,
            )
            assert counts["eligible_requests"] == 1
            assert counts["processed_requests"] == 1
            assert counts["classified_intents"] == 1
            assert counts["unclassified_intents"] == 0
            ranking = await statistics.question_ranking(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                assignment_revision=int(profile["assignment_revision"]),
                context_version=1,
                filter_key="f",
                window=window,
                previous=None,
                rank="intents",
                total_intents=1,
                settings=_settings(),
                cursor_key=None,
                limit=50,
                filters={},
            )
            assert ranking["items"] and ranking["items"][0]["atomic_intent_count"] == 1
            assert ranking["items"][0]["distinct_requests"] == 1
            await session.rollback()
    finally:
        await runtime.dispose()


async def test_late_confirmation_gap_rediscovered(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    row = await _seed_request(
        owner,
        tenant=tenant,
        user=uuid.uuid4(),
        membership=uuid.uuid4(),
        content="查广州今天O3",
        confirmed=False,
    )
    profile = await _seed_profile(worker, tenant)
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        discovered = await source_reader.discover_requests(
            session,
            tenant_id=str(tenant),
            profile_id=profile["active_profile_id"],
            analysis_version=profile["analysis_version"],
            window_start=row["admitted_at"] - dt.timedelta(days=1),
            window_end=row["admitted_at"] + dt.timedelta(days=1),
            cursor_admitted_at=None,
            cursor_request_id=None,
            limit=10,
        )
        source, reason = source_reader.classify_discovered(
            discovered[0], now=dt.datetime.now(dt.UTC)
        )
        assert source is None and reason == "pending_confirmation"
        # Repeated scans while the admission is still pending must not exhaust
        # the retry budget; late confirmation has to remain discoverable.
        for _ in range(8):
            await source_reader.record_gap(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                row=discovered[0],
                reason=reason,
            )
        await session.commit()
    async with owner() as session:
        await session.execute(
            text("UPDATE agent_submissions SET admission_status='confirmed' WHERE request_id=:r"),
            {"r": row["request_id"]},
        )
        await session.commit()
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        retries = await source_reader.discover_gap_retries(
            session,
            tenant_id=str(tenant),
            analysis_version=profile["analysis_version"],
            limit=10,
        )
        assert retries
        source, reason = source_reader.classify_discovered(retries[0], now=dt.datetime.now(dt.UTC))
        assert source is not None and reason is None
        assert source.content_version == 1


async def test_privacy_purge_retires_cluster(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查深圳今天SO2"
    )
    await _discover_and_process(worker, tenant, profile, row)
    async with owner() as session:
        await session.execute(
            text(
                "INSERT INTO agent_thread_deletions(id,tenant_id,thread_id,user_id,state,"
                "requested_at) VALUES (gen_random_uuid(),:t,:th,:u,'requested',now())"
            ),
            {"t": tenant, "th": row["thread"], "u": user},
        )
        await session.commit()
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        result = await privacy.purge_thread(session, tenant_id=str(tenant), thread_id=row["thread"])
        await session.commit()
        assert result["sources"] == 1
        assert (
            await session.scalar(
                text("SELECT count(*) FROM agent_question_intents WHERE tenant_id=:t"),
                {"t": tenant},
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM agent_question_cluster_vectors WHERE tenant_id=:t"),
                {"t": tenant},
            )
            == 0
        )


async def _seed_job(owner, tenant, user, membership, profile_id, delegation_id):
    now = dt.datetime.now(dt.UTC)
    job_id = uuid.uuid4()
    async with owner() as session:
        await session.execute(
            text(
                "INSERT INTO agent_insight_delegations(id,tenant_id,user_id,membership_id,"
                "session_handle_hash,ctx_version,user_auth_revision,tenant_auth_revision,"
                "membership_revision,profile_id,scope,status,revision,issued_at,expires_at)"
                " VALUES (:id,:t,:u,:m,:h,1,1,1,1,:p,'{}'::jsonb,'active',0,:now,:exp)"
            ),
            {
                "id": delegation_id,
                "t": tenant,
                "u": user,
                "m": membership,
                "h": uuid.uuid4().hex + uuid.uuid4().hex,
                "p": profile_id,
                "now": now,
                "exp": now + dt.timedelta(hours=1),
            },
        )
        await session.execute(
            text(
                "INSERT INTO agent_insight_jobs(id,tenant_id,profile_id,delegation_id,"
                "requested_by,job_kind,state,window_start,window_end,as_of,batch_size,"
                "max_sources,max_tokens,authorization_expires_at,created_at,updated_at)"
                " VALUES (:id,:t,:p,:d,:u,'incremental','pending',:start,:end,:now,100,"
                "        500,200000,:exp,:now,:now)"
            ),
            {
                "id": job_id,
                "t": tenant,
                "p": profile_id,
                "d": delegation_id,
                "u": user,
                "start": now - dt.timedelta(days=1),
                "end": now,
                "now": now,
                "exp": now + dt.timedelta(hours=1),
            },
        )
        await session.commit()
    return job_id


async def test_job_lease_fence_and_delegation_not_readable(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    async with owner() as session:
        # 测试库可能有其他用例留下的 pending 作业；先清理本域作业再领取。
        await session.execute(text("DELETE FROM agent_insight_jobs"))
        await session.execute(text("DELETE FROM agent_insight_delegations"))
        await session.commit()
    await _seed_job(
        owner,
        tenant,
        user,
        membership,
        profile["active_profile_id"],
        uuid.uuid4(),
    )
    async with worker() as first:
        await _guc(first, worker=True)
        job = await jobs.claim_job(first, worker_id="w1", lease_seconds=60)
        assert job is not None and job["fence"] == 1
        await first.commit()
    async with worker() as second:
        await _guc(second, worker=True)
        assert await jobs.claim_job(second, worker_id="w2", lease_seconds=60) is None
        await second.commit()
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        assert await jobs.update_progress(
            session,
            job_id=job["id"],
            worker_id="w1",
            fence=1,
            processed_delta=1,
            classified_delta=1,
            unclassified_delta=0,
            failed_delta=0,
            usage_tokens=None,
            estimated_tokens=10,
            cursor_admitted_at=None,
            cursor_request_id=None,
        )
        assert not await jobs.update_progress(
            session,
            job_id=job["id"],
            worker_id="w1",
            fence=999,
            processed_delta=1,
            classified_delta=0,
            unclassified_delta=0,
            failed_delta=0,
            usage_tokens=None,
            estimated_tokens=0,
            cursor_admitted_at=None,
            cursor_request_id=None,
        )
        assert await jobs.release_job(
            session, job_id=job["id"], worker_id="w1", fence=1, state="completed"
        )
        await session.commit()
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    try:
        factory = async_sessionmaker(runtime, expire_on_commit=False)
        async with factory() as session:
            identity = _identity(
                tenant, user, membership, {"agent:operations:read", "agent:insights:manage"}
            )
            await scope_session(session, identity, require_ops=True, require_manage=True)
            with pytest.raises(ProgrammingError):
                await session.scalar(text("SELECT count(*) FROM agent_insight_delegations"))
    finally:
        await runtime.dispose()


async def test_governance_rename_and_merge(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    query_row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查北京今天PM2.5"
    )
    cause_row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="北京今天PM2.5为什么升高"
    )
    await _discover_and_process(worker, tenant, profile, query_row)
    await _discover_and_process(worker, tenant, profile, cause_row)
    async with worker() as session:
        await _guc(session, tenant=tenant, worker=True)
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, object, revision, status FROM agent_question_clusters"
                        " WHERE tenant_id=:t ORDER BY object"
                    ),
                    {"t": tenant},
                )
            )
            .mappings()
            .all()
        )
        await session.commit()
    assert len(rows) == 2
    query_cluster = next(row for row in rows if row["object"] == "air_quality")
    cause_cluster = next(row for row in rows if row["object"] == "pollution_source")
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:operations:read", "agent:content:read", "agent:insights:manage"},
    )
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    try:
        async with factory() as session:
            await scope_session(
                session,
                identity,
                require_ops=True,
                require_content=True,
                require_manage=True,
            )
            renamed = await governance.question_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                profile=profile,
                cluster_id=cause_cluster["id"],
                payload={
                    "action": "rename",
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": int(cause_cluster["revision"]),
                    "preview": False,
                    "new_name": "污染成因分析",
                },
                settings=_settings(),
            )
            assert renamed["new_name"] == "污染成因分析"
            merged = await governance.question_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                profile=profile,
                cluster_id=cause_cluster["id"],
                payload={
                    "action": "merge",
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": int(renamed["revision"]),
                    "preview": False,
                    "target_cluster_id": str(query_cluster["id"]),
                },
                settings=_settings(),
            )
            await session.commit()
            assert merged["target_cluster_id"] == str(query_cluster["id"])
        async with factory() as session:
            await scope_session(session, identity, require_ops=True, require_content=True)
            status = await session.scalar(
                text("SELECT status FROM agent_question_clusters WHERE id=:id"),
                {"id": cause_cluster["id"]},
            )
            assert status == "merged"
            await session.rollback()
    finally:
        await runtime.dispose()


async def test_unclassified_assign_with_sentinel_and_split(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner,
        tenant=tenant,
        user=user,
        membership=membership,
        content="查北京今天PM2.5，并解释为什么升高",
    )
    await _discover_and_process(worker, tenant, profile, row)
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:operations:read", "agent:content:read", "agent:insights:manage"},
    )
    sentinel = uuid.UUID(int=0)
    try:
        async with factory() as session:
            await scope_session(
                session, identity, require_ops=True, require_content=True, require_manage=True
            )
            state = await storage.get_profile_state(session, tenant_id=str(tenant))
            assignment_revision = int(state["assignment_revision"])
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT a.intent_id, a.cluster_id, i.action"
                            " FROM agent_question_assignments a"
                            " JOIN agent_question_intents i ON i.id=a.intent_id"
                            " WHERE a.tenant_id=:t AND a.status='active'"
                        ),
                        {"t": tenant},
                    )
                )
                .mappings()
                .all()
            )
            query_row = next(item for item in rows if item["action"] == "query")
            cause_row = next(item for item in rows if item["action"] == "cause")
            assigned = await governance.question_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                profile=state,
                cluster_id=sentinel,
                payload={
                    "action": "assign",
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": assignment_revision,
                    "preview": False,
                    "intent_ids": [str(cause_row["intent_id"])],
                    "target_cluster_id": str(query_row["cluster_id"]),
                },
                settings=_settings(),
            )
            assert assigned["target_cluster_id"] == str(query_row["cluster_id"])
            await session.commit()
        async with factory() as session:
            await scope_session(
                session, identity, require_ops=True, require_content=True, require_manage=True
            )
            state = await storage.get_profile_state(session, tenant_id=str(tenant))
            intent_ids = (
                (
                    await session.execute(
                        text(
                            "SELECT a.intent_id::text AS intent_id"
                            " FROM agent_question_assignments a"
                            " WHERE a.tenant_id=:t AND a.cluster_id=:c AND a.status='active'"
                        ),
                        {"t": tenant, "c": query_row["cluster_id"]},
                    )
                )
                .scalars()
                .all()
            )
            assert len(intent_ids) == 2
            cluster = (
                (
                    await session.execute(
                        text(
                            "SELECT revision, canonical_name FROM agent_question_clusters"
                            " WHERE tenant_id=:t AND id=:c"
                        ),
                        {"t": tenant, "c": query_row["cluster_id"]},
                    )
                )
                .mappings()
                .first()
            )
            current = await storage.get_profile_state(session, tenant_id=str(tenant))
            preview = await governance.question_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                profile=current,
                cluster_id=query_row["cluster_id"],
                payload={
                    "action": "split",
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": int(cluster["revision"]),
                    "preview": True,
                    "groups": [
                        {"name": cluster["canonical_name"], "intent_ids": [intent_ids[0]]},
                        {"name": "拆分出的原因分析", "intent_ids": [intent_ids[1]]},
                    ],
                },
                settings=_settings(),
            )
            assert preview["preview_token"]
            committed = await governance.question_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                profile=current,
                cluster_id=query_row["cluster_id"],
                payload={
                    "action": "split",
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": int(cluster["revision"]),
                    "preview": False,
                    "preview_token": preview["preview_token"],
                    "groups": [
                        {"name": cluster["canonical_name"], "intent_ids": [intent_ids[0]]},
                        {"name": "拆分出的原因分析", "intent_ids": [intent_ids[1]]},
                    ],
                },
                settings=_settings(),
            )
            assert len(committed["groups"]) == 2
            await session.commit()
    finally:
        await runtime.dispose()


async def test_feedback_is_request_level_and_users_deduped(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    membership_a, membership_b = uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row_a = await _seed_request(
        owner, tenant=tenant, user=user_a, membership=membership_a, content="查北京今天PM2.5"
    )
    row_b = await _seed_request(
        owner, tenant=tenant, user=user_b, membership=membership_b, content="查上海今天PM2.5"
    )
    await _discover_and_process(worker, tenant, profile, row_a)
    await _discover_and_process(worker, tenant, profile, row_b)
    answer_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    async with owner() as session:
        await session.execute(
            text(
                "INSERT INTO agent_run_attempts(id,tenant_id,user_id,membership_id,request_id,"
                "attempt_index,graph_id,state,admitted_at,last_progress_at,updated_at)"
                " VALUES (:id,:t,:u,:m,:r,1,'basic-qa','succeeded',:at,:at,:at)"
            ),
            {
                "id": attempt_id,
                "t": tenant,
                "u": user_a,
                "m": membership_a,
                "r": row_a["request_id"],
                "at": row_a["admitted_at"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO agent_answers(id,tenant_id,user_id,attempt_id,message_id,"
                "content_version,partial,confirmed,confirmed_at,created_at)"
                " VALUES (:id,:t,:u,:a,'m',1,false,true,:at,:at)"
            ),
            {
                "id": answer_id,
                "t": tenant,
                "u": user_a,
                "a": attempt_id,
                "at": row_a["admitted_at"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO agent_feedback_current(id,tenant_id,user_id,answer_id,request_id,"
                "attempt_id,thread_id,rating,reason_code,revision,updated_at,created_at)"
                " VALUES (gen_random_uuid(),:t,:u,:a,:r,:att,:th,'negative',"
                "         'answer_incorrect',1,:at,:at)"
            ),
            {
                "t": tenant,
                "u": user_a,
                "a": answer_id,
                "r": row_a["request_id"],
                "att": attempt_id,
                "th": row_a["thread"],
                "at": row_a["admitted_at"],
            },
        )
        await session.commit()
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    identity = _identity(
        tenant,
        user_a,
        membership_a,
        {"agent:metrics:read", "agent:operations:read", "agent:content:read"},
    )
    try:
        async with factory() as session:
            await scope_session(session, identity, require_metrics=True)
            window = parse_window(
                window="last_7_days", start=None, end=None, as_of=dt.datetime.now(dt.UTC)
            )
            counts = await statistics.load_counts(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                window=window,
            )
            assert counts["negative_feedback_requests"] == 1
            assert counts["evaluated_requests"] == 1
            ranking = await statistics.question_ranking(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                assignment_revision=int(profile["assignment_revision"]),
                context_version=1,
                filter_key="f",
                window=window,
                previous=None,
                rank="users",
                total_intents=2,
                settings=_settings(),
                cursor_key=None,
                limit=50,
                filters={},
            )
            row = ranking["items"][0]
            assert row["atomic_intent_count"] == 2
            assert row["distinct_requests"] == 2
            assert row["distinct_users"] == 2
            assert row["request_level_negative_feedback"] == 1
            await session.rollback()
    finally:
        await runtime.dispose()


async def test_growth_zero_baseline_is_new_not_infinite(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant = uuid.uuid4()
    user = uuid.uuid4()
    membership = uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查北京今天PM2.5"
    )
    await _discover_and_process(worker, tenant, profile, row)
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:metrics:read", "agent:operations:read", "agent:content:read"},
    )
    try:
        async with factory() as session:
            await scope_session(session, identity, require_metrics=True)
            window = parse_window(
                window="last_7_days", start=None, end=None, as_of=dt.datetime.now(dt.UTC)
            )
            counts = await statistics.load_counts(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                window=window,
            )
            ranking = await statistics.question_ranking(
                session,
                tenant_id=str(tenant),
                profile_id=profile["active_profile_id"],
                analysis_version=profile["analysis_version"],
                assignment_revision=int(profile["assignment_revision"]),
                context_version=1,
                filter_key="f",
                window=window,
                previous=previous_window(window),
                rank="intents",
                total_intents=1,
                settings=_settings(),
                cursor_key=None,
                limit=50,
                filters={},
            )
            item = ranking["items"][0]
            assert counts["eligible_requests"] == 1
            assert item["previous_count"] == 0
            assert item["new"] is True
            assert item["growth_rate"] is None
            assert item["comparison_status"] == "comparable"
            await session.rollback()
    finally:
        await runtime.dispose()


async def test_job_actions_cas_and_idempotent(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, membership = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    job_id = await _seed_job(
        owner, tenant, user, membership, profile["active_profile_id"], uuid.uuid4()
    )
    runtime = create_async_engine(
        _normalize(os.environ["ADMIN_RUNTIME_DATABASE_URI"]), pool_pre_ping=True
    )
    factory = async_sessionmaker(runtime, expire_on_commit=False)
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:operations:read", "agent:content:read", "agent:insights:manage"},
    )
    try:
        async with factory() as session:
            await scope_session(
                session, identity, require_ops=True, require_content=True, require_manage=True
            )
            operation_id = str(uuid.uuid4())
            paused = await jobs.submit_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                job_id=job_id,
                operation_id=operation_id,
                expected_revision=0,
                action="pause",
            )
            assert paused["state"] == "paused_user"
            await session.commit()
        async with factory() as session:
            await scope_session(
                session, identity, require_ops=True, require_content=True, require_manage=True
            )
            replay = await jobs.submit_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                job_id=job_id,
                operation_id=operation_id,
                expected_revision=0,
                action="pause",
            )
            assert replay["replayed"] is True
            cancelled = await jobs.submit_action(
                session,
                identity=identity,
                tenant_id=str(tenant),
                job_id=job_id,
                operation_id=str(uuid.uuid4()),
                expected_revision=1,
                action="cancel",
            )
            assert cancelled["state"] == "cancelled"
            await session.commit()
        async with factory() as session:
            await scope_session(session, identity, require_ops=True)
            job = await jobs.get_job(session, tenant_id=str(tenant), job_id=job_id)
            assert job["state"] == "cancelled"
            assert job["cancel_requested"] is True
            await session.rollback()
    finally:
        await runtime.dispose()
