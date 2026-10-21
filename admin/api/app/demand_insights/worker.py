"""需求洞察独立 worker（默认关闭，显式 `python -m app.demand_insights.worker`）.

- 使用独立非 owner worker 角色连接（INSIGHTS_WORKER_DATABASE_URI），跨企业领取
  作业后立即切换到任务企业；只读台账来源/反馈/墓碑，写本域表。
- 每个批次、每次 Embedding/LLM 外呼前、提交前都重新内省有限人类委托；撤权/
  切企业/过期立即暂停，IAM 故障暂停但保留委托。
- 引擎/模型只在 worker 进程内创建；web 进程导入本模块不会启动线程或网络。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import _normalize_pg_uri
from app.demand_insights import classifier, delegations, jobs, privacy, source_reader, storage
from app.demand_insights.config import InsightsSettings, get_insights_settings
from app.demand_insights.errors import InsightsUnavailable
from app.demand_insights.model_gateway import ModelGateway

logger = logging.getLogger("air_agent.insights.worker")
LEASE_SECONDS = 120
STOP = asyncio.Event()


def _worker_id(settings: InsightsSettings) -> str:
    return f"{settings.worker_id}:{os.getpid()}"


def pause_state_for(exc: InsightsUnavailable) -> str:
    """Map an unavailable error to the documented pause state.

    Delegation/revocation and IAM unavailability pause authorization and keep
    the delegation for renewal; model/protocol faults pause as failures.
    """
    code = str(exc.code or "")
    if "authorization" in code or code == "iam_unavailable":
        return "paused_authorization"
    return "paused_failure"


def _apply_worker_transaction(session, transaction, connection):
    scope = session.info.get("insights_scope")
    if scope is None:
        return
    for key, value in (
        ("insight_worker", "on"),
        ("tenant_id", scope or ""),
        ("agent_operations", "on" if scope else ""),
    ):
        connection.execute(
            text("SELECT set_config(:key,:value,true)"), {"key": "air_agent." + key, "value": value}
        )


async def _set_worker(scope: AsyncSession, tenant_id: str | None = None) -> None:
    """每个事务重施限定上下文，连接归还后不残留上一个任务的企业。"""
    scope.info["insights_scope"] = tenant_id or ""
    if not scope.info.get("insights_scope_listener"):
        event.listen(scope.sync_session, "after_begin", _apply_worker_transaction)
        scope.info["insights_scope_listener"] = True
    await scope.execute(text("SELECT 1"))


async def _claim(
    factory: async_sessionmaker[AsyncSession], settings: InsightsSettings
) -> dict[str, Any] | None:
    async with factory() as session:
        await _set_worker(session)
        job = await jobs.claim_job(
            session,
            worker_id=_worker_id(settings),
            lease_seconds=LEASE_SECONDS,
            successor_delay_seconds=settings.worker_poll_seconds,
        )
        await session.commit()
        return job


async def _load_profile(
    factory: async_sessionmaker[AsyncSession], job: dict[str, Any]
) -> dict[str, Any] | None:
    async with factory() as session:
        await _set_worker(session, str(job["tenant_id"]))
        try:
            return await storage.get_profile(
                session, tenant_id=str(job["tenant_id"]), profile_id=job["profile_id"]
            )
        except Exception:  # noqa: BLE001 - 缺失 profile 由调用方暂停
            return None


async def _check_delegation(
    factory: async_sessionmaker[AsyncSession],
    job: dict[str, Any],
    settings: InsightsSettings,
) -> tuple[bool, str]:
    async with factory() as session:
        await _set_worker(session, str(job["tenant_id"]))
        delegation = await delegations.load_delegation(
            session, tenant_id=str(job["tenant_id"]), delegation_id=job["delegation_id"]
        )
        if delegation is None:
            await session.commit()
            return False, "delegation_missing"
        try:
            check = await delegations.check_delegation(
                session, delegation=delegation, settings=settings
            )
        except InsightsUnavailable:
            await session.rollback()
            raise
        await session.commit()
        return check.ok, check.reason


async def _guard_factory(
    factory: async_sessionmaker[AsyncSession],
    job: dict[str, Any],
    settings: InsightsSettings,
) -> Callable[[], Awaitable[None]]:
    async def guard() -> None:
        ok, reason = await _check_delegation(factory, job, settings)
        if not ok:
            raise InsightsUnavailable("分析委托已失效", code=f"authorization_{reason}")
        async with factory() as session:
            await _set_worker(session, str(job["tenant_id"]))
            valid = await jobs.extend_lease(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                lease_seconds=LEASE_SECONDS,
            )
            await session.commit()
        if not valid:
            raise InsightsUnavailable("作业租约已失效", code="stale_lease")

    return guard


async def _process_batch(
    factory: async_sessionmaker[AsyncSession],
    job: dict[str, Any],
    profile: dict[str, Any],
    settings: InsightsSettings,
    gateway: ModelGateway,
) -> tuple[int, str | None, bool]:
    """返回 (处理来源数, 终止状态, 是否还有未处理工作)."""
    tenant = str(job["tenant_id"])
    analysis_version = profile["analysis_version"]
    guard = await _guard_factory(factory, job, settings)
    processed = 0
    classified = 0
    unclassified = 0
    failed = 0
    estimated = max(int(job.get("estimated_tokens") or 0), int(job.get("actual_tokens") or 0))
    cursor_at = job.get("cursor_admitted_at")
    cursor_id = job.get("cursor_request_id")
    max_sources = int(job.get("max_sources") or settings.max_sources_per_job)
    max_tokens = int(job.get("max_tokens") or settings.max_tokens_per_job)
    remaining = max_sources - int(job.get("processed_sources") or 0)
    if remaining <= 0:
        return 0, "completed", False
    if estimated >= max_tokens:
        return 0, "paused_budget", True
    # 在读取来源前也检查授权和租约，不等到第一次模型外呼。
    await guard()

    async def finalize(session, result):
        await guard()
        await jobs.require_live_lease(
            session, job_id=job["id"], worker_id=_worker_id(settings), fence=int(job["fence"])
        )
        updated = await jobs.update_progress(
            session,
            job_id=job["id"],
            worker_id=_worker_id(settings),
            fence=int(job["fence"]),
            processed_delta=1,
            classified_delta=result.classified,
            unclassified_delta=result.unclassified,
            failed_delta=int(result.status != "processed"),
            usage_tokens=result.usage_tokens,
            estimated_tokens=max(50, result.split_count * 80),
            cursor_admitted_at=current_source["admitted_at"],
            cursor_request_id=current_source["request_id"],
        )
        if not updated:
            raise InsightsUnavailable("作业租约已失效", code="stale_lease")

    async with factory() as session:
        await _set_worker(session, tenant)
        await privacy.purge_expired(session, tenant_id=tenant)
        # 1) 晚确认/晚补正文缺口重试
        gaps = await source_reader.discover_gap_retries(
            session,
            tenant_id=tenant,
            analysis_version=analysis_version,
            window_start=job["window_start"],
            window_end=job.get("window_end"),
            limit=min(int(job.get("batch_size") or settings.batch_size), remaining),
        )
        for row in gaps:
            source, reason = source_reader.classify_discovered(row, now=dt.datetime.now(dt.UTC))
            if source is None:
                await source_reader.record_gap(
                    session,
                    tenant_id=tenant,
                    profile_id=profile["id"],
                    analysis_version=analysis_version,
                    row=row,
                    reason=reason or "missing_content",
                )
            else:
                await source_reader.save_source(
                    session,
                    tenant_id=tenant,
                    profile_id=profile["id"],
                    analysis_version=analysis_version,
                    source=source,
                )
        # 2) 正常增量发现
        discovered = await source_reader.discover_requests(
            session,
            tenant_id=tenant,
            profile_id=profile["id"],
            analysis_version=analysis_version,
            window_start=job["window_start"],
            window_end=job.get("window_end"),
            cursor_admitted_at=cursor_at,
            cursor_request_id=cursor_id,
            limit=min(int(job.get("batch_size") or settings.batch_size), remaining),
        )
        for row in discovered:
            source, reason = source_reader.classify_discovered(row, now=dt.datetime.now(dt.UTC))
            if source is None:
                await source_reader.record_gap(
                    session,
                    tenant_id=tenant,
                    profile_id=profile["id"],
                    analysis_version=analysis_version,
                    row=row,
                    reason=reason or "missing_content",
                )
            else:
                await source_reader.save_source(
                    session,
                    tenant_id=tenant,
                    profile_id=profile["id"],
                    analysis_version=analysis_version,
                    source=source,
                )
        if discovered:
            # 未确认/缺正文也推进发现水位；缺口表负责回看，不能卡住后续合法来源。
            last = discovered[-1]
            await jobs.require_live_lease(
                session, job_id=job["id"], worker_id=_worker_id(settings), fence=int(job["fence"])
            )
            if not await jobs.update_progress(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                processed_delta=0,
                classified_delta=0,
                unclassified_delta=0,
                failed_delta=0,
                usage_tokens=0,
                estimated_tokens=0,
                cursor_admitted_at=last["admitted_at"],
                cursor_request_id=last["request_id"],
            ):
                raise InsightsUnavailable("作业租约已失效", code="stale_lease")
            job["cursor_admitted_at"] = last["admitted_at"]
            job["cursor_request_id"] = last["request_id"]
        await session.commit()

    # 3) 重试/处理本次及历史未完成来源
    async with factory() as session:
        await _set_worker(session, tenant)
        sources = await source_reader.load_retry_sources(
            session,
            tenant_id=tenant,
            profile_id=profile["id"],
            analysis_version=analysis_version,
            limit=min(int(job.get("batch_size") or settings.batch_size), remaining),
            max_attempts=settings.max_attempts,
            window_start=job["window_start"],
            window_end=job.get("window_end"),
        )
        await session.commit()

    for source in sources:
        current_source = source
        if STOP.is_set():
            return processed, "pending", True
        ok, reason = await _check_delegation(factory, job, settings)
        if not ok:
            return processed, "paused_authorization", True
        async with factory() as session:
            await _set_worker(session, tenant)
            attempt = await source_reader.begin_source_attempt(
                session,
                tenant_id=tenant,
                source_id=source["id"],
                max_attempts=settings.max_attempts,
            )
            if attempt is None:
                await source_reader.mark_exhausted(
                    session,
                    tenant_id=tenant,
                    source_id=source["id"],
                    max_attempts=settings.max_attempts,
                )
            await session.commit()
        if attempt is None:
            continue
        async with factory() as session:
            await _set_worker(session, tenant)
            try:
                result = await classifier.process_source(
                    session,
                    tenant_id=tenant,
                    profile=profile,
                    source=dict(source),
                    settings=settings,
                    gateway=gateway,
                    guard=guard,
                    finalize=finalize,
                )
            except InsightsUnavailable as exc:
                await session.rollback()
                logger.warning("insights source authorization/model pause code=%s", exc.code)
                return processed, pause_state_for(exc), True
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                logger.warning(
                    "insights source failed class=%s source=%s",
                    type(exc).__name__,
                    str(source["id"])[:8],
                )
                failed += 1
                continue
        processed += 1
        classified += result.classified
        unclassified += result.unclassified
        if result.status != "processed":
            failed += 1
        estimated += max(50, result.split_count * 80)
        if result.usage_tokens is not None:
            # 已在外呼后实时写入；这里只用于预算判断的保守估计
            estimated += result.usage_tokens
        cursor_at = source["admitted_at"]
        cursor_id = source["request_id"]
        if processed >= remaining:
            job["cursor_admitted_at"] = cursor_at
            job["cursor_request_id"] = cursor_id
            return processed, "completed", False
        if estimated >= max_tokens:
            job["cursor_admitted_at"] = cursor_at
            job["cursor_request_id"] = cursor_id
            return processed, "paused_budget", True

    # 本轮没有可处理来源：若仍有未确认/缺源缺口，保留 pending 让周期作业回看
    has_pending = False
    async with factory() as session:
        await _set_worker(session, tenant)
        has_pending = bool(
            await session.scalar(
                text(
                    "SELECT 1 FROM agent_question_source_gaps"
                    " WHERE tenant_id=:tenant AND profile_id=:profile"
                    " AND analysis_version=:av"
                    " AND reason IN ('pending_confirmation','missing_content')"
                    " AND attempts < 5 AND admitted_at >= :window_start"
                    " AND (CAST(:window_end AS timestamptz) IS NULL"
                    " OR admitted_at<:window_end) LIMIT 1"
                ),
                {
                    "tenant": tenant,
                    "profile": profile["id"],
                    "av": analysis_version,
                    "window_start": job["window_start"],
                    "window_end": job.get("window_end"),
                },
            )
        )
        await session.commit()
    more = bool(discovered) or len(sources) >= min(
        int(job.get("batch_size") or settings.batch_size), remaining
    )
    return processed, "pending" if more or has_pending else "completed", has_pending or more


async def _run_job(
    factory: async_sessionmaker[AsyncSession],
    job: dict[str, Any],
    settings: InsightsSettings,
) -> None:
    profile = await _load_profile(factory, job)
    if profile is None:
        async with factory() as session:
            await _set_worker(session, str(job["tenant_id"]))
            await jobs.release_job(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                state="paused_failure",
                error_code="profile_not_found",
            )
            await session.commit()
        return
    gateway = ModelGateway(settings, guard=await _guard_factory(factory, job, settings))
    processed = 0
    try:
        processed, state, _has_pending = await _process_batch(
            factory, job, profile, settings, gateway
        )
    except InsightsUnavailable as exc:
        state = pause_state_for(exc)
        processed = 0
        async with factory() as session:
            await _set_worker(session, str(job["tenant_id"]))
            await jobs.release_job(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                state=state,
                error_code=exc.code,
            )
            await session.commit()
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("insights job failed class=%s", type(exc).__name__)
        async with factory() as session:
            await _set_worker(session, str(job["tenant_id"]))
            await jobs.release_job(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                state="paused_failure",
                error_code="worker_exception",
            )
            await session.commit()
        return
    try:
        async with factory() as session:
            await _set_worker(session, str(job["tenant_id"]))
            await jobs.require_live_lease(
                session, job_id=job["id"], worker_id=_worker_id(settings), fence=int(job["fence"])
            )
            # 本域墓碑/到期清理与作业同企业；独立脚本负责无活跃作业企业的全局回看。
            try:
                await privacy.purge_expired(session, tenant_id=str(job["tenant_id"]))
            except Exception as exc:  # noqa: BLE001 - 清理失败不改变作业结论
                logger.warning("insights cleanup failed class=%s", type(exc).__name__)
                await session.rollback()
                await _set_worker(session, str(job["tenant_id"]))
            if state == "completed" and job.get("continuous"):
                await jobs.create_successor_job(session, job=job, settings=settings)
            await jobs.release_job(
                session,
                job_id=job["id"],
                worker_id=_worker_id(settings),
                fence=int(job["fence"]),
                state=state
                if state in ("completed", "paused_authorization", "paused_failure", "paused_budget")
                else "pending",
                error_code=None if state == "completed" else state,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - 单个作业收尾失败不终止 worker
        logger.warning("insights job finalize failed class=%s", type(exc).__name__)
    logger.info(
        "insights job cycle tenant=%s kind=%s processed=%s state=%s",
        str(job["tenant_id"])[:8],
        job.get("job_kind"),
        processed,
        state,
    )


async def _cleanup_tenant_batch(factory, after: str | None = None) -> str | None:
    """独立于有效人类委托的删除清理；按企业有界轮转，失败下轮重试。"""
    async with factory() as session:
        await _set_worker(session)
        tenants = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT tenant_id FROM agent_insight_jobs"
                        " WHERE CAST(:after AS uuid) IS NULL OR tenant_id>CAST(:after AS uuid)"
                        " ORDER BY tenant_id LIMIT 20"
                    ),
                    {"after": after},
                )
            )
            .scalars()
            .all()
        )
    for tenant in tenants:
        async with factory() as session:
            await _set_worker(session, str(tenant))
            try:
                await privacy.purge_expired(session, tenant_id=str(tenant))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning("insights cleanup deferred class=%s", type(exc).__name__)
    return str(tenants[-1]) if len(tenants) == 20 else None


async def run_worker() -> int:
    settings = get_insights_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.worker_enabled:
        logger.info("INSIGHTS_WORKER_ENABLED 未开启；worker 退出")
        return 0
    if not settings.worker_database_configured:
        logger.error("INSIGHTS_WORKER_DATABASE_URI 未配置；拒绝启动")
        return 2
    if os.environ.get("AGENT_IDENTITY_BACKEND", "").strip().lower() != "remote":
        logger.error("worker 需要 AGENT_IDENTITY_BACKEND=remote 才能内省委托；拒绝启动")
        return 2
    engine = create_async_engine(
        _normalize_pg_uri(settings.worker_database_uri), pool_pre_ping=True, pool_size=2
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, STOP.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    cleanup_cursor = None
    try:
        while not STOP.is_set():
            cleanup_cursor = await _cleanup_tenant_batch(factory, cleanup_cursor)
            job = await _claim(factory, settings)
            if job is None:
                try:
                    await asyncio.wait_for(STOP.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            await _run_job(factory, job, settings)
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    return asyncio.run(run_worker())


if __name__ == "__main__":
    raise SystemExit(main())
