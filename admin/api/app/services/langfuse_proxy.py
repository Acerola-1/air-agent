"""服务端有界回读；只回传本地已授权的逐模型跨度和结构化计数。"""

from __future__ import annotations

import os
import re

import httpx


async def read_observations(trace_id: str, allowed_ids: set[str]) -> dict:
    """先由调用方核验本地归属；远端故障明确返回待同步而非伪成功。"""
    base = os.environ.get("LANGFUSE_BASE_URL", "")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not base or not public or not secret or not allowed_ids:
        return {"sync_state": "pending", "observations": [], "reason": "readback_unavailable"}
    try:
        async with httpx.AsyncClient(timeout=2, auth=(public, secret)) as client:
            response = await client.get(
                base.rstrip("/") + "/api/public/v2/observations",
                params={"traceId": trace_id, "fields": "core,basic,model,usage", "limit": 1000},
            )
            response.raise_for_status()
            body = response.json()
        rows = body.get("data", [])
        if not isinstance(rows, list):
            raise ValueError("invalid_shape")
        safe = []
        for row in rows:
            if row.get("id") not in allowed_ids or row.get("traceId") != trace_id:
                continue
            item = {
                k: row[k] for k in ("id", "traceId", "type", "startTime", "endTime") if k in row
            }
            model = row.get("providedModelName") or row.get("model")
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", model):
                item["model"] = model
            for field in ("usageDetails", "usage"):
                if isinstance(row.get(field), dict):
                    item[field] = {
                        k: v
                        for k, v in row[field].items()
                        if k
                        in {
                            "input",
                            "output",
                            "total",
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                            "input_cached_tokens",
                            "output_reasoning_tokens",
                        }
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and v >= 0
                    }
            safe.append(item)
        complete = allowed_ids.issubset({r["id"] for r in safe})
        return {
            "sync_state": "confirmed" if complete else "pending",
            "observations": safe,
            "reason": None if complete else "remote_not_visible_yet",
        }
    except (httpx.HTTPError, ValueError, TypeError):
        return {"sync_state": "failed", "observations": [], "reason": "readback_failed"}
