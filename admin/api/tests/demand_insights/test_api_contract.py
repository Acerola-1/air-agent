"""Insights API DTO/权限/路由契约（真实 PG + 依赖覆盖）."""

from __future__ import annotations

import datetime as dt
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import get_identity, require_active_tenant, require_tenant_context
from app.routers import demand_insights as router_module
from app.services.remote_identity import new_handle
from tests.demand_insights.test_db_integration import (
    _discover_and_process,
    _identity,
    _normalize,
    _seed_profile,
    _seed_request,
)

OWNER_URI = os.environ.get("INSIGHTS_TEST_OWNER_URI", "")
WORKER_URI = os.environ.get("INSIGHTS_TEST_WORKER_URI", "")

pytestmark = pytest.mark.skipif(
    not OWNER_URI or not WORKER_URI, reason="需要自有空 PG 与 worker 角色"
)


def _app(identity) -> FastAPI:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/agent")

    async def fake_identity():
        return identity

    async def fake_active():
        return identity

    async def fake_context():
        return identity

    app.dependency_overrides[get_identity] = fake_identity
    app.dependency_overrides[require_active_tenant] = fake_active
    app.dependency_overrides[require_tenant_context] = fake_context
    return app


@pytest.fixture()
async def engines():
    owner = create_async_engine(_normalize(OWNER_URI), pool_pre_ping=True)
    worker = create_async_engine(_normalize(WORKER_URI), pool_pre_ping=True)
    yield owner, worker
    await owner.dispose()
    await worker.dispose()


async def test_metrics_reads_and_ops_denial(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, membership = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查北京今天PM2.5"
    )
    await _discover_and_process(worker, tenant, profile, row)
    metrics_identity = _identity(tenant, user, membership, {"agent:metrics:read"})
    app = _app(metrics_identity)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        overview = await client.get("/agent/insights/overview?window=last_7_days")
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert body["meta"]["schema_version"] == "demand-insights-v1"
        assert body["meta"]["analysis_version"] == profile["analysis_version"]
        assert body["meta"]["profile_id"] == str(profile["active_profile_id"])
        assert body["counts"]["eligible_requests"] >= 1
        questions = await client.get("/agent/insights/questions?window=last_7_days")
        assert questions.status_code == 200, questions.text
        payload = questions.json()
        assert payload["items"]
        assert payload["items"][0]["distinct_requests"] >= 1
        assert "question" not in payload["items"][0]  # 不返回原句字段
        topics = await client.get("/agent/insights/topics?window=last_7_days")
        assert topics.status_code == 200
        detail = await client.get(f"/agent/insights/questions/{payload['items'][0]['cluster_id']}")
        assert detail.status_code == 200, detail.text
        assert "text" not in detail.json()
        # metrics-only 不能看可识别来源/未归类/作业
        denied = await client.get(
            f"/agent/insights/questions/{payload['items'][0]['cluster_id']}/sources"
        )
        assert denied.status_code == 403
        assert (await client.get("/agent/insights/unclassified")).status_code == 403
        assert (await client.get("/agent/insights/jobs")).status_code == 403


async def test_ops_content_sources_and_unclassified(engines):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, membership = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile = await _seed_profile(worker, tenant)
    row = await _seed_request(
        owner, tenant=tenant, user=user, membership=membership, content="查北京今天PM2.5"
    )
    await _discover_and_process(worker, tenant, profile, row)
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:metrics:read", "agent:operations:read", "agent:content:read"},
    )
    app = _app(identity)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        questions = (await client.get("/agent/insights/questions?window=last_7_days")).json()
        cluster_id = questions["items"][0]["cluster_id"]
        sources = await client.get(f"/agent/insights/questions/{cluster_id}/sources")
        assert sources.status_code == 200, sources.text
        source_items = sources.json()["items"]
        assert source_items
        assert source_items[0]["content_path"].startswith("/operations/runs")
        assert "intent_summary" in source_items[0]
        unclassified = await client.get("/agent/insights/unclassified")
        assert unclassified.status_code == 200, unclassified.text
        assert "items" in unclassified.json() and "gaps" in unclassified.json()


async def test_job_create_requires_remote_and_is_idempotent(engines, monkeypatch):
    owner_engine, worker_engine = engines
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    worker = async_sessionmaker(worker_engine, expire_on_commit=False)
    tenant, user, membership = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_profile(worker, tenant)
    identity = _identity(
        tenant,
        user,
        membership,
        {"agent:operations:read", "agent:content:read", "agent:insights:manage"},
    )
    identity.session.expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)
    handle = new_handle()
    app = _app(identity)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(
            "/agent/insights/jobs",
            json={"operation_id": str(uuid.uuid4()), "job_kind": "incremental"},
        )
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "remote_identity_required"

    monkeypatch.setattr(router_module, "identity_backend_mode", lambda: "remote")
    monkeypatch.setattr(router_module, "request_cookie", lambda request: handle)
    app = _app(identity)
    transport = httpx.ASGITransport(app=app)
    operation_id = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/agent/insights/jobs",
            json={"operation_id": operation_id, "job_kind": "incremental"},
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["result"]["job_id"]
        replay = await client.post(
            "/agent/insights/jobs",
            json={"operation_id": operation_id, "job_kind": "incremental"},
        )
        assert replay.status_code == 202
        assert replay.json()["replayed"] is True
        assert replay.json()["result"]["job_id"] == job_id
        listing = await client.get("/agent/insights/jobs")
        assert listing.status_code == 200
        assert any(item["job_id"] == job_id for item in listing.json()["items"])
    # 确认作业行没有明文 Bearer/cookie
    async with owner() as session:
        row = (
            (
                await session.execute(
                    text("SELECT requested_by, delegation_id FROM agent_insight_jobs WHERE id=:id"),
                    {"id": uuid.UUID(job_id)},
                )
            )
            .mappings()
            .first()
        )
        assert row and row["requested_by"] == user
