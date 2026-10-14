"""外部Trace删除独立投递：本地禁读即时生效，远端失败进入outbox重试/死信."""

from __future__ import annotations

import os

import httpx


class ExternalTraceCleanupUnavailable(RuntimeError):
    """未确认远端清理完成，调用方必须保留重试状态."""


async def cleanup_thread_traces(conn, task):
    """只删除已标记线程的已知项目Trace；远端回读为空才确认完成."""
    await conn.execute("SELECT set_config('air_agent.deletion_cleanup','on',true)")
    rows = await (
        await conn.execute(
            "SELECT l.provider,l.project_scope,l.trace_id,l.remote_observation_id "
            "FROM agent_observation_trace_links l JOIN agent_run_attempts a "
            "ON a.tenant_id=l.tenant_id AND a.id=l.attempt_id JOIN agent_requests r "
            "ON r.tenant_id=a.tenant_id AND r.id=a.request_id "
            "WHERE l.tenant_id=%s AND r.thread_id=%s",
            (task.tenant_id, task.thread_id),
        )
    ).fetchall()
    if not rows:
        return
    base = os.environ.get("LANGFUSE_BASE_URL", "")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    project = os.environ.get("LANGFUSE_PROJECT_SCOPE", "")
    traces = {}
    for row in rows:
        if row["provider"] != "langfuse" or row["project_scope"] != project:
            raise ExternalTraceCleanupUnavailable("project_unconfigured")
        traces.setdefault(row["trace_id"], set()).add(row["remote_observation_id"])
    if not base or not public or not secret:
        raise ExternalTraceCleanupUnavailable("credentials_unconfigured")
    try:
        async with httpx.AsyncClient(timeout=2, auth=(public, secret)) as client:
            for trace_id, ids in traces.items():
                params = {"traceId": trace_id, "fields": "core,basic", "limit": 1000}
                before = await client.get(
                    base.rstrip("/") + "/api/public/v2/observations", params=params
                )
                before.raise_for_status()
                body = before.json()
                observations = body.get("data", [])
                if not isinstance(observations, list) or (body.get("meta") or {}).get(
                    "cursor"
                ):
                    raise ExternalTraceCleanupUnavailable("incomplete_readback")
                if any(
                    r.get("type") == "GENERATION" and r.get("id") not in ids
                    for r in observations
                ):
                    raise ExternalTraceCleanupUnavailable(
                        "trace_has_other_observations"
                    )
                if observations:
                    deleted = await client.delete(
                        base.rstrip("/") + "/api/public/traces/" + trace_id
                    )
                    if deleted.status_code not in {200, 202, 204}:
                        raise ExternalTraceCleanupUnavailable("delete_not_accepted")
                after = await client.get(
                    base.rstrip("/") + "/api/public/v2/observations", params=params
                )
                after.raise_for_status()
                if after.json().get("data") != []:
                    raise ExternalTraceCleanupUnavailable("delete_pending")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ExternalTraceCleanupUnavailable("remote_unavailable") from exc
