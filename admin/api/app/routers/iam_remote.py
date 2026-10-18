"""remote 部署的认证门面（I2，G3.1）：SSO + 服务端应用会话 + 旧外壳逐字段兼容.

local 部署继续使用 routers/auth.py（本地 PG 会话）；本路由只在
AGENT_IDENTITY_BACKEND=remote 时注册（main.py 条件挂载）。

- POST /auth/iam/start：生成一次性 state（HttpOnly 短时 cookie 绑定发起方），
  303 到 IAM /v1/sso/authorize（client/redirect/state 精确登记值）；
- GET /auth/iam/callback：state cookie 比对（一次性，防重放/错配）→ 服务端
  SSO exchange（SSO client Basic；ticket+redirect_uri+state，state 必填）→
  进程内 introspect（include_execution_context=true 取执行引用）→
  save_iam_session（HttpOnly 应用会话 handle）→ 同UUID投影 upsert → 303 回
  页面并清理 URL 中 ticket（重定向到干净路径）；
- POST /auth/logout：先撤销 IAM 会话再撤销应用句柄，运行引用随即失效；
- GET /auth/me、POST /auth/switch-tenant：旧外壳逐字段兼容（remote Identity）。

安全边界：IAM token/state/ticket 原文不进浏览器 JS 存储/URL/日志；应用会话
handle 为 HttpOnly+SameSite=Lax（+可选 Secure）；SSO cookie 名与中央会话
cookie 名不同（不按端口隔离，名称隔离）。企业切换带 CAS（expected_ctx_version）。
"""

from __future__ import annotations

import hmac
import os
import secrets
import urllib.parse
import urllib.request

from common.identity.db import IdentityBackendUnavailable
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import IdentityDep, verify_write_origin
from app.services.remote_identity import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    hash_token,
    request_cookie,
    revoke_session,
    save_iam_session,
    upsert_reference_projection,
)

router = APIRouter(tags=["iam-remote"], dependencies=[Depends(verify_write_origin)])

# 一次性 state cookie（与中央 IAM 会话 cookie、应用会话 cookie 三者名称互异）
_STATE_COOKIE = "air_agent_iam_oid"


def _iam_config() -> dict[str, str]:
    import os

    keys = ("IAM_BASE_URL", "IAM_ISSUER", "IAM_SSO_CLIENT_ID", "IAM_SSO_CLIENT_SECRET")
    missing = [key for key in keys if not (os.environ.get(key) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IAM SSO 配置不可用",
        )
    return {key: os.environ[key].strip() for key in keys}


def _frontend_kind(request: Request) -> str:
    kind = request.query_params.get("frontend", "chat")
    if kind not in ("chat", "admin"):
        raise HTTPException(status_code=400, detail="登录入口非法")
    return kind


def _callback_url(config: dict[str, str], request: Request, kind: str | None = None) -> str:
    kind = kind or _frontend_kind(request)
    prefix = "IAM_SSO_ADMIN_CLIENT" if kind == "admin" else "IAM_SSO_CLIENT"
    values = {
        key: os.environ.get(f"{prefix}_{key}", "").strip() for key in ("ID", "SECRET", "CALLBACK")
    }
    if not all(values.values()):
        raise HTTPException(status_code=503, detail="SSO 登录入口未配置")
    config["IAM_SSO_CLIENT_ID"] = values["ID"]
    config["IAM_SSO_CLIENT_SECRET"] = values["SECRET"]
    return values["CALLBACK"]


@router.post("/auth/iam/start", summary="发起 SSO（一次性 state，303 中央授权）")
async def start_sso(request: Request, response: Response) -> Response:

    config = _iam_config()
    redirect_uri = _callback_url(config, request)
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO 回调未配置"
        )
    state = secrets.token_urlsafe(24)
    # state cookie 绑定发起方类型（chat/admin），callback 用它选择同一 SSO 客户端兑换
    state_cookie_value = f"{_frontend_kind(request)}:{state}"
    response.set_cookie(
        f"{_STATE_COOKIE}_{_frontend_kind(request)}",
        state_cookie_value,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/",
    )
    authorize = (
        f"{config['IAM_BASE_URL'].rstrip('/')}/v1/sso/authorize"
        f"?client_id={urllib.parse.quote(config['IAM_SSO_CLIENT_ID'])}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&state={urllib.parse.quote(state, safe='')}"
    )
    response.status_code = status.HTTP_303_SEE_OTHER
    response.headers["Location"] = authorize
    return response


async def _sso_exchange(config: dict[str, str], ticket: str, state: str, redirect_uri: str) -> dict:
    import asyncio

    from common.identity.remote import request_iam, response_json

    client_config = dict(
        config,
        IAM_INTERNAL_CLIENT_ID=config["IAM_SSO_CLIENT_ID"],
        IAM_INTERNAL_CLIENT_SECRET=config["IAM_SSO_CLIENT_SECRET"],
    )
    response = await asyncio.to_thread(
        request_iam,
        "POST",
        "/v1/internal/sso/exchange",
        config=client_config,
        body={"ticket": ticket, "redirect_uri": redirect_uri, "state": state},
    )
    if response.status_code in (400, 401, 403):
        raise HTTPException(status_code=401, detail="SSO 兑换失败，请重新登录")
    return response_json(response)


async def _introspect_with_ref(config: dict[str, str], token: str, audience: str) -> dict:
    import asyncio

    from common.identity.remote import subject_payload

    return await asyncio.to_thread(
        subject_payload, token, expected_audience=audience, include_execution_context=True
    )


@router.get("/auth/iam/callback", summary="SSO 回调（一次性 state/ticket 兑换 → 应用会话）")
async def sso_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    config = _iam_config()
    query = request.query_params
    ticket = str(query.get("ticket") or "")
    state = str(query.get("state") or "")
    if not ticket or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="回调缺少 ticket 或 state"
        )
    requested_kind = _frontend_kind(request)
    state_cookie = f"{_STATE_COOKIE}_{requested_kind}"
    stored_state = request.cookies.get(state_cookie)
    if not stored_state or ":" not in stored_state:
        response = Response(status_code=status.HTTP_400_BAD_REQUEST, content=b"invalid state")
        response.delete_cookie(state_cookie, path="/")
        return response
    kind, _, bound_state = stored_state.partition(":")
    if kind != requested_kind or not hmac.compare_digest(bound_state, state):
        # 错误 state / 未发起的回调：不兑换，清掉一次性 cookie
        response = Response(status_code=status.HTTP_400_BAD_REQUEST, content=b"invalid state")
        response.delete_cookie(state_cookie, path="/")
        return response
    redirect_uri = _callback_url(config, request, kind)

    result = await _sso_exchange(config, ticket, state, redirect_uri)
    token = str(result.get("access_token") or "")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO 兑换未返回会话")

    return await _establish_session(session, token, kind)


async def _establish_session(session: AsyncSession, token: str, kind: str) -> Response:
    """经实时严格内省后建立服务端应用会话；只返回不透明 HttpOnly 句柄。"""
    from common.identity.remote import require_remote_config

    config = require_remote_config()
    audience = os.environ.get("IAM_RUNTIME_AUDIENCE", "langgraph-runtime")
    payload = await _introspect_with_ref(config, token, audience)
    if payload.get("active") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份无效")
    ec = payload.get("execution_context") or {}
    principal = payload.get("principal") or {}
    current_tenant = payload.get("current_tenant") or {}
    membership = payload.get("membership") or {}

    handle = await save_iam_session(
        session,
        token_hash=hash_token(token),
        user_id=str(principal.get("id") or ""),
        account=str((payload.get("user") or {}).get("account") or ""),
        subject_token=token,
        iam_session_id=str((payload.get("session") or {}).get("id") or "") or None,
        tenant_id=str(current_tenant.get("id")) if current_tenant.get("id") else None,
        tenant_code=str(current_tenant.get("code")) if current_tenant.get("code") else None,
        tenant_name=str(current_tenant.get("name")) if current_tenant.get("name") else None,
        membership_id=str(membership.get("id")) if membership.get("id") else None,
        ctx_version=int(payload.get("context_version") or 0),
        revisions=dict(payload.get("revisions") or {}),
        permissions=list(payload.get("permissions") or []),
        audience=audience,
        issuer=config["IAM_ISSUER"],
        verification_ref=str(ec.get("verification_ref") or "") or None,
        expires_at=_expiry_from_payload(payload),
    )
    # 同UUID最小引用（FK/展示；不授权），只在 IAM 实时回复后建立。
    if current_tenant.get("id") or principal.get("id"):
        user = payload.get("user") or {}
        revisions = payload.get("revisions") or {}
        await upsert_reference_projection(
            session,
            user_id=str(principal.get("id") or ""),
            account=str(user.get("account") or ""),
            display_name=str(user.get("display_name") or user.get("account") or ""),
            tenant_id=str(current_tenant.get("id")) if current_tenant.get("id") else None,
            tenant_code=str(current_tenant.get("code")) if current_tenant.get("code") else None,
            tenant_name=str(current_tenant.get("name")) if current_tenant.get("name") else None,
            membership_id=str(membership.get("id")) if membership.get("id") else None,
            user_auth_revision=int(revisions.get("user_auth_revision") or 0),
            tenant_auth_revision=revisions.get("tenant_auth_revision"),
            membership_revision=revisions.get("membership_revision"),
        )
    await session.commit()

    response = Response(status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Location"] = "/"
    response.set_cookie(
        "air_agent_iam_admin_handle" if kind == "admin" else SESSION_COOKIE,
        handle,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/",
    )
    response.delete_cookie(f"{_STATE_COOKIE}_{kind}", path="/")
    return response


def _expiry_from_payload(payload: dict):
    import datetime as dt

    session = payload.get("session") or {}
    raw = session.get("expires_at")
    if isinstance(raw, str):
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)


@router.post("/auth/login")
async def login_remote(
    payload: dict, request: Request, session: AsyncSession = Depends(get_session)
):
    import asyncio

    from common.identity.remote import (
        RemoteIdentityRejected,
        request_iam,
        require_remote_config,
        response_json,
    )
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    from app.services.remote_identity import (
        SESSION_COOKIE,
        raise_remote_error,
        resolve_remote_identity,
    )
    from app.services.tenant_context import build_session_context

    kind = request.headers.get("x-agent-frontend", "chat")
    if kind not in ("chat", "admin"):
        raise HTTPException(status_code=400, detail="登录入口非法")
    if not isinstance(payload.get("account"), str) or not isinstance(payload.get("password"), str):
        raise HTTPException(status_code=422, detail="请输入账号和密码")
    try:
        response = await asyncio.to_thread(
            request_iam,
            "POST",
            "/v1/auth/login",
            config=require_remote_config(),
            body={
                "account": payload["account"],
                "password": payload["password"],
                "realm": "workforce",
                "audience": "air-agent",
            },
            internal=False,
        )
        if response.status_code == 401:
            raise RemoteIdentityRejected("账号或密码错误")
        result = response_json(response)
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise IdentityBackendUnavailable("IAM 登录协议异常")
        established = await _establish_session(session, token, kind)
        from http.cookies import SimpleCookie

        cookies = SimpleCookie()
        for cookie in established.headers.getlist("set-cookie"):
            cookies.load(cookie)
        name = "air_agent_iam_admin_handle" if kind == "admin" else SESSION_COOKIE
        identity = await resolve_remote_identity(session, cookies[name].value)
        ctx = await build_session_context(session, identity)
        await session.commit()
        out = JSONResponse(jsonable_encoder(ctx))
        for cookie in established.headers.getlist("set-cookie"):
            out.headers.append("set-cookie", cookie)
        return out
    except HTTPException:
        raise
    except Exception as exc:
        raise_remote_error(exc)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    from app.services.remote_identity import (
        iam_user_request,
        load_session_token,
        parse_handle_cookie,
        raise_remote_error,
    )

    probe = parse_handle_cookie(request_cookie(request))
    row = await load_session_token(session, probe) if probe else None
    if row:
        try:
            await iam_user_request(row["subject_token"], "POST", "/v1/auth/logout")
        except Exception as exc:
            raise_remote_error(exc)
        await revoke_session(session, probe)
        await session.commit()
    response = Response(status_code=204)
    cookie_name = (
        "air_agent_iam_admin_handle"
        if (
            request.headers.get("x-agent-frontend") == "admin"
            or (
                request.cookies.get("air_agent_iam_admin_handle")
                and not request.cookies.get(SESSION_COOKIE)
            )
        )
        else SESSION_COOKIE
    )
    response.delete_cookie(cookie_name, path="/")
    return response


@router.post("/auth/switch-tenant", operation_id="switch_tenant_iam_remote")
async def switch_tenant_remote(
    payload: dict,
    request: Request,
    identity: IdentityDep,
    session: AsyncSession = Depends(get_session),
):
    import uuid

    from app.services.remote_identity import (
        iam_user_request,
        load_session_token,
        parse_handle_cookie,
        raise_remote_error,
        resolve_remote_identity,
    )
    from app.services.tenant_context import build_session_context

    expected = payload.get("expected_ctx_version")
    try:
        tenant_id = str(uuid.UUID(payload.get("tenant_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="企业标识非法") from exc
    if type(expected) is not int:
        raise HTTPException(status_code=400, detail="上下文版本必须为整数")
    if expected != identity.context_version:
        raise HTTPException(status_code=409, detail="企业上下文已变更，请刷新")
    probe = parse_handle_cookie(request_cookie(request))
    row = await load_session_token(session, probe)
    if not row:
        raise HTTPException(status_code=401, detail="应用会话无效")
    try:
        await iam_user_request(
            row["subject_token"],
            "POST",
            "/v1/auth/switch-tenant",
            {"tenant_id": tenant_id, "expected_ctx_version": expected},
        )
    except Exception as exc:
        raise_remote_error(exc)
    identity = await resolve_remote_identity(
        session,
        request_cookie(request),
    )
    await session.commit()
    return await build_session_context(session, identity, include_tenants=True)
