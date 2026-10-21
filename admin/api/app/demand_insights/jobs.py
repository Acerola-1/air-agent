"""分析作业：创建、幂等动作、租约/栅栏领取与进度提交.

状态语义：停止/暂停意图不等于已完成；只有 worker 在当前 fence 下提交的进度
才算数。旧租约返回的结果由 CAS 丢弃，不会覆盖新 worker 的进度。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights import storage
from app.demand_insights.config import InsightsSettings
from app.demand_insights.errors import InsightsConflict, InsightsNotFound
from app.services.identity import Identity

TERMINAL_STATES = ("completed", "cancelled", "failed")
RETRYABLE_STATES = ("paused_failure", "paused_user", "paused_authorization", "failed")


async def create_job(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile_id: uuid.UUID,
    delegation_id: uuid.UUID,
    job_kind: str,
    window_start: dt.datetime,
    window_end: dt.datetime | None,
    as_of: dt.datetime,
    batch_size: int,
    max_sources: int,
    max_tokens: int,
    continuous: bool,
    acknowledge_backfill: bool,
    authorization_expires_at: dt.datetime,
    operation_id: str,
    predecessor_job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if job_kind not in ("incremental", "backfill", "rebuild"):
        raise InsightsConflict("未知作业类型", code="invalid_job_kind")
    if job_kind == "backfill":
        if window_end is None:
            raise InsightsConflict("回填必须给出结束时间", code="backfill_window_required")
        if not acknowledge_backfill:
            raise InsightsConflict("回填必须显式确认预算", code="backfill_budget_required")
        if window_end - window_start > dt.timedelta(days=30):
            raise InsightsConflict("回填最多 30 个业务日", code="backfill_window_too_large")
    payload = {
        "profile_id": str(profile_id),
        "job_kind": job_kind,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat() if window_end else None,
        "max_sources": max_sources,
        "max_tokens": max_tokens,
        "continuous": continuous,
    }
    digest = storage.hash_payload(payload)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"insight-operation:{tenant_id}:{operation_id}"},
    )
    existing = (
        (
            await session.execute(
                text(
                    "SELECT payload_digest, result_snapshot FROM agent_insight_operations"
                    " WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant_id, "operation": operation_id},
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if existing["payload_digest"] != digest or existing["result_snapshot"] is None:
            raise InsightsConflict("同一操作 ID 携带不同内容", code="event_conflict")
        snapshot = dict(existing["result_snapshot"])
        snapshot["replayed"] = True
        return snapshot
    job_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    row = (
        (
            await session.execute(
                text(
                    """
                INSERT INTO agent_insight_jobs
                    (id, tenant_id, profile_id, delegation_id, requested_by, job_kind,
                     state, window_start, window_end, as_of, batch_size, max_sources,
                     max_tokens, authorization_expires_at, continuous, predecessor_job_id,
                     created_at, updated_at)
                VALUES (:id, :tenant, :profile, :delegation, :requester, :kind, 'pending',
                        :start, :end, :as_of, :batch, :max_sources, :max_tokens,
                        :authorization_expires, :continuous, :predecessor, :now, :now)
                RETURNING id, state, state_revision, created_at
                """
                ),
                {
                    "id": job_id,
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "delegation": delegation_id,
                    "requester": identity.user.id,
                    "kind": job_kind,
                    "start": window_start,
                    "end": window_end,
                    "as_of": as_of,
                    "batch": batch_size,
                    "max_sources": max_sources,
                    "max_tokens": max_tokens,
                    "authorization_expires": authorization_expires_at,
                    "continuous": continuous,
                    "predecessor": predecessor_job_id,
                    "now": now,
                },
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    snapshot = {
        "job_id": str(job_id),
        "state": row["state"],
        "state_revision": int(row["state_revision"]),
        "created_at": row["created_at"].isoformat(),
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="job_create",
        target_type="job",
        target_id=job_id,
        expected_revision=0,
        result_revision=0,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def get_job(session: AsyncSession, *, tenant_id: str, job_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT j.id, j.job_kind, j.state, j.profile_id, j.window_start,
                       j.window_end, j.as_of, j.processed_sources, j.classified_intents,
                       j.unclassified_intents, j.failed_sources, j.estimated_tokens,
                       j.actual_tokens, j.usage_missing, j.batch_size, j.max_sources,
                       j.max_tokens, j.authorization_expires_at, j.state_revision,
                       j.last_error_code, j.cancel_requested, j.continuous,
                       j.lease_deadline, j.created_at, j.started_at, j.paused_at,
                       j.completed_at
                FROM agent_insight_jobs j
                WHERE j.tenant_id=:tenant AND j.id=:id
                """
                ),
                {"tenant": tenant_id, "id": job_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InsightsNotFound("作业不存在", code="job_not_found")
    return dict(row)


async def list_jobs(
    session: AsyncSession, *, tenant_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT j.id, j.job_kind, j.state, j.window_start, j.window_end,
                       j.processed_sources, j.failed_sources, j.classified_intents,
                       j.unclassified_intents, j.actual_tokens, j.usage_missing,
                       j.authorization_expires_at, j.state_revision, j.last_error_code,
                       j.created_at
                FROM agent_insight_jobs j
                WHERE j.tenant_id=:tenant
                ORDER BY j.created_at DESC
                LIMIT :limit
                """
                ),
                {"tenant": tenant_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def submit_action(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    job_id: uuid.UUID,
    operation_id: str,
    expected_revision: int,
    action: str,
    renewal_delegation_id: uuid.UUID | None = None,
    renewal_expires_at: dt.datetime | None = None,
) -> dict[str, Any]:
    if action not in ("retry", "pause", "renew", "cancel"):
        raise InsightsConflict("未知作业动作", code="invalid_action")
    if action == "renew" and renewal_delegation_id is None:
        raise InsightsConflict("续期缺少新委托", code="delegation_required")
    digest = storage.hash_payload(
        {"job_id": str(job_id), "action": action, "expected_revision": expected_revision}
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"insight-operation:{tenant_id}:{operation_id}"},
    )
    existing = (
        (
            await session.execute(
                text(
                    "SELECT payload_digest, result_snapshot FROM agent_insight_operations"
                    " WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant_id, "operation": operation_id},
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if existing["payload_digest"] != digest or existing["result_snapshot"] is None:
            raise InsightsConflict("同一操作 ID 携带不同内容", code="event_conflict")
        snapshot = dict(existing["result_snapshot"])
        snapshot["replayed"] = True
        return snapshot
    job = await get_job(session, tenant_id=tenant_id, job_id=job_id)
    if int(job["state_revision"]) != expected_revision:
        raise InsightsConflict("作业状态已变化，请刷新后重试", code="revision_conflict")
    now = dt.datetime.now(dt.UTC)
    if action == "cancel":
        if job["state"] in TERMINAL_STATES:
            raise InsightsConflict("终态作业不能取消", code="invalid_transition")
        new_state = "cancelled"
    elif action == "pause":
        if job["state"] not in ("pending", "running"):
            raise InsightsConflict("当前状态不能暂停", code="invalid_transition")
        new_state = "paused_user"
    elif action == "retry":
        if job["state"] not in RETRYABLE_STATES:
            raise InsightsConflict("当前状态不能重试", code="invalid_transition")
        new_state = "pending"
    else:
        if job["state"] != "paused_authorization":
            raise InsightsConflict("只有授权暂停的作业可以续期", code="invalid_transition")
        new_state = "pending"
    assignments: dict[str, Any] = {
        "state": new_state,
        "revision": expected_revision + 1,
        "now": now,
        "tenant": tenant_id,
        "job": job_id,
    }
    set_parts = ["state=:state", "state_revision=:revision"]
    if renewal_delegation_id is not None:
        set_parts.append("delegation_id=:delegation")
        set_parts.append("authorization_expires_at=:authorization_expires")
        assignments["delegation"] = renewal_delegation_id
        assignments["authorization_expires"] = renewal_expires_at
    if action == "cancel":
        set_parts.append("cancel_requested=true")
    set_parts += [
        "lease_owner=NULL",
        "lease_deadline=NULL",
        "paused_at=CASE WHEN :state LIKE 'paused%' THEN :now ELSE paused_at END",
        "completed_at=CASE WHEN :state IN ('completed','cancelled','failed')"
        " THEN :now ELSE completed_at END",
        "updated_at=:now",
    ]
    result = await session.execute(
        text(
            "UPDATE agent_insight_jobs SET "
            + ", ".join(set_parts)
            + " WHERE tenant_id=:tenant AND id=:job AND state_revision=:expected"
        ),
        {**assignments, "expected": expected_revision},
    )
    if result.rowcount != 1:
        raise InsightsConflict("作业状态已变化，请刷新后重试", code="revision_conflict")
    snapshot = {
        "job_id": str(job_id),
        "action": action,
        "state": new_state,
        "state_revision": expected_revision + 1,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action=f"job_{action}",
        target_type="job",
        target_id=job_id,
        expected_revision=expected_revision,
        result_revision=expected_revision + 1,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def claim_job(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    successor_delay_seconds: int = 60,
) -> dict[str, Any] | None:
    """跨企业领取：worker 事务内已设置 `air_agent.insight_worker='on'`."""
    row = (
        (
            await session.execute(
                text(
                    """
                UPDATE agent_insight_jobs
                   SET state='running',
                       lease_owner=:worker,
                       lease_deadline=now() + make_interval(secs => :lease),
                       fence=fence+1,
                       state_revision=state_revision+1,
                       started_at=COALESCE(started_at, now()),
                       updated_at=now()
                 WHERE id = (
                    SELECT id FROM agent_insight_jobs
                    WHERE state IN ('pending','running')
                      AND cancel_requested=false
                      AND (started_at IS NULL OR state='running'
                           OR updated_at <= now()-make_interval(secs => :successor_delay))
                      AND (predecessor_job_id IS NULL
                           OR created_at <= now()-make_interval(secs => :successor_delay))
                      AND (lease_deadline IS NULL OR lease_deadline < now())
                    ORDER BY created_at, id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                 )
                RETURNING id, tenant_id, profile_id, delegation_id, requested_by, job_kind,
                          window_start, window_end, as_of, batch_size, max_sources,
                          max_tokens, authorization_expires_at, processed_sources,
                          classified_intents, unclassified_intents, failed_sources,
                          estimated_tokens, actual_tokens, usage_missing, fence,
                          cursor_admitted_at, cursor_request_id, state_revision, continuous
                """
                ),
                {
                    "worker": worker_id,
                    "lease": lease_seconds,
                    "successor_delay": successor_delay_seconds,
                },
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def require_live_lease(
    session: AsyncSession, *, job_id: uuid.UUID, worker_id: str, fence: int
) -> None:
    """锁住当前有效租约，分类结果与作业进度须在同一事务提交。"""
    live = await session.scalar(
        text(
            "SELECT 1 FROM agent_insight_jobs"
            " WHERE id=:job AND lease_owner=:worker AND fence=:fence"
            " AND state='running' AND cancel_requested=false AND lease_deadline>now() FOR UPDATE"
        ),
        {"job": job_id, "worker": worker_id, "fence": fence},
    )
    if live is None:
        from app.demand_insights.errors import InsightsUnavailable

        raise InsightsUnavailable("作业租约已失效", code="stale_lease")


async def extend_lease(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    fence: int,
    lease_seconds: int = 120,
) -> bool:
    result = await session.execute(
        text(
            """
            UPDATE agent_insight_jobs
               SET lease_deadline=now() + make_interval(secs => :lease),
                   updated_at=now()
             WHERE id=:job AND lease_owner=:worker AND fence=:fence
               AND state='running' AND cancel_requested=false AND lease_deadline > now()
            """
        ),
        {"job": job_id, "worker": worker_id, "fence": fence, "lease": lease_seconds},
    )
    return result.rowcount == 1


async def update_progress(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    fence: int,
    processed_delta: int,
    classified_delta: int,
    unclassified_delta: int,
    failed_delta: int,
    usage_tokens: int | None,
    estimated_tokens: int,
    cursor_admitted_at: dt.datetime | None,
    cursor_request_id: uuid.UUID | None,
) -> bool:
    result = await session.execute(
        text(
            """
            UPDATE agent_insight_jobs
               SET processed_sources=processed_sources+:processed,
                   classified_intents=classified_intents+:classified,
                   unclassified_intents=unclassified_intents+:unclassified,
                   failed_sources=failed_sources+:failed,
                   estimated_tokens=estimated_tokens+:estimated,
                   actual_tokens=CASE WHEN CAST(:usage AS bigint) IS NULL
                                      THEN actual_tokens
                                      ELSE COALESCE(actual_tokens,0)+CAST(:usage AS bigint) END,
                   usage_missing=usage_missing OR CAST(:usage AS bigint) IS NULL,
                   cursor_admitted_at=CASE
                     WHEN CAST(:cursor_at AS timestamptz) IS NULL THEN cursor_admitted_at
                     WHEN cursor_admitted_at IS NULL
                          OR CAST(:cursor_at AS timestamptz) > cursor_admitted_at
                       THEN CAST(:cursor_at AS timestamptz)
                     ELSE cursor_admitted_at END,
                   cursor_request_id=CASE
                     WHEN CAST(:cursor_id AS uuid) IS NULL THEN cursor_request_id
                     WHEN cursor_admitted_at IS NULL
                          OR (CAST(:cursor_at AS timestamptz),CAST(:cursor_id AS uuid))
                             > (cursor_admitted_at,cursor_request_id)
                       THEN CAST(:cursor_id AS uuid)
                     ELSE cursor_request_id END,
                   updated_at=now()
             WHERE id=:job AND lease_owner=:worker AND fence=:fence
               AND state='running' AND cancel_requested=false AND lease_deadline > now()
            """
        ),
        {
            "job": job_id,
            "worker": worker_id,
            "fence": fence,
            "processed": processed_delta,
            "classified": classified_delta,
            "unclassified": unclassified_delta,
            "failed": failed_delta,
            "usage": usage_tokens,
            "estimated": estimated_tokens,
            "cursor_at": cursor_admitted_at,
            "cursor_id": cursor_request_id,
        },
    )
    return result.rowcount == 1


async def release_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    fence: int,
    state: str,
    error_code: str | None = None,
) -> bool:
    if state not in (
        "pending",
        "completed",
        "cancelled",
        "failed",
        "paused_budget",
        "paused_failure",
        "paused_authorization",
    ):
        state = "pending"
    result = await session.execute(
        text(
            """
            UPDATE agent_insight_jobs
               SET state=:state, lease_owner=NULL, lease_deadline=NULL,
                   state_revision=state_revision+1,
                   last_error_code=:error,
                   paused_at=CASE WHEN :state LIKE 'paused%' THEN now() ELSE paused_at END,
                   completed_at=CASE WHEN :state IN ('completed','cancelled','failed')
                                     THEN now() ELSE completed_at END,
                   updated_at=now()
             WHERE id=:job AND lease_owner=:worker AND fence=:fence
            """
        ),
        {
            "job": job_id,
            "worker": worker_id,
            "fence": fence,
            "state": state,
            "error": error_code,
        },
    )
    return result.rowcount == 1


def budget_exhausted(job: dict[str, Any], settings: InsightsSettings) -> bool:
    max_tokens = int(job.get("max_tokens") or settings.max_tokens_per_job)
    used = job.get("actual_tokens")
    if used is not None and int(used) >= max_tokens:
        return True
    if int(job.get("estimated_tokens") or 0) >= max_tokens:
        return True
    return int(job.get("processed_sources") or 0) >= int(
        job.get("max_sources") or settings.max_sources_per_job
    )


async def create_successor_job(
    session: AsyncSession,
    *,
    job: dict[str, Any],
    settings: InsightsSettings,
) -> uuid.UUID | None:
    """持续增量：在当前委托仍有效时创建后继作业；不复制凭据."""
    delegation = (
        (
            await session.execute(
                text(
                    "SELECT status, expires_at FROM agent_insight_delegations"
                    " WHERE id=:id AND tenant_id=:tenant"
                ),
                {"id": job["delegation_id"], "tenant": job["tenant_id"]},
            )
        )
        .mappings()
        .first()
    )
    if delegation is None or delegation["status"] != "active":
        return None
    now = dt.datetime.now(dt.UTC)
    if delegation["expires_at"] <= now:
        return None
    start = job["cursor_admitted_at"] or job["window_start"]
    predecessor = job["id"]
    successor_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_jobs
                (id, tenant_id, profile_id, delegation_id, requested_by, job_kind,
                 state, window_start, window_end, as_of, batch_size, max_sources,
                 max_tokens, authorization_expires_at, continuous, predecessor_job_id,
                 created_at, updated_at)
            VALUES (:id, :tenant, :profile, :delegation, :requester, 'incremental',
                    'pending', :start, :end, :as_of, :batch, :max_sources, :max_tokens,
                    :authorization_expires, true, :predecessor, :now, :now)
            """
        ),
        {
            "id": successor_id,
            "tenant": job["tenant_id"],
            "profile": job["profile_id"],
            "delegation": job["delegation_id"],
            "requester": job["requested_by"],
            "start": start,
            "end": now,
            "as_of": now,
            "batch": job["batch_size"],
            "max_sources": job["max_sources"],
            "max_tokens": job["max_tokens"],
            "authorization_expires": job["authorization_expires_at"],
            "predecessor": predecessor,
            "now": now,
        },
    )
    return successor_id


def encode_job(row: dict[str, Any]) -> dict[str, Any]:
    """安全 DTO：不含 delegation_id/handle/token，只保留到期时间."""
    result = dict(row)
    if result.get("id") is not None:
        result["job_id"] = str(result.pop("id"))
    result.pop("profile_id", None)
    result.pop("delegation_id", None)
    for key in (
        "created_at",
        "started_at",
        "paused_at",
        "completed_at",
        "authorization_expires_at",
        "window_start",
        "window_end",
        "as_of",
        "lease_deadline",
    ):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


def canonical_digest(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
