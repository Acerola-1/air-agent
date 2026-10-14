"""LangGraph API 自定义归档接口，授权来自同源服务签名.

另承载 R01 归属认领确认端点（`/internal/threads/claim-confirmation`）：
自定义 app 不经平台认证中间件，该端点用内部服务凭据（X-Internal-Token，
浏览器不可得）保护；只有聊天代理在平台创建线程真实成功（2xx）后调用，
把 `native_pending` 归属升级为 `native`。未确认的认领对所有资源路径不可见。
"""

from __future__ import annotations

import asyncio
import hmac
import os
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from common.artifact_scope import verify_scope
from common.identity.db import IdentityBackendUnavailable
from common.identity.db import confirm_thread_claim as _confirm_claim
from common.identity.ticket import TicketError, parse_keys, verify_ticket
from common.storage.artifact_db import (
    ArtifactExpired,
    cleanup_expired_artifacts,
    delete_thread_artifacts,
    get_tool_artifact,
    list_tool_artifacts,
)


async def _sweep() -> None:
    """有界过期清理：维护路径故障只记日志（失败关闭），绝不回退 owner 连接."""
    while True:
        try:
            await cleanup_expired_artifacts()
        except Exception as exc:
            from loguru import logger

            logger.bind(artifact_maintenance=True).warning(
                "归档过期清理未执行: {}: {}", type(exc).__name__, str(exc)[:200]
            )
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理有界过期清理任务."""
    task = asyncio.create_task(_sweep())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)

from common.event_ledger.service_read import router as reconcile_router  # noqa: E402

app.include_router(reconcile_router)


async def _authenticate_custom(request: Request) -> dict[str, Any]:
    """将平台鉴权错误转换为自定义 FastAPI 路由的正常拒绝响应."""
    from langgraph_sdk import Auth

    from common.identity.langgraph_auth import authenticate

    try:
        return await authenticate(request)
    except Auth.exceptions.HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from exc


async def _owners(request: Request, thread_id: str) -> tuple[str, str]:
    """独立校验 v2 scope（自定义 app 不经平台认证中间件——探针实测）."""
    try:
        scope = verify_scope(request.headers.get("x-artifact-scope", ""), thread_id)
        tenant_id, user_id = str(scope["tenantId"]), str(scope["userId"])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="无权读取此会话资料") from exc
    from common.identity.db import fetch_thread_owner

    actor = await _authenticate_custom(request)
    if actor.get("is_migration"):
        raise HTTPException(status_code=403, detail="迁移凭据不允许访问资料")
    if actor["tenant_id"] != tenant_id or actor["identity"] != user_id:
        raise HTTPException(status_code=403, detail="资料作用域与当前身份不一致")
    try:
        owner = await fetch_thread_owner(
            thread_id, tenant_id=tenant_id, user_id=user_id
        )
    except IdentityBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail="归属确认不可用") from exc
    if owner is None or owner["source"] not in {"native", "migrated"}:
        raise HTTPException(status_code=404, detail="资料不存在或不属于此会话")
    return tenant_id, user_id


@app.get("/archives/{thread_id}")
async def list_archives(
    request: Request, thread_id: str, offset: int = 0, limit: int = 50
):
    """列出当前授权线程归档的元数据."""
    tenant_id, user_id = await _owners(request, thread_id)
    return await list_tool_artifacts(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
        offset=offset,
        limit=limit,
    )


@app.get("/archives/{thread_id}/{artifact_id}")
async def read_archive(
    request: Request,
    thread_id: str,
    artifact_id: str,
    offset: int = 0,
    limit: int = 8000,
):
    """按字符分页读取归档，错误不透露其他线程数据."""
    tenant_id, user_id = await _owners(request, thread_id)
    try:
        record = await get_tool_artifact(
            artifact_id, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
        )
    except ArtifactExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="资料不存在或不属于此会话")
    start, size = max(offset, 0), min(max(limit, 1), 16000)
    raw = record.pop("raw_payload")
    record.pop("processing_metadata", None)
    return {
        **record,
        "offset": start,
        "content": raw[start : start + size],
        "next_offset": start + size if start + size < len(raw) else None,
    }


@app.delete("/archives/{thread_id}")
async def delete_archives(request: Request, thread_id: str):
    """清理已授权线程资料，重复请求幂等."""
    tenant_id, user_id = await _owners(request, thread_id)
    await delete_thread_artifacts(
        tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
    )
    return {"ok": True}


# ------------------------------------------------------------- R01 归属认领确认


def _require_internal_token(request: Request) -> None:
    """与 admin API 内部端点同源的凭据校验：不区分缺失/错误，统一 401."""
    expected = os.environ.get("INTERNAL_API_TOKEN", "")
    presented = request.headers.get("x-internal-token", "")
    if not expected or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="内部接口")


@app.post("/internal/threads/claim-confirmation")
async def confirm_thread_claim(request: Request) -> dict[str, Any]:
    """聊天代理确认平台已真实创建线程后，把 pending 归属升级为已确认.

    幂等：已确认返回 confirmed=true；无匹配 pending 行（认领不存在/属于
    他人/从未发生）返回 confirmed=false，不报错、不泄露归属细节。
    身份库故障必须 503（代理侧不得把未确认的线程当作可用）。
    """
    _require_internal_token(request)
    # 自定义应用不经过原生认证中间件；签名有效也不能代替当前会话/授权有效。
    await _authenticate_custom(request)
    # F01：作用域与会话只能来自**已验签票据**——内部凭据、pending 行存在、
    # 409、请求体自报身份都不构成创建事实。请求体仅允许 thread_id。
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        keys = parse_keys(os.environ.get("RUNTIME_TICKET_KEYS", ""))
        audience = os.environ.get("RUNTIME_TICKET_AUDIENCE", "langgraph-runtime")
        claims = verify_ticket(token, keys, expected_audience=audience)
    except TicketError:
        raise HTTPException(status_code=401, detail="运行凭据无效") from None
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求格式无效") from exc
    if not isinstance(body, dict) or set(body) - {"thread_id"}:
        raise HTTPException(status_code=400, detail="请求格式无效")
    thread_id = str(body.get("thread_id") or "")
    try:
        uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="标识非法") from exc
    try:
        confirmed = await _confirm_claim(
            thread_id,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
            session_id=claims.session_id,
        )
    except IdentityBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail="归属确认不可用") from exc
    return {"confirmed": confirmed}


@app.get("/internal/observability/health")
async def observability_health(request: Request) -> dict[str, Any]:
    """内部运维读取进程采集状态；不向企业普通用户披露全局计数."""
    _require_internal_token(request)
    from common.observability.collector import collector_health

    return collector_health()
