"""LangGraph API 自定义归档接口，授权来自同源服务签名.

另承载归属认领确认端点（`/internal/threads/claim-confirmation`）：
自定义 app 不经平台认证中间件，该端点用内部服务凭据（X-Internal-Token，
浏览器不可得）保护；只有聊天代理在平台创建线程真实成功（2xx）后调用，
把 `native_pending` 归属升级为 `native`。未确认的认领对所有资源路径不可见。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response

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


@app.middleware("http")
async def deleted_thread_search_barrier(request: Request, call_next):
    """平台搜索钩子仅过滤metadata；出口再排除已删除会话的values/extracted正文.

    保持平台原有分页顺序，当前页过滤后可能少于limit，不伪造补齐或计数。
    官方count不能附加墓碑条件时失败关闭；不能用未过滤的数量冒充权威目录。
    """
    if request.method != "POST" or request.url.path not in {
        "/threads/search",
        "/threads/count",
    }:
        return await call_next(request)
    try:
        actor = await _authenticate_custom(request)
        if not actor.get("membership_id"):
            return JSONResponse({"detail": "缺少有效企业成员身份"}, status_code=403)
        response = await call_next(request)
        if response.status_code != 200:
            return response
        from common.identity.db import fetch_deleted_thread_ids

        deleted = await fetch_deleted_thread_ids(
            tenant_id=actor["tenant_id"], user_id=actor["identity"]
        )
        if not deleted:
            return response
        if request.url.path == "/threads/count":
            return JSONResponse(
                {"detail": "会话删除后精确计数暂不可用，请使用会话列表"},
                status_code=503,
            )
        raw = b"".join([chunk async for chunk in response.body_iterator])
        rows = json.loads(raw)
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("thread_id"), str)
            for row in rows
        ):
            return JSONResponse({"detail": "会话目录响应无效"}, status_code=503)
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-encoding", "content-type"}
        }
        return JSONResponse(
            [row for row in rows if row["thread_id"] not in deleted], headers=headers
        )
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except (IdentityBackendUnavailable, ValueError):
        return JSONResponse({"detail": "会话目录暂不可用"}, status_code=503)


@app.middleware("http")
async def native_identity_error_response(request: Request, call_next):
    """Preserve SDK authentication denials across the native ASGI boundary.

    Some official runtime versions only translate SDK 401/403 errors inside
    their authentication adapter. A dependency 503 or stale-context 409 can
    otherwise escape as a server 500. Only known authentication statuses are
    translated here; unexpected exceptions still propagate.
    """
    from langgraph_sdk import Auth

    try:
        return await call_next(request)
    except Auth.exceptions.HTTPException as exc:
        messages = {
            401: "登录已失效",
            403: "无权访问",
            409: "企业上下文已变更",
            503: "身份服务暂不可用",
        }
        if exc.status_code not in messages:
            raise
        return JSONResponse(
            {"detail": messages[exc.status_code]}, status_code=exc.status_code
        )


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


async def _owners(
    request: Request,
    thread_id: str,
    *,
    allow_deleted: bool = False,
    allow_tombstone_without_owner: bool = False,
) -> tuple[str, str]:
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
            thread_id,
            tenant_id=tenant_id,
            user_id=user_id,
            allow_deleted=allow_deleted,
        )
    except IdentityBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail="归属确认不可用") from exc
    if owner is None or owner["source"] not in {"native", "migrated"}:
        if allow_tombstone_without_owner:
            from common.identity.db import fetch_deleted_thread_ids

            try:
                deleted = await fetch_deleted_thread_ids(
                    tenant_id=tenant_id, user_id=user_id
                )
            except IdentityBackendUnavailable as exc:
                raise HTTPException(status_code=503, detail="归属确认不可用") from exc
            if thread_id in deleted:
                return tenant_id, user_id
        raise HTTPException(status_code=404, detail="资料不存在或不属于此会话")
    if request.method == "GET":
        from common.identity.runtime import identity_from_auth_user
        from common.knowledge.validation import (
            KnowledgeValidationError,
            validate_thread_knowledge,
        )

        identity = identity_from_auth_user(actor)
        if identity is None:
            raise HTTPException(status_code=403, detail="缺少可信身份")
        try:
            await validate_thread_knowledge(identity, thread_id)
        except KnowledgeValidationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
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
    tenant_id, user_id = await _owners(
        request, thread_id, allow_deleted=True, allow_tombstone_without_owner=True
    )
    await delete_thread_artifacts(
        tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
    )
    return {"ok": True}


# ------------------------------------------------------------- 知识引用原文代理


@app.get("/knowledge/citations/{citation_id}/{kind}")
async def read_knowledge_citation(
    request: Request,
    citation_id: str,
    kind: str,
    offset: int = 0,
    limit: int = 8000,
):
    """受保护原文读取：citation 映射来自服务端存储，浏览器不能指定来源."""
    from common.identity.runtime import identity_from_auth_user
    from common.knowledge.proxy import CitationSourceError, read_citation_with_budget
    from common.knowledge.store import (
        CitationBackendUnavailable,
        CitationNotFound,
        get_citation,
    )

    try:
        uuid.UUID(citation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="引用不存在") from exc
    actor = await _authenticate_custom(request)
    identity = identity_from_auth_user(actor)
    if identity is None:
        raise HTTPException(status_code=403, detail="缺少可信用户身份")
    try:
        citation = await get_citation(identity, citation_id)
    except CitationNotFound as exc:
        raise HTTPException(
            status_code=404, detail="引用不存在或不属于当前用户"
        ) from exc
    except CitationBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail="引用存储暂不可用") from exc
    thread_id = str(citation.get("thread_id") or "")
    if thread_id:
        from common.identity.db import fetch_thread_owner

        owner = await fetch_thread_owner(
            thread_id, tenant_id=identity.tenant_id, user_id=identity.user_id
        )
        if owner is None or owner["source"] not in {"native", "migrated"}:
            raise HTTPException(status_code=404, detail="引用所在会话不可访问")
    try:
        body, content_type, headers, status = await read_citation_with_budget(
            identity,
            citation,
            kind=kind,
            offset=offset,
            limit=limit,
            range_header=request.headers.get("range"),
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="原文读取超时") from exc
    except CitationSourceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return Response(
        content=body, media_type=content_type, headers=headers, status_code=status
    )


# ------------------------------------------------------------- 归属认领确认


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
    # 作用域与会话只能来自**已验签票据**——内部凭据、pending 行存在、
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


@app.get("/chat/config")
async def get_chat_config(request: Request, graph_id: str) -> JSONResponse:
    """受保护的只读输入限制配置；不返回凭据、模型规则或容量细节."""
    actor = await _authenticate_custom(request)
    if actor.get("is_migration") or actor.get("is_reconcile"):
        raise HTTPException(status_code=403, detail="内部凭据不允许读取聊天配置")
    if not actor.get("tenant_id") or not actor.get("membership_id"):
        raise HTTPException(status_code=403, detail="缺少有效企业成员身份")

    from common.identity.business_access import BusinessAccessError, grant_for_graph
    from common.identity.runtime import identity_from_auth_user
    from common.input_limits import current_input_limit

    identity = identity_from_auth_user(actor)
    if identity is None:
        raise HTTPException(status_code=401, detail="运行身份无效")
    try:
        grant_for_graph(graph_id=graph_id, identity=identity)
    except BusinessAccessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "error": exc.message},
        ) from exc
    limit = current_input_limit()
    return JSONResponse(
        {
            "graph_id": graph_id,
            "max_input_chars": limit.max_chars,
            "character_count_mode": limit.character_count_mode,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/capabilities")
async def get_capabilities(
    request: Request,
    graph_id: str,
    limit: int = 200,
    cursor: str | None = None,
    menu_name: str | None = None,
) -> dict[str, Any]:
    """原生能力目录接口：按当前人类成员授权实时返回 Skill 与 MCP 目录.

    严格鉴权：必须活跃人类成员；迁移凭据与内部校准票据一律 403；
    单个 MCP 2秒/总 5秒超时控制，部分服务不可用时返回 200 并标 temporarily_unavailable.
    """
    actor = await _authenticate_custom(request)
    if actor.get("is_migration"):
        raise HTTPException(status_code=403, detail="迁移凭据不允许访问能力目录")
    if actor.get("is_reconcile"):
        raise HTTPException(status_code=403, detail="内部校准凭据不允许访问能力目录")
    if not actor.get("tenant_id") or not actor.get("membership_id"):
        raise HTTPException(status_code=403, detail="缺少有效企业成员身份")

    from common.capabilities import get_capabilities_catalog
    from common.identity.business_access import BusinessAccessError
    from common.identity.runtime import identity_from_auth_user

    identity = identity_from_auth_user(actor)
    if identity is None:
        raise HTTPException(status_code=401, detail="运行身份无效")
    auth_revisions = {
        "tenant": int(actor.get("tenant_auth_revision") or 1),
        "user": int(actor.get("user_auth_revision") or 1),
        "membership": int(actor.get("membership_revision") or 1),
    }

    try:
        return await get_capabilities_catalog(
            graph_id=graph_id,
            identity=identity,
            auth_revisions=auth_revisions,
            limit=limit,
            cursor=cursor,
            menu_name=menu_name,
        )
    except BusinessAccessError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "error": exc.message}
        ) from exc
