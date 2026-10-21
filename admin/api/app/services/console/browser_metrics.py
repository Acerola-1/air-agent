"""按回答/尝试队列计算浏览器体验，不把事件时间或缺事件当成测量。"""

import datetime as dt

from sqlalchemy import text

from app.services.interaction_policy import enabled as collection_enabled
from app.services.interaction_policy import enabled_at


async def browser_metrics(session, *, tenant, start, end, as_of, filters):
    enabled = enabled_at() if collection_enabled() else None
    params = {
        "tenant": tenant,
        "start": start,
        "end": end,
        "as_of": as_of,
        "enabled": enabled or dt.datetime.max.replace(tzinfo=dt.UTC),
    }
    where = [
        "r.tenant_id=:tenant",
        (
            "NOT EXISTS (SELECT 1 FROM agent_thread_deletions d WHERE "
            "d.tenant_id=r.tenant_id AND d.thread_id=r.thread_id)"
        ),
    ]
    for field in ("business_code", "graph_id"):
        if filters.get(field):
            expr = (
                "COALESCE(r.business_code,r.graph_id)" if field == "business_code" else "r.graph_id"
            )
            where.append(f"{expr}=:{field}")
            params[field] = filters[field]
    guard = " AND ".join(where)
    copy = (
        (
            await session.execute(
                text(f"""
        WITH eligible AS (
            SELECT a.id FROM agent_answers a
            JOIN agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
            JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id
            WHERE {guard} AND a.confirmed AND NOT a.partial
              AND a.confirmed_at>=:start AND a.confirmed_at<:end AND a.confirmed_at>=:enabled
        )
        SELECT count(*) AS copies,count(DISTINCT e.answer_id) AS answers,
               (SELECT count(*) FROM eligible) AS eligible
        FROM agent_events e JOIN eligible a ON a.id=e.answer_id
        WHERE e.tenant_id=:tenant AND e.event_type='browser.copy' AND e.received_at<=:as_of
    """),
                params,
            )
        )
        .mappings()
        .one()
    )
    timing = (
        (
            await session.execute(
                text(f"""
        WITH cohort AS (
          SELECT t.id,t.admitted_at FROM agent_run_attempts t
          JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id
          WHERE {guard} AND t.admitted_at>=:start AND t.admitted_at<:end
        ), measurements AS (
          SELECT DISTINCT ON (e.attempt_id,e.payload->>'measurement_id') e.attempt_id,e.payload
          FROM agent_events e JOIN cohort t ON t.id=e.attempt_id
          WHERE e.tenant_id=:tenant AND e.event_type='browser.timing'
            AND t.admitted_at>=:enabled AND e.received_at<=:as_of
          ORDER BY e.attempt_id,e.payload->>'measurement_id',e.received_at,e.event_id
        ), numbers AS (
          SELECT *,CASE WHEN jsonb_typeof(payload->'first_visible_answer_ms')='number'
                        THEN (payload->>'first_visible_answer_ms')::numeric END AS first_ms,
                   CASE WHEN jsonb_typeof(payload->'display_complete_ms')='number'
                        THEN (payload->>'display_complete_ms')::numeric END AS display_ms
          FROM measurements
        ), checked AS (
          SELECT *, COALESCE(payload->>'clock'='monotonic'
            AND length(payload->>'measurement_id')>0
            AND (payload->>'first_visible_answer_ms' IS NULL OR first_ms>=0)
            AND (payload->>'display_complete_ms' IS NULL OR display_ms>=0)
            AND (first_ms IS NULL OR display_ms IS NULL OR display_ms>=first_ms),false) AS valid
          FROM numbers
        )
        SELECT percentile_cont(.5) WITHIN GROUP(ORDER BY first_ms) FILTER(WHERE valid) AS p50,
               percentile_cont(.95) WITHIN GROUP(ORDER BY first_ms) FILTER(WHERE valid) AS p95,
               percentile_cont(.99) WITHIN GROUP(ORDER BY first_ms) FILTER(WHERE valid) AS p99,
               count(first_ms) FILTER(WHERE valid) AS samples,
               count(*) FILTER(WHERE NOT valid) AS invalid,
               (SELECT count(*) FROM cohort WHERE
               admitted_at>=:enabled)-count(DISTINCT attempt_id) FILTER(WHERE
               valid AND first_ms IS NOT NULL) AS missing,
               (SELECT count(*) FROM cohort WHERE admitted_at<:enabled) AS not_collected,
               percentile_cont(.5) WITHIN GROUP(ORDER BY display_ms) FILTER(WHERE
               valid AND payload->>'completion_state'='complete') AS display_p50,
               percentile_cont(.95) WITHIN GROUP(ORDER BY display_ms)
               FILTER(WHERE valid AND payload->>'completion_state'='complete') AS
               display_p95,
               percentile_cont(.99) WITHIN GROUP(ORDER BY display_ms)
               FILTER(WHERE valid AND payload->>'completion_state'='complete') AS
               display_p99,
               count(display_ms) FILTER(WHERE valid AND
               payload->>'completion_state'='complete') AS display_samples
        FROM checked
    """),
                params,
            )
        )
        .mappings()
        .one()
    )
    return dict(copy), {**dict(timing), "collection_enabled": enabled is not None}
