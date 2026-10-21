"""榜单、主题、增长、覆盖与体验证据（design D6）.

口径：
- 原子意图次数来自当前 active assignment；请求/用户按来源 request/user 去重；
- 主题的 requests/users 直接从意图去重，不累加子簇；
- 占比分母 = 当前 profile 在窗口内已处理的全部原子意图（含未归类）；
- 增长只在同 profile/assignment revision、前期覆盖可比时返回；零基线 `new=true`
  且 `growth_rate=null`；未处理历史标 `not_comparable`，不以 0 补齐；
- 负反馈是请求级证据，不复制成每个意图的确定错误。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights.config import InsightsSettings
from app.demand_insights.errors import InsightsNotFound
from app.demand_insights.scope import InsightsBadCursor, InsightsWindow, encode_cursor

DELETED_GUARD = (
    "NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
    " WHERE d.tenant_id={alias}.tenant_id AND d.thread_id={alias}.thread_id)"
)


def _window_params(prefix: str, window: InsightsWindow) -> dict[str, Any]:
    return {f"{prefix}_start": window.start, f"{prefix}_end": window.end}


def _source_filter(filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("business_code"):
        clauses.append("s.business_code = :filter_business")
        params["filter_business"] = filters["business_code"]
    if filters.get("graph_id"):
        clauses.append("s.graph_id = :filter_graph")
        params["filter_graph"] = filters["graph_id"]
    return ("AND " + " AND ".join(clauses) if clauses else "", params)


def _intent_filter(
    filters: dict[str, Any], *, cluster_alias: str = "c"
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.get("action"):
        clauses.append("i.action = :filter_action")
        params["filter_action"] = filters["action"]
    if filters.get("pollutant"):
        clauses.append("i.slots->>'pollutant' = :filter_pollutant")
        params["filter_pollutant"] = filters["pollutant"]
    if filters.get("city_code"):
        clauses.append("i.slots->>'city_code' = :filter_city")
        params["filter_city"] = filters["city_code"]
    if filters.get("topic_id"):
        clauses.append(f"{cluster_alias}.topic_id = :filter_topic")
        params["filter_topic"] = uuid.UUID(str(filters["topic_id"]))
    return ("AND " + " AND ".join(clauses) if clauses else "", params)


async def load_counts(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
) -> dict[str, Any]:
    params = {
        "tenant": tenant_id,
        "profile": profile_id,
        "av": analysis_version,
        **_window_params("w", window),
    }
    eligible = int(
        await session.scalar(
            text(
                f"""
                SELECT count(DISTINCT r.id)
                FROM agent_requests r
                JOIN agent_submissions s
                  ON s.tenant_id=r.tenant_id AND s.request_id=r.id
                 AND s.admission_status='confirmed'
                WHERE r.tenant_id=:tenant
                  AND r.admitted_at >= :w_start AND r.admitted_at < :w_end
                  AND {DELETED_GUARD.format(alias="r")}
                """
            ),
            params,
        )
        or 0
    )
    source_row = (
        (
            await session.execute(
                text(
                    f"""
                SELECT
                  count(DISTINCT s.request_id) FILTER (WHERE s.status='processed') AS processed,
                  count(DISTINCT s.request_id) FILTER (WHERE s.status='failed') AS failed,
                  count(DISTINCT s.request_id) FILTER (WHERE s.status='processing') AS pending,
                  max(s.admitted_at) FILTER (WHERE s.status='processed') AS cutoff
                FROM agent_question_sources s
                WHERE s.tenant_id=:tenant AND s.profile_id=:profile
                  AND s.analysis_version=:av
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND {DELETED_GUARD.format(alias="s")}
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    gap_row = (
        (
            await session.execute(
                text(
                    f"""
                SELECT
                  count(*) FILTER (WHERE g.reason='pending_confirmation') AS pending_confirmation,
                  count(*) FILTER (WHERE g.reason='missing_content') AS missing_content,
                  count(*) FILTER (WHERE g.reason IN ('expired_content','deleted'))
                    AS expired_or_deleted,
                  count(*) FILTER (WHERE g.reason IN
                    ('sensitive_content','unresolved_context','too_long')) AS review_required
                FROM agent_question_source_gaps g
                WHERE g.tenant_id=:tenant AND g.profile_id=:profile
                  AND g.analysis_version=:av
                  AND g.admitted_at >= :w_start AND g.admitted_at < :w_end
                  AND {DELETED_GUARD.format(alias="g")}
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    intent_row = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  count(*) FILTER (WHERE a.cluster_id IS NOT NULL) AS classified,
                  count(*) FILTER (WHERE a.cluster_id IS NULL) AS unclassified
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND s.status='processed'
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    global_cutoff = await session.scalar(
        text(
            """
            SELECT max(s.admitted_at)
            FROM agent_question_sources s
            WHERE s.tenant_id=:tenant AND s.profile_id=:profile
              AND s.analysis_version=:av AND s.status='processed'
            """
        ),
        {"tenant": tenant_id, "profile": profile_id, "av": analysis_version},
    )
    feedback_row = (
        (
            await session.execute(
                text(
                    f"""
                SELECT
                  count(DISTINCT f.request_id) FILTER (WHERE f.rating='negative') AS negative,
                  count(DISTINCT f.request_id) FILTER (WHERE f.rating IN ('positive','negative'))
                    AS evaluated
                FROM agent_feedback_current f
                WHERE f.tenant_id=:tenant
                  AND f.request_id IN (
                    SELECT r.id FROM agent_requests r
                    WHERE r.tenant_id=:tenant
                      AND r.admitted_at >= :w_start AND r.admitted_at < :w_end
                      AND {DELETED_GUARD.format(alias="r")}
                  )
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    attempt_row = (
        (
            await session.execute(
                text(
                    f"""
                WITH latest AS (
                  SELECT DISTINCT ON (r.id) r.id, a.state
                  FROM agent_requests r
                  JOIN agent_run_attempts a
                    ON a.request_id=r.id AND a.tenant_id=r.tenant_id
                  WHERE r.tenant_id=:tenant
                    AND r.admitted_at >= :w_start AND r.admitted_at < :w_end
                    AND {DELETED_GUARD.format(alias="r")}
                  ORDER BY r.id, a.attempt_index DESC
                )
                SELECT
                  count(*) FILTER (WHERE state IN ('failed','timed_out','status_unknown'))
                    AS technical_failure,
                  count(*) FILTER (WHERE state='cancelled') AS stopped
                FROM latest
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    processed = int(source_row["processed"] or 0) if source_row else 0
    classified = int(intent_row["classified"] or 0) if intent_row else 0
    unclassified = int(intent_row["unclassified"] or 0) if intent_row else 0
    total_intents = classified + unclassified
    coverage = round(processed / eligible, 6) if eligible else None
    return {
        "eligible_requests": eligible,
        "processed_requests": processed,
        "pending_requests": int(source_row["pending"] or 0) if source_row else 0,
        "failed_requests": int(source_row["failed"] or 0) if source_row else 0,
        "missing_content_requests": int(gap_row["missing_content"] or 0) if gap_row else 0,
        "pending_confirmation_requests": int(gap_row["pending_confirmation"] or 0)
        if gap_row
        else 0,
        "expired_or_deleted_requests": int(gap_row["expired_or_deleted"] or 0) if gap_row else 0,
        "review_required_requests": int(gap_row["review_required"] or 0) if gap_row else 0,
        "classified_intents": classified,
        "unclassified_intents": unclassified,
        "total_intents": total_intents,
        "coverage": coverage,
        "negative_feedback_requests": int(feedback_row["negative"] or 0) if feedback_row else 0,
        "evaluated_requests": int(feedback_row["evaluated"] or 0) if feedback_row else 0,
        "feedback_coverage": round(int(feedback_row["evaluated"] or 0) / processed, 6)
        if feedback_row and processed
        else None,
        "technical_failure_requests": int(attempt_row["technical_failure"] or 0)
        if attempt_row
        else 0,
        "stopped_requests": int(attempt_row["stopped"] or 0) if attempt_row else 0,
        "source_cutoff": global_cutoff,
    }


async def _aggregate(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
    group: str,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 cluster 或 topic 聚合；主题直接去重，不累加子簇."""
    filters = filters or {}
    source_clause, source_params = _source_filter(filters)
    intent_clause, intent_params = _intent_filter(filters)
    if group == "cluster":
        select_key = (
            "c.id AS key, c.canonical_name AS name, c.status, c.topic_id, t.name_zh AS topic_name"
        )
        join = (
            "JOIN agent_question_clusters c ON c.id=a.cluster_id AND c.tenant_id=a.tenant_id"
            " LEFT JOIN agent_question_topics t ON t.id=c.topic_id AND t.tenant_id=c.tenant_id"
        )
        group_by = "c.id, c.canonical_name, c.status, c.topic_id, t.name_zh"
    else:
        select_key = (
            "t.id AS key, t.name_zh AS name, 'active' AS status,"
            " t.id AS topic_id, t.name_zh AS topic_name"
        )
        join = (
            "JOIN agent_question_clusters c ON c.id=a.cluster_id AND c.tenant_id=a.tenant_id"
            " JOIN agent_question_topics t ON t.id=c.topic_id AND t.tenant_id=c.tenant_id"
        )
        group_by = "t.id, t.name_zh"
    rows = (
        (
            await session.execute(
                text(
                    f"""
                SELECT {select_key},
                       count(*) AS atomic_intent_count,
                       count(DISTINCT i.request_id) AS distinct_requests,
                       count(DISTINCT i.user_id) AS distinct_users
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                 AND a.cluster_id IS NOT NULL
                {join}
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  {source_clause}
                  {intent_clause}
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                GROUP BY {group_by}
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    **_window_params("w", window),
                    **source_params,
                    **intent_params,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def filtered_intent_total(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
    filters: dict[str, Any] | None = None,
) -> int:
    """当前过滤条件下的全部已处理原子意图（含未归类），作为 share 分母."""
    filters = filters or {}
    source_clause, source_params = _source_filter(filters)
    intent_clause, intent_params = _intent_filter(filters)
    value = await session.scalar(
        text(
            f"""
            SELECT count(*)
            FROM agent_question_intents i
            JOIN agent_question_sources s
              ON s.id=i.source_id AND s.tenant_id=i.tenant_id
            JOIN agent_question_assignments a
              ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
             AND a.profile_id=:profile AND a.status='active'
            LEFT JOIN agent_question_clusters c
              ON c.id=a.cluster_id AND c.tenant_id=a.tenant_id
            WHERE i.tenant_id=:tenant AND i.analysis_version=:av
              AND s.status='processed'
              AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
              {source_clause}
              {intent_clause}
              AND NOT EXISTS (
                SELECT 1 FROM agent_thread_deletions d
                WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
              )
            """
        ),
        {
            "tenant": tenant_id,
            "profile": profile_id,
            "av": analysis_version,
            **_window_params("w", window),
            **source_params,
            **intent_params,
        },
    )
    return int(value or 0)


async def _feedback_by_cluster(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
) -> dict[str, int]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT a.cluster_id::text AS cluster_id,
                       count(DISTINCT f.request_id) AS negative_requests
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                 AND a.cluster_id IS NOT NULL
                JOIN agent_feedback_current f
                  ON f.tenant_id=i.tenant_id AND f.request_id=i.request_id
                 AND f.rating='negative'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                GROUP BY a.cluster_id
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .all()
    )
    return {row["cluster_id"]: int(row["negative_requests"]) for row in rows}


async def question_ranking(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    assignment_revision: int,
    context_version: int,
    filter_key: str,
    window: InsightsWindow,
    previous: InsightsWindow | None,
    rank: str,
    total_intents: int,
    settings: InsightsSettings,
    cursor_key: str | None,
    limit: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    current = await _aggregate(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
        group="cluster",
        filters=filters,
    )
    previous_counts: dict[str, int] = {}
    previous_comparable = False
    if previous is not None:
        previous_counts_rows = await _aggregate(
            session,
            tenant_id=tenant_id,
            profile_id=profile_id,
            analysis_version=analysis_version,
            window=previous,
            group="cluster",
            filters=filters,
        )
        previous_counts = {
            str(row["key"]): int(row["atomic_intent_count"]) for row in previous_counts_rows
        }
        prev_counts = await load_counts(
            session,
            tenant_id=tenant_id,
            profile_id=profile_id,
            analysis_version=analysis_version,
            window=previous,
        )
        previous_comparable = is_comparable(prev_counts, previous, settings)
    negative = await _feedback_by_cluster(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
    )
    items: list[dict[str, Any]] = []
    for row in current:
        key = str(row["key"])
        current_count = int(row["atomic_intent_count"])
        previous_count = previous_counts.get(key, 0) if previous is not None else None
        status = (
            "not_requested"
            if previous is None
            else ("comparable" if previous_comparable else "not_comparable")
        )
        is_new = bool(
            previous is not None
            and previous_comparable
            and previous_count == 0
            and current_count > 0
        )
        growth_rate = None
        if (
            previous is not None
            and previous_comparable
            and previous_count is not None
            and previous_count > 0
        ):
            growth_rate = round((current_count - previous_count) / previous_count, 6)
        items.append(
            {
                "cluster_id": str(row["key"]),
                "canonical_name": row["name"],
                "status": row["status"],
                "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
                "topic_name": row["topic_name"],
                "atomic_intent_count": current_count,
                "distinct_requests": int(row["distinct_requests"]),
                "distinct_users": int(row["distinct_users"]),
                "share": {
                    "value": round(current_count / total_intents, 6) if total_intents else None,
                    "numerator": current_count,
                    "denominator": total_intents or None,
                },
                "previous_count": previous_count,
                "growth_rate": growth_rate,
                "new": is_new,
                "comparison_status": status,
                "request_level_negative_feedback": negative.get(key, 0),
                "feedback_coverage": {
                    "numerator": negative.get(key, 0),
                    "denominator": int(row["distinct_requests"]) or None,
                },
            }
        )
    sort_key = {
        "intents": "atomic_intent_count",
        "users": "distinct_users",
        "requests": "distinct_requests",
    }[rank]
    items.sort(key=lambda item: (-int(item[sort_key]), item["cluster_id"]), reverse=False)
    if cursor_key:
        cursor_value, cursor_id = _decode_rank_cursor(cursor_key)
        items = [
            item
            for item in items
            if int(item[sort_key]) < cursor_value
            or (int(item[sort_key]) == cursor_value and item["cluster_id"] > cursor_id)
        ]
    page = items[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            tenant_id=tenant_id,
            context_version=context_version,
            filter_key=filter_key,
            rank=rank,
            analysis_version=analysis_version,
            assignment_revision=assignment_revision,
            window=window,
            key=f"{int(last[sort_key])}|{last['cluster_id']}",
        )
    return {
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": limit,
        "sort": sort_key,
        "previous_comparable": previous_comparable,
        "filters": filters,
    }


async def topic_ranking(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    assignment_revision: int,
    context_version: int,
    filter_key: str,
    window: InsightsWindow,
    total_intents: int,
    rank: str,
    cursor_key: str | None,
    limit: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    rows = await _aggregate(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
        group="topic",
        filters=filters,
    )
    items = [
        {
            "topic_id": str(row["key"]),
            "name": row["name"],
            "status": row["status"],
            "atomic_intent_count": int(row["atomic_intent_count"]),
            "distinct_requests": int(row["distinct_requests"]),
            "distinct_users": int(row["distinct_users"]),
            "share": {
                "value": round(int(row["atomic_intent_count"]) / total_intents, 6)
                if total_intents
                else None,
                "numerator": int(row["atomic_intent_count"]),
                "denominator": total_intents or None,
            },
        }
        for row in rows
    ]
    sort_key = {
        "intents": "atomic_intent_count",
        "users": "distinct_users",
        "requests": "distinct_requests",
    }[rank]
    items.sort(key=lambda item: (-int(item[sort_key]), item["topic_id"]))
    if cursor_key:
        cursor_value, cursor_id = _decode_rank_cursor(cursor_key)
        items = [
            item
            for item in items
            if int(item[sort_key]) < cursor_value
            or (int(item[sort_key]) == cursor_value and item["topic_id"] > cursor_id)
        ]
    page = items[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            tenant_id=tenant_id,
            context_version=context_version,
            filter_key=filter_key,
            rank=rank,
            analysis_version=analysis_version,
            assignment_revision=assignment_revision,
            window=window,
            key=f"{int(last[sort_key])}|{last['topic_id']}",
        )
    return {
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": limit,
        "sort": sort_key,
        "filters": filters,
    }


def _decode_rank_cursor(cursor: str) -> tuple[int, str]:
    try:
        value, cluster_id = cursor.rsplit("|", 1)
        return int(value), cluster_id
    except (ValueError, AttributeError) as exc:
        raise InsightsBadCursor("游标非法", code="bad_cursor") from exc


def is_comparable(
    counts: dict[str, Any], window: InsightsWindow, settings: InsightsSettings
) -> bool:
    coverage = counts.get("coverage")
    cutoff = counts.get("source_cutoff")
    if coverage is not None and coverage >= settings.coverage_comparable_threshold:
        return True
    if counts.get("eligible_requests") == 0 and cutoff is not None and cutoff >= window.end:
        return True
    return False


async def question_detail(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    assignment_revision: int,
    window: InsightsWindow,
    cluster_id: uuid.UUID,
    settings: InsightsSettings,
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT c.id, c.canonical_name, c.action, c.object, c.time_kind,
                       c.negation, c.constraints, c.status, c.merged_into, c.revision,
                       c.representative_intent_id, c.created_at, c.updated_at,
                       t.id AS topic_id, t.name_zh AS topic_name
                FROM agent_question_clusters c
                LEFT JOIN agent_question_topics t
                  ON t.id=c.topic_id AND t.tenant_id=c.tenant_id
                WHERE c.tenant_id=:tenant AND c.id=:id AND c.profile_id=:profile
                """
                ),
                {"tenant": tenant_id, "id": cluster_id, "profile": profile_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InsightsNotFound("标准问题不存在", code="cluster_not_found")
    if row["merged_into"] is not None:
        return {
            "cluster_id": str(row["id"]),
            "canonical_name": row["canonical_name"],
            "status": row["status"],
            "merged_into": str(row["merged_into"]),
            "statistics": None,
            "trend": [],
        }
    counts = await load_counts(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
    )
    stats = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS atomic_intent_count,
                       count(DISTINCT i.request_id) AS distinct_requests,
                       count(DISTINCT i.user_id) AS distinct_users
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND a.cluster_id=:cluster
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "cluster": cluster_id,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .first()
    )
    trend_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT date_trunc('day', s.admitted_at AT TIME ZONE 'Asia/Shanghai')::date
                         AS day,
                       count(*) AS intents,
                       count(DISTINCT i.request_id) AS requests,
                       count(DISTINCT i.user_id) AS users
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND a.cluster_id=:cluster
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                GROUP BY 1 ORDER BY 1
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "cluster": cluster_id,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .all()
    )
    total = int(stats["atomic_intent_count"] or 0) if stats else 0
    negative_count = int(
        await session.scalar(
            text(
                """
                SELECT count(DISTINCT f.request_id)
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                JOIN agent_feedback_current f
                  ON f.tenant_id=i.tenant_id AND f.request_id=i.request_id
                 AND f.rating='negative'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND a.cluster_id=:cluster
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                """
            ),
            {
                "tenant": tenant_id,
                "profile": profile_id,
                "av": analysis_version,
                "cluster": cluster_id,
                **_window_params("w", window),
            },
        )
        or 0
    )
    return {
        "cluster_id": str(row["id"]),
        "canonical_name": row["canonical_name"],
        "action": row["action"],
        "object": row["object"],
        "time_kind": row["time_kind"],
        "negation": row["negation"],
        "constraints": row["constraints"],
        "status": row["status"],
        "revision": int(row["revision"]),
        "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
        "topic_name": row["topic_name"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "statistics": {
            "atomic_intent_count": total,
            "distinct_requests": int(stats["distinct_requests"] or 0) if stats else 0,
            "distinct_users": int(stats["distinct_users"] or 0) if stats else 0,
            "share": {
                "value": round(total / counts["total_intents"], 6)
                if counts["total_intents"]
                else None,
                "numerator": total,
                "denominator": counts["total_intents"] or None,
            },
            "request_level_negative_feedback": negative_count,
        },
        "summary": (
            f"{row['canonical_name']}：窗口内 {total} 个原子意图、"
            f"{int(stats['distinct_requests'] or 0) if stats else 0} 个请求；"
            "摘要仅引用当前统计，不推断原因。"
        ),
        "summary_source": "deterministic",
        "trend": [
            {
                "day": trend["day"].isoformat()
                if hasattr(trend["day"], "isoformat")
                else str(trend["day"]),
                "intents": int(trend["intents"]),
                "requests": int(trend["requests"]),
                "users": int(trend["users"]),
            }
            for trend in trend_rows
        ],
        "coverage": {
            "eligible_requests": counts["eligible_requests"],
            "processed_requests": counts["processed_requests"],
            "coverage": counts["coverage"],
        },
    }


async def list_question_sources(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    cluster_id: uuid.UUID,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT i.id AS intent_id, i.request_id, i.split_index, i.action, i.object,
                       i.time_kind, i.negation, i.slots, i.confidence, ic.summary,
                       s.admitted_at, s.source_content_version, s.thread_id,
                       s.graph_id, s.business_code, at.id AS attempt_id
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_intent_contents ic
                  ON ic.intent_id=i.id AND ic.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                LEFT JOIN LATERAL (
                  SELECT t.id FROM agent_run_attempts t
                  WHERE t.tenant_id=s.tenant_id AND t.request_id=s.request_id
                  ORDER BY t.attempt_index DESC LIMIT 1
                ) at ON true
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND a.cluster_id=:cluster
                  AND s.status='processed'
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                ORDER BY s.admitted_at DESC, i.request_id, i.split_index
                LIMIT :limit OFFSET :offset
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "cluster": cluster_id,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "intent_id": str(row["intent_id"]),
            "request_id": str(row["request_id"]),
            "split_index": int(row["split_index"]),
            "intent_summary": row["summary"],
            "action": row["action"],
            "object": row["object"],
            "time_kind": row["time_kind"],
            "negation": row["negation"],
            "slots": row["slots"],
            "confidence": float(row["confidence"]),
            "admitted_at": row["admitted_at"].isoformat(),
            "content_version": int(row["source_content_version"]),
            "graph_id": row["graph_id"],
            "business_code": row["business_code"],
            "attempt_id": str(row["attempt_id"]) if row["attempt_id"] else None,
        }
        for row in rows
    ]


async def list_unclassified(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT i.id AS intent_id, i.request_id, i.split_index, i.action, i.object,
                       i.time_kind, i.negation, i.slots, i.confidence, ic.summary,
                       a.unclassified_reason, s.admitted_at, s.source_content_version,
                       s.thread_id, s.graph_id, s.business_code, at.id AS attempt_id
                FROM agent_question_intents i
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                JOIN agent_question_intent_contents ic
                  ON ic.intent_id=i.id AND ic.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                LEFT JOIN LATERAL (
                  SELECT t.id FROM agent_run_attempts t
                  WHERE t.tenant_id=s.tenant_id AND t.request_id=s.request_id
                  ORDER BY t.attempt_index DESC LIMIT 1
                ) at ON true
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND a.cluster_id IS NULL
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_thread_deletions d
                    WHERE d.tenant_id=s.tenant_id AND d.thread_id=s.thread_id
                  )
                ORDER BY s.admitted_at DESC, i.request_id, i.split_index
                LIMIT :limit OFFSET :offset
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "limit": limit,
                    "offset": offset,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "intent_id": str(row["intent_id"]),
            "request_id": str(row["request_id"]),
            "split_index": int(row["split_index"]),
            "intent_summary": row["summary"],
            "action": row["action"],
            "object": row["object"],
            "time_kind": row["time_kind"],
            "negation": row["negation"],
            "slots": row["slots"],
            "confidence": float(row["confidence"]),
            "unclassified_reason": row["unclassified_reason"],
            "admitted_at": row["admitted_at"].isoformat(),
            "content_version": int(row["source_content_version"]),
            "graph_id": row["graph_id"],
            "business_code": row["business_code"],
            "attempt_id": str(row["attempt_id"]) if row["attempt_id"] else None,
        }
        for row in rows
    ]


async def list_gaps(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT request_id, reason, detail, attempts, admitted_at, thread_id
                FROM agent_question_source_gaps
                WHERE tenant_id=:tenant AND profile_id=:profile
                  AND analysis_version=:av
                  AND admitted_at >= :w_start AND admitted_at < :w_end
                ORDER BY admitted_at DESC, request_id
                LIMIT :limit OFFSET :offset
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    "av": analysis_version,
                    "limit": limit,
                    "offset": offset,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "request_id": str(row["request_id"]),
            "reason": row["reason"],
            "detail": row["detail"],
            "attempts": int(row["attempts"]),
            "admitted_at": row["admitted_at"].isoformat(),
        }
        for row in rows
    ]


async def overview(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    window: InsightsWindow,
    settings: InsightsSettings,
) -> dict[str, Any]:
    counts = await load_counts(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
    )
    topic_rows = await _aggregate(
        session,
        tenant_id=tenant_id,
        profile_id=profile_id,
        analysis_version=analysis_version,
        window=window,
        group="topic",
    )
    reasons = (
        (
            await session.execute(
                text(
                    """
                SELECT a.unclassified_reason AS reason, count(*) AS count
                FROM agent_question_assignments a
                JOIN agent_question_intents i
                  ON i.id=a.intent_id AND i.tenant_id=a.tenant_id
                JOIN agent_question_sources s
                  ON s.id=i.source_id AND s.tenant_id=i.tenant_id
                WHERE a.tenant_id=:tenant AND a.profile_id=:profile
                  AND a.status='active' AND a.cluster_id IS NULL
                  AND s.status='processed'
                  AND s.admitted_at >= :w_start AND s.admitted_at < :w_end
                GROUP BY a.unclassified_reason
                ORDER BY count(*) DESC
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile_id,
                    **_window_params("w", window),
                },
            )
        )
        .mappings()
        .all()
    )
    return {
        "counts": counts,
        "topics": [
            {
                "topic_id": str(row["key"]),
                "name": row["name"],
                "atomic_intent_count": int(row["atomic_intent_count"]),
                "distinct_requests": int(row["distinct_requests"]),
                "distinct_users": int(row["distinct_users"]),
            }
            for row in sorted(topic_rows, key=lambda item: -int(item["atomic_intent_count"]))[:5]
        ],
        "unclassified_reasons": [
            {"reason": row["reason"], "count": int(row["count"])} for row in reasons
        ],
    }
