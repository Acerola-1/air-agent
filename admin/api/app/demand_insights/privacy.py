"""来源删除/到期与恢复后的清理.

派生文本、向量、归属与簇原型都不能比来源活得更久。分类写入在提交前复核
来源存活；本模块提供物理清理和维护/恢复脚本的入口。清理失败由调用方保留
待办并重试，读取路径持续依赖墓碑/有效期拒绝。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights import storage


async def _affected_clusters(
    session: AsyncSession, *, tenant_id: str, source_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    if not source_ids:
        return []
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT DISTINCT a.cluster_id
                FROM agent_question_assignments a
                JOIN agent_question_intents i
                  ON i.id=a.intent_id AND i.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant AND a.cluster_id IS NOT NULL
                  AND i.source_id = ANY(CAST(:sources AS uuid[]))
                """
                ),
                {"tenant": tenant_id, "sources": source_ids},
            )
        )
        .scalars()
        .all()
    )
    return [row for row in rows if row is not None]


async def purge_source_ids(
    session: AsyncSession, *, tenant_id: str, source_ids: list[uuid.UUID]
) -> dict[str, int]:
    if not source_ids:
        return {"sources": 0, "clusters_refreshed": 0}
    clusters = await _affected_clusters(session, tenant_id=tenant_id, source_ids=source_ids)
    result = await session.execute(
        text(
            "DELETE FROM agent_question_sources"
            " WHERE tenant_id=:tenant AND id = ANY(CAST(:sources AS uuid[]))"
        ),
        {"tenant": tenant_id, "sources": source_ids},
    )
    for cluster_id in clusters:
        await storage.refresh_cluster_prototype(session, tenant_id=tenant_id, cluster_id=cluster_id)
    return {"sources": result.rowcount or 0, "clusters_refreshed": len(clusters)}


async def purge_thread(session: AsyncSession, *, tenant_id: str, thread_id: str) -> dict[str, int]:
    """删除整个会话的派生分析；幂等，可重复执行."""
    source_rows = (
        (
            await session.execute(
                text(
                    "SELECT id FROM agent_question_sources"
                    " WHERE tenant_id=:tenant AND thread_id=:thread"
                ),
                {"tenant": tenant_id, "thread": thread_id},
            )
        )
        .scalars()
        .all()
    )
    result = await purge_source_ids(session, tenant_id=tenant_id, source_ids=list(source_rows))
    gaps = await session.execute(
        text(
            "DELETE FROM agent_question_source_gaps WHERE tenant_id=:tenant AND thread_id=:thread"
        ),
        {"tenant": tenant_id, "thread": thread_id},
    )
    result["gaps"] = gaps.rowcount or 0
    return result


async def purge_expired(
    session: AsyncSession, *, tenant_id: str, now: dt.datetime | None = None
) -> dict[str, int]:
    """清理已过保留期、已删除原文或墓碑会话的来源."""
    moment = now or dt.datetime.now(dt.UTC)
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT s.id
                FROM agent_question_sources s
                WHERE s.tenant_id=:tenant
                  AND (
                    (s.retention_until IS NOT NULL AND s.retention_until <= :now)
                    OR EXISTS (
                      SELECT 1 FROM agent_content_refs c
                      WHERE c.tenant_id=s.tenant_id AND c.request_id=s.request_id
                        AND (c.deleted_at IS NOT NULL
                             OR (c.retention_until IS NOT NULL AND c.retention_until <= :now))
                    )
                    OR EXISTS (
                      SELECT 1 FROM agent_thread_deletions d
                      WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                    )
                  )
                """
                ),
                {"tenant": tenant_id, "now": moment},
            )
        )
        .scalars()
        .all()
    )
    return await purge_source_ids(
        session, tenant_id=tenant_id, source_ids=[row for row in rows if row is not None]
    )


async def restore_guards(session: AsyncSession, *, tenant_id: str) -> dict[str, int]:
    """备份恢复后、开放读取前：过期委托/作业与失效派生内容先收敛."""
    now = dt.datetime.now(dt.UTC)
    delegations = await session.execute(
        text(
            "UPDATE agent_insight_delegations SET status='expired', revision=revision+1"
            " WHERE tenant_id=:tenant AND status='active' AND expires_at <= :now"
        ),
        {"tenant": tenant_id, "now": now},
    )
    jobs = await session.execute(
        text(
            "UPDATE agent_insight_jobs SET state='paused_authorization',"
            " state_revision=state_revision+1, last_error_code='delegation_restored_expired',"
            " lease_owner=NULL, lease_deadline=NULL, updated_at=:now"
            " WHERE tenant_id=:tenant AND (state IN ('pending','running')"
            " OR state LIKE 'paused_%')"
            " AND delegation_id IN (SELECT id FROM agent_insight_delegations"
            " WHERE tenant_id=:tenant AND status <> 'active')"
        ),
        {"tenant": tenant_id, "now": now},
    )
    purged = await purge_expired(session, tenant_id=tenant_id, now=now)
    return {
        "delegations_expired": delegations.rowcount or 0,
        "jobs_paused": jobs.rowcount or 0,
        **purged,
    }


async def thread_is_deleted(session: AsyncSession, *, tenant_id: str, thread_id: str) -> bool:
    return bool(
        await session.scalar(
            text(
                "SELECT 1 FROM agent_thread_deletions"
                " WHERE tenant_id=:tenant AND thread_id=:thread LIMIT 1"
            ),
            {"tenant": tenant_id, "thread": thread_id},
        )
    )
