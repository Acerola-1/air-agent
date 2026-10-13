"""服务校准网关：只投影运行状态与可信关联，不暴露原生运行正文.

原生 read 钩子同时覆盖状态/历史，直接放行 rs-v1 会泄露 messages 与 kwargs。
服务票据只访问本网关；网关以内部签名委托原生窄读，并对响应执行字段白名单。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from common.event_ledger.store import (
    LedgerBackendUnavailable,
    LedgerScope,
    thread_deletion_exists,
)
from common.identity.db import IdentityBackendUnavailable, fetch_thread_in_tenant
from common.identity.ticket import (
    ReconcileClaims,
    TicketError,
    parse_keys,
    verify_reconcile_ticket,
)

router = APIRouter()
_NATIVE_READ = re.compile(r"^/threads/([^/]+)/runs(?:/([^/]+))?$")
PROJECTION_HEADER = "x-ledger-reconcile-projection"
_FIELDS = ("run_id", "thread_id", "status", "created_at", "updated_at")
_LINKS = ("ledger_request_id", "ledger_attempt_id", "ledger_submission_id")


def projection_signature(token: str, path: str) -> str:
    """内部委托绑定完整票据和原生读路径，普通服务票据持有者不能自行签发."""
    secret = os.environ.get("INTERNAL_API_TOKEN", "")
    if not secret:
        raise HTTPException(503, detail="校准网关内部凭据未配置")
    return hmac.new(
        secret.encode(), f"ledger-status-read\n{path}\n{token}".encode(), hashlib.sha256
    ).hexdigest()


def native_read_thread(method: str, path: str) -> str:
    """只允许运行列表和单个运行 GET；状态、历史、消息、join 和写操作均不允许."""
    match = _NATIVE_READ.fullmatch(path) if method == "GET" else None
    if match is None:
        raise HTTPException(403, detail="校准凭据只允许经过状态投影网关读取")
    return match[1]


def claims_from_token(token: str) -> ReconcileClaims:
    """独立验签、时效与受众校验；不依赖原成员会话."""
    try:
        return verify_reconcile_ticket(
            token,
            parse_keys(os.environ.get("LEDGER_RECONCILE_KEYS", "")),
            expected_audience=os.environ.get(
                "LEDGER_RECONCILE_AUDIENCE", "ledger-reconcile"
            ),
        )
    except TicketError as exc:
        raise HTTPException(401, detail="校准凭据无效") from exc


async def authorized_owner(claims: ReconcileClaims, thread_id: str) -> dict[str, Any]:
    """按票据租户/线程和永久删除标记授权，归属用于平台自身的运行可见性过滤."""
    if not claims.allows_thread(thread_id):
        raise HTTPException(403, detail="校准凭据未授权该会话")
    try:
        owner = await fetch_thread_in_tenant(thread_id, tenant_id=claims.tenant_id)
        if owner is None or owner["source"] == "native_pending":
            raise HTTPException(404, detail="会话不可访问")
        scope = LedgerScope(claims.tenant_id, str(owner["user_id"]), "")
        if await thread_deletion_exists(scope, thread_id=thread_id):
            raise HTTPException(404, detail="会话不可访问")
        return owner
    except (IdentityBackendUnavailable, LedgerBackendUnavailable) as exc:
        raise HTTPException(503, detail="校准授权后端不可用") from exc


def project_run(value: Any) -> dict[str, Any]:
    """采用白名单，新增上游字段不会意外成为原文/配置读取能力."""
    if not isinstance(value, dict) or not value.get("run_id"):
        raise HTTPException(503, detail="平台运行响应无效")
    result = {k: value[k] for k in _FIELDS if k in value}
    metadata = value.get("metadata")
    result["metadata"] = (
        {k: metadata[k] for k in _LINKS if isinstance(metadata.get(k), str)}
        if isinstance(metadata, dict)
        else {}
    )
    return result


async def _read(
    request: Request,
    thread_id: str,
    run_id: str | None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    claims = claims_from_token(token)
    await authorized_owner(claims, thread_id)
    path = f"/threads/{thread_id}/runs" + (f"/{run_id}" if run_id else "")
    native_read_thread("GET", path)
    headers = {
        "authorization": f"Bearer {token}",
        PROJECTION_HEADER: projection_signature(token, path),
    }
    # 部署指定的固定回环地址；绝不使用调用方提交的 host/url。
    base = os.environ.get("LEDGER_NATIVE_LOOPBACK_URL", "http://127.0.0.1:8000")
    parsed = urlparse(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HTTPException(503, detail="校准网关需要固定本地回环地址")
    try:
        async with httpx.AsyncClient(
            base_url=base, timeout=10, trust_env=False
        ) as client:
            response = await client.get(
                path,
                headers=headers,
                params={"limit": limit, "offset": offset} if not run_id else None,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(503, detail="平台状态接口不可用") from exc
    if response.status_code != 200:
        # 不转发上游可能含正文/内部配置的错误响应。
        status = (
            response.status_code if response.status_code in (401, 403, 404) else 503
        )
        raise HTTPException(status, detail="平台状态不可读取")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(503, detail="平台状态响应无效") from exc
    if run_id:
        return project_run(payload)
    if not isinstance(payload, list):
        raise HTTPException(503, detail="平台运行列表响应无效")
    return [project_run(item) for item in payload]


@router.get("/internal/ledger/threads/{thread_id}/runs")
async def list_statuses(
    request: Request,
    thread_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
) -> Any:
    """有界状态分页，不返回运行输入、消息、模型配置或自由 metadata."""
    return await _read(request, thread_id, None, limit=limit, offset=offset)


@router.get("/internal/ledger/threads/{thread_id}/runs/{run_id}")
async def read_status(request: Request, thread_id: str, run_id: str) -> Any:
    """只读单个运行的状态与关联标识."""
    return await _read(request, thread_id, run_id)
