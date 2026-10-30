"""PostgreSQL 报告持久化：scope/摘要/篡改/过期/删除.

需要显式提供自建测试库 DSN：
    TRACING_TEST_RUNTIME_DSN=postgresql://...
未设置时跳过，避免把单元测试连接到共享正式库。
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
TRACING_TESTS = Path(__file__).resolve().parents[1] / "unit_tests" / "tracing"
for path in (SRC, TRACING_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fixtures import make_scope  # noqa: E402

from intelligent_tracing.report_store import (  # noqa: E402
    PersistentReportStore,
    ReportRereadError,
)
from intelligent_tracing.schemas import ValidatedReport  # noqa: E402

DSN = os.getenv("TRACING_TEST_RUNTIME_DSN", "")


def _report(
    *, run_id: str = "run-test-1", report_id: str = "rep_store_test"
) -> ValidatedReport:
    scope = make_scope()
    report = ValidatedReport(
        report_id=report_id,
        analysis_id="analysis-store",
        revision=2,
        request_digest="digest-request",
        policy_version="analysis-policy-v2",
        run_id=run_id,
        claim_ids=("coverage.target",),
        limitations=("trajectory_unavailable",),
        body_markdown="## 报告\n数据完整率 90.0%。",
        canvas={
            "title": "测试报告",
            "content_type": "markdown",
            "content": "## 报告\n数据完整率 90.0%。",
            "open_in": "canvas_window",
        },
        evidence_ids=("ev_1",),
        scope=scope,
        content_sha256="",
        created_at=datetime.now(UTC),
    )
    return report.model_copy(update={"content_sha256": report.compute_digest()})


@pytest.fixture()
def runtime_env(monkeypatch):
    if not DSN:
        pytest.skip("未提供 TRACING_TEST_RUNTIME_DSN")
    monkeypatch.setenv("DATABASE_URI", DSN)
    monkeypatch.setenv("RUNTIME_DATABASE_URI", DSN)
    monkeypatch.setenv("TOOL_ARTIFACT_RETENTION_DAYS", "30")
    yield DSN


@pytest.mark.anyio
async def test_report_roundtrip_scope_and_tamper(runtime_env):
    import psycopg

    store = PersistentReportStore()
    report = _report()
    await store.save(report)
    loaded = await store.get(report.report_id, scope=report.scope)
    assert loaded is not None
    assert loaded.content_sha256 == report.content_sha256
    assert loaded.scope == report.scope
    assert loaded.run_id == report.run_id

    wrong_scope = report.scope.model_copy(update={"user_id": "other-user"})
    # 跨用户查询在存储层就不可见，返回 None 而不是泄露报告存在性。
    assert await store.get(report.report_id, scope=wrong_scope) is None

    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        await conn.execute(
            "UPDATE tool_artifact_storage SET raw_payload = replace(raw_payload, '90.0', '10.0') "
            "WHERE tool_call_id = %s",
            (report.report_id,),
        )
        await conn.commit()
    with pytest.raises(ReportRereadError, match="摘要"):
        await store.get(report.report_id, scope=report.scope)


@pytest.mark.anyio
async def test_report_expiry_and_deletion(runtime_env):
    import psycopg

    store = PersistentReportStore()
    report = _report(run_id="run-test-2", report_id="rep_store_expiry")
    await store.save(report)
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        await conn.execute(
            "UPDATE tool_artifact_storage SET expires_at = %s WHERE tool_call_id = %s",
            ("2000-01-01T00:00:00+00:00", report.report_id),
        )
        await conn.commit()
    with pytest.raises(ReportRereadError, match="过期"):
        await store.get(report.report_id, scope=report.scope)

    await store.save(report)
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        await conn.execute(
            "DELETE FROM tool_artifact_storage WHERE tool_call_id = %s",
            (report.report_id,),
        )
        await conn.commit()
    assert await store.get(report.report_id, scope=report.scope) is None


def test_save_requires_run_binding(runtime_env):
    import asyncio

    store = PersistentReportStore()

    async def _run():
        report = _report(run_id="", report_id="rep_store_unbound")
        with pytest.raises(ReportRereadError, match="运行归属"):
            await store.save(report)

    asyncio.run(_run())


@pytest.mark.anyio
async def test_reopened_canvas_matches_stored_report_digest(runtime_env):
    store = PersistentReportStore()
    report = _report(run_id="run-test-3", report_id="rep_store_canvas")
    await store.save(report)
    reopened = await store.get(report.report_id, scope=report.scope)
    assert reopened is not None
    assert reopened.canvas == report.canvas
    assert reopened.content_sha256 == report.compute_digest()


@pytest.mark.anyio
async def test_report_readback_isolated_across_tenant_thread_and_context(runtime_env):
    store = PersistentReportStore()
    report = _report(run_id="run-test-4", report_id="rep_store_scope")
    await store.save(report)
    for update in ({"tenant_id": "tenant-b"}, {"thread_id": "thread-b"}):
        other = report.scope.model_copy(update=update)
        assert await store.get(report.report_id, scope=other) is None
    # 同企业/线程但上下文或 epoch 变化：能定位到行，但作用域不一致必须拒绝。
    for update in ({"context_version": "cv-2"}, {"epoch": "epoch-2"}):
        other = report.scope.model_copy(update=update)
        with pytest.raises(ReportRereadError, match="作用域"):
            await store.get(report.report_id, scope=other)
    # A-B-A：回到原作用域仍能读到同一摘要。
    assert (await store.get(report.report_id, scope=report.scope)).content_sha256 == (
        report.content_sha256
    )
