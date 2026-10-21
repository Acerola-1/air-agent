"""已确认受理 request 的精确来源读取与缺口重试.

- 来源单位是**逻辑提问 request**：同一 request 的 retry/resume/regenerate 不会
  增加来源；精确使用 `agent_content_refs.content_version`。
- 未确认（admitting/admission_unknown）、缺正文、过期、删除、墓碑分别记 gap，
  下次扫描/作业批次重新发现；不依赖单一 admitted_at 高水位。
- 只读受控快照；不读 checkpoint、Trace、溯源报告或共享向量库。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights.errors import InsightsNotFound


@dataclass(frozen=True)
class DiscoveredSource:
    request_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    thread_id: str
    graph_id: str
    business_code: str | None
    admitted_at: dt.datetime
    user_message_id: str | None
    content_version: int
    content_sha256: str
    retention_until: dt.datetime | None
    content: str


@dataclass(frozen=True)
class SourceGap:
    request_id: uuid.UUID
    user_id: uuid.UUID
    thread_id: str
    admitted_at: dt.datetime
    reason: str
    detail: str | None


@dataclass(frozen=True)
class SourceContent:
    text: str
    content_version: int
    content_sha256: str
    retention_until: dt.datetime | None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def discover_requests(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window_start: dt.datetime,
    window_end: dt.datetime | None,
    cursor_admitted_at: dt.datetime | None,
    cursor_request_id: uuid.UUID | None,
    limit: int,
) -> list[Any]:
    """按 (admitted_at,id) 分页列出尚未建立来源的 request；含确认/正文判断字段."""
    effective_end = window_end or dt.datetime.now(dt.UTC)
    cursor_clause = ""
    params: dict[str, Any] = {
        "tenant": tenant_id,
        "profile": profile_id,
        "av": analysis_version,
        "start": window_start,
        "end": effective_end,
        "limit": limit,
    }
    if cursor_admitted_at is not None and cursor_request_id is not None:
        cursor_clause = "AND (r.admitted_at, r.id) > (:cursor_at, :cursor_id)"
        params["cursor_at"] = cursor_admitted_at
        params["cursor_id"] = cursor_request_id
    rows = (
        (
            await session.execute(
                text(
                    f"""
                SELECT r.id AS request_id, r.user_id, r.membership_id, r.thread_id,
                       r.graph_id, r.business_code, r.admitted_at, r.user_message_id,
                       conf.confirmed AS has_confirmed_submission,
                       c.content_version, c.content_snapshot, c.retention_until,
                       c.deleted_at AS content_deleted_at, c.created_at AS content_created_at,
                       EXISTS (
                         SELECT 1 FROM agent_thread_deletions d
                         WHERE d.tenant_id=r.tenant_id AND d.thread_id=r.thread_id
                       ) AS thread_deleted
                FROM agent_requests r
                LEFT JOIN LATERAL (
                    SELECT true AS confirmed
                    FROM agent_submissions s
                    WHERE s.tenant_id=r.tenant_id AND s.request_id=r.id
                      AND s.admission_status='confirmed'
                    LIMIT 1
                ) conf ON true
                LEFT JOIN LATERAL (
                    SELECT c.content_version, c.content_snapshot, c.retention_until,
                           c.deleted_at, c.created_at
                    FROM agent_content_refs c
                    WHERE c.tenant_id=r.tenant_id AND c.request_id=r.id
                    ORDER BY c.content_version DESC, c.created_at DESC
                    LIMIT 1
                ) c ON true
                WHERE r.tenant_id=:tenant
                  AND r.admitted_at >= :start
                  AND r.admitted_at < :end
                  {cursor_clause}
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_question_sources s
                    WHERE s.tenant_id=r.tenant_id AND s.request_id=r.id
                      AND s.analysis_version=:av
                  )
                ORDER BY r.admitted_at, r.id
                LIMIT :limit
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return list(rows)


async def discover_gap_retries(
    session: AsyncSession,
    *,
    tenant_id: str,
    analysis_version: str,
    limit: int,
    max_attempts: int = 5,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
) -> list[Any]:
    """晚确认/晚补正文：不受高水位限制地重查缺口 request."""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT g.id AS gap_id, g.request_id, g.reason, g.attempts,
                       r.user_id, r.membership_id, r.thread_id, r.graph_id,
                       r.business_code, r.admitted_at, r.user_message_id,
                       conf.confirmed AS has_confirmed_submission,
                       c.content_version, c.content_snapshot, c.retention_until,
                       c.deleted_at AS content_deleted_at, c.created_at AS content_created_at,
                       EXISTS (
                         SELECT 1 FROM agent_thread_deletions d
                         WHERE d.tenant_id=r.tenant_id AND d.thread_id=r.thread_id
                       ) AS thread_deleted
                FROM agent_question_source_gaps g
                JOIN agent_requests r
                  ON r.id=g.request_id AND r.tenant_id=g.tenant_id
                LEFT JOIN LATERAL (
                    SELECT true AS confirmed
                    FROM agent_submissions s
                    WHERE s.tenant_id=r.tenant_id AND s.request_id=r.id
                      AND s.admission_status='confirmed'
                    LIMIT 1
                ) conf ON true
                LEFT JOIN LATERAL (
                    SELECT c.content_version, c.content_snapshot, c.retention_until,
                           c.deleted_at, c.created_at
                    FROM agent_content_refs c
                    WHERE c.tenant_id=r.tenant_id AND c.request_id=r.id
                    ORDER BY c.content_version DESC, c.created_at DESC
                    LIMIT 1
                ) c ON true
                WHERE g.tenant_id=:tenant
                  AND g.analysis_version=:av
                  AND g.reason IN ('pending_confirmation','missing_content')
                  AND g.attempts < :max_attempts
                  AND (CAST(:window_start AS timestamptz) IS NULL OR g.admitted_at>=:window_start)
                  AND (CAST(:window_end AS timestamptz) IS NULL OR g.admitted_at<:window_end)
                ORDER BY g.updated_at, g.id
                LIMIT :limit
                """
                ),
                {
                    "tenant": tenant_id,
                    "av": analysis_version,
                    "limit": limit,
                    "max_attempts": max_attempts,
                    "window_start": window_start,
                    "window_end": window_end,
                },
            )
        )
        .mappings()
        .all()
    )
    return list(rows)


def classify_discovered(
    row: Any, *, now: dt.datetime
) -> tuple[DiscoveredSource | None, str | None]:
    """把发现行分成可处理来源或缺口原因；不读取正文内容之外的信息."""
    if row["thread_deleted"]:
        return None, "deleted"
    if not row["has_confirmed_submission"]:
        return None, "pending_confirmation"
    if row["content_version"] is None or row["content_snapshot"] is None:
        return None, "missing_content"
    if row["content_deleted_at"] is not None:
        return None, "expired_content"
    if row["retention_until"] is not None and row["retention_until"] <= now:
        return None, "expired_content"
    content = str(row["content_snapshot"])
    if not content.strip():
        return None, "missing_content"
    return (
        DiscoveredSource(
            request_id=row["request_id"],
            user_id=row["user_id"],
            membership_id=row["membership_id"],
            thread_id=row["thread_id"],
            graph_id=row["graph_id"],
            business_code=row["business_code"],
            admitted_at=row["admitted_at"],
            user_message_id=row["user_message_id"],
            content_version=int(row["content_version"]),
            content_sha256=_sha256(content),
            retention_until=row["retention_until"],
            content=content,
        ),
        None,
    )


async def record_gap(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    row: Any,
    reason: str,
    detail: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO agent_question_source_gaps
                (id, tenant_id, profile_id, request_id, user_id, thread_id, admitted_at,
                 reason, detail, attempts, last_attempt_at, analysis_version,
                 created_at, updated_at)
            VALUES (gen_random_uuid(), :tenant, :profile, :request, :user, :thread, :admitted,
                    :reason, :detail, 1, now(), :av, now(), now())
            ON CONFLICT (tenant_id, request_id, analysis_version) DO UPDATE SET
                reason=EXCLUDED.reason,
                detail=EXCLUDED.detail,
                attempts=agent_question_source_gaps.attempts + 1,
                last_attempt_at=now(),
                updated_at=now()
            """
        ),
        {
            "tenant": tenant_id,
            "profile": profile_id,
            "request": row["request_id"],
            "user": row["user_id"],
            "thread": row["thread_id"],
            "admitted": row["admitted_at"],
            "reason": reason,
            "detail": detail[:200] if detail else None,
            "av": analysis_version,
        },
    )


async def save_source(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    source: DiscoveredSource,
) -> uuid.UUID:
    """幂等保存来源；同 request/内容版本/分析版本只保留一行."""
    existing = await session.scalar(
        text(
            """
            SELECT id FROM agent_question_sources
            WHERE tenant_id=:tenant AND request_id=:request
              AND source_content_version=:version AND analysis_version=:av
            """
        ),
        {
            "tenant": tenant_id,
            "request": source.request_id,
            "version": source.content_version,
            "av": analysis_version,
        },
    )
    if existing is not None:
        return existing
    row = (
        await session.execute(
            text(
                """
                INSERT INTO agent_question_sources
                    (id, tenant_id, profile_id, request_id, user_id, membership_id, thread_id,
                     graph_id, business_code, admitted_at, source_message_id,
                     source_content_version, source_sha256, retention_until, status,
                     analysis_version, created_at, updated_at)
                VALUES (gen_random_uuid(), :tenant, :profile, :request, :user, :membership,
                        :thread, :graph, :business, :admitted, :message, :version, :sha,
                        :retention, 'processing', :av, now(), now())
                ON CONFLICT (tenant_id, request_id, source_content_version, analysis_version)
                DO UPDATE SET updated_at=agent_question_sources.updated_at
                RETURNING id
                """
            ),
            {
                "tenant": tenant_id,
                "profile": profile_id,
                "request": source.request_id,
                "user": source.user_id,
                "membership": source.membership_id,
                "thread": source.thread_id,
                "graph": source.graph_id,
                "business": source.business_code,
                "admitted": source.admitted_at,
                "message": source.user_message_id,
                "version": source.content_version,
                "sha": source.content_sha256,
                "retention": source.retention_until,
                "av": analysis_version,
            },
        )
    ).scalar()
    if row is None:
        row = await session.scalar(
            text(
                """
                SELECT id FROM agent_question_sources
                WHERE tenant_id=:tenant AND request_id=:request
                  AND source_content_version=:version AND analysis_version=:av
                """
            ),
            {
                "tenant": tenant_id,
                "request": source.request_id,
                "version": source.content_version,
                "av": analysis_version,
            },
        )
    if row is None:
        raise InsightsNotFound("来源保存失败", code="source_not_saved")
    await session.execute(
        text(
            "DELETE FROM agent_question_source_gaps"
            " WHERE tenant_id=:tenant AND request_id=:request AND analysis_version=:av"
        ),
        {"tenant": tenant_id, "request": source.request_id, "av": analysis_version},
    )
    return row


async def load_source_content(
    session: AsyncSession,
    *,
    tenant_id: str,
    source: dict[str, Any],
    now: dt.datetime | None = None,
) -> SourceContent | None:
    """按精确 content_version 再读；删除/过期/墓碑优先返回 None."""
    moment = now or dt.datetime.now(dt.UTC)
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT c.content_snapshot, c.content_version, c.retention_until,
                       c.deleted_at, EXISTS (
                         SELECT 1 FROM agent_thread_deletions d
                         WHERE d.tenant_id=c.tenant_id AND d.thread_id=c.thread_id
                       ) AS thread_deleted
                FROM agent_content_refs c
                WHERE c.tenant_id=:tenant AND c.request_id=:request
                  AND c.content_version=:version
                  AND c.user_id=:user
                ORDER BY c.created_at DESC
                LIMIT 1
                """
                ),
                {
                    "tenant": tenant_id,
                    "request": source["request_id"],
                    "version": source["source_content_version"],
                    "user": source["user_id"],
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["content_snapshot"] is None:
        return None
    if row["thread_deleted"] or row["deleted_at"] is not None:
        return None
    if row["retention_until"] is not None and row["retention_until"] <= moment:
        return None
    return SourceContent(
        text=str(row["content_snapshot"]),
        content_version=int(row["content_version"]),
        content_sha256=_sha256(str(row["content_snapshot"])),
        retention_until=row["retention_until"],
    )


async def load_context_texts(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    thread_id: str,
    request_id: uuid.UUID,
    before: dt.datetime,
    limit: int = 2,
    max_chars: int = 8000,
) -> list[str]:
    """同企业/同用户/同 thread 最近至多 2 条原问题；只用于指代补全."""
    if limit <= 0 or max_chars <= 0:
        return []
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT c.content_snapshot, c.created_at
                FROM agent_content_refs c
                WHERE c.tenant_id=:tenant AND c.user_id=:user AND c.thread_id=:thread
                  AND c.request_id IS DISTINCT FROM :request
                  AND c.created_at < :before
                  AND c.content_snapshot IS NOT NULL
                  AND c.deleted_at IS NULL
                  AND (c.retention_until IS NULL OR c.retention_until > now())
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=c.tenant_id AND d.thread_id=c.thread_id
                  )
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT :limit
                """
                ),
                {
                    "tenant": tenant_id,
                    "user": user_id,
                    "thread": thread_id,
                    "request": request_id,
                    "before": before,
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    texts: list[str] = []
    total = 0
    for row in reversed(rows):
        value = str(row["content_snapshot"])
        if total + len(value) > max_chars:
            break
        texts.append(value)
        total += len(value)
    return texts


async def mark_source(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_id: uuid.UUID,
    status: str,
    failure_code: str | None = None,
    split_count: int = 0,
    next_attempt_at: dt.datetime | None = None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE agent_question_sources
               SET status=:status, failure_code=:failure, split_count=:split,
                   next_attempt_at=:next_attempt,
                   processed_at=CASE WHEN :status IN ('processed','failed','skipped')
                                     THEN now() ELSE processed_at END,
                   updated_at=now()
             WHERE tenant_id=:tenant AND id=:id
            """
        ),
        {
            "tenant": tenant_id,
            "id": source_id,
            "status": status,
            "failure": failure_code,
            "split": split_count,
            "next_attempt": next_attempt_at,
        },
    )


async def begin_source_attempt(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_id: uuid.UUID,
    max_attempts: int,
) -> int | None:
    """原子占用一次来源尝试；达到上限返回 None（不再无限重试）."""
    value = await session.scalar(
        text(
            """
            UPDATE agent_question_sources
               SET status='processing', attempts=attempts+1, updated_at=now()
             WHERE tenant_id=:tenant AND id=:id
               AND status IN ('processing','failed')
               AND attempts < :max_attempts
            RETURNING attempts
            """
        ),
        {
            "tenant": tenant_id,
            "id": source_id,
            "max_attempts": max_attempts,
        },
    )
    return int(value) if value is not None else None


async def load_retry_sources(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    limit: int,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
    max_attempts: int = 3,
) -> list[Any]:
    """重启/失败后重试：processing 残留与未达上限的 failed 来源."""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT id, request_id, user_id, membership_id, thread_id, graph_id,
                       business_code, admitted_at, source_message_id,
                       source_content_version, source_sha256, retention_until,
                       status, attempts
                FROM agent_question_sources
                WHERE tenant_id=:tenant AND profile_id=:profile
                  AND analysis_version=:av
                  AND status IN ('processing','failed') AND attempts < :max_attempts
                  AND (CAST(:window_start AS timestamptz) IS NULL OR admitted_at>=:window_start)
                  AND (CAST(:window_end AS timestamptz) IS NULL OR admitted_at<:window_end)
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY admitted_at, id
                LIMIT :limit
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "limit": limit,
                    "window_start": window_start,
                    "window_end": window_end,
                    "max_attempts": max_attempts,
                },
            )
        )
        .mappings()
        .all()
    )
    return list(rows)


async def source_live(
    session: AsyncSession, *, tenant_id: str, source_id: uuid.UUID
) -> dict[str, Any] | None:
    """提交前复核：来源未被删除/过期/墓碑，且仍存在."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT s.id, s.request_id, s.user_id, s.thread_id, s.source_content_version,
                       s.retention_until, s.status
                FROM agent_question_sources s
                WHERE s.tenant_id=:tenant AND s.id=:id
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                  AND (s.retention_until IS NULL OR s.retention_until > now())
                """
                ),
                {"tenant": tenant_id, "id": source_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None
