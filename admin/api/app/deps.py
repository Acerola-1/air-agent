"""FastAPI 依赖：统一会话解析、企业/平台权限校验、CSRF 与内部端点凭据。

`get_session` 在 FastAPI 里是按请求缓存的，所以 `get_identity` 和路由函数拿到
同一个 AsyncSession（同一事务）——GUC 作用域在请求内连续有效。

授权语义（设计 §7）：
- 401 未认证/会话撤销（含旧 HMAC cookie）；
- 403 自身已知范围内的权限拒绝（账号停用、越权、成员/企业停用后的业务操作）；
- 对“其他企业对象”一律 404，不泄露存在性。

`get_identity` 每请求实时查库：登出、改密、停用、角色撤销都立即生效，
不等 token 过期。cookie 存在 ≠ 授权——所有业务写路径还要 Origin/CSRF 校验。
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.security import SESSION_COOKIE
from app.services.identity import Identity, resolve_identity
from app.services.remote_mode import identity_backend_mode

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_identity(request: Request, session: SessionDep) -> Identity:
    """从统一会话 cookie 解析实时身份与权限（含当前企业上下文）。

    I2：按启动时显式选择的后端分派——local 走既有 PG 会话解析（原语义不变）；
    remote 走 agent_iam_sessions 桥接 + IAM 实时内省（不解析本地 auth_sessions/角色，
    权限完全来自 IAM），失败关闭不回退 local（A01/A05）。
    """
    if identity_backend_mode() == "remote":
        from app.services.remote_identity import (
            request_cookie,
            resolve_remote_identity,
        )

        cookie = request_cookie(request)
        identity = await resolve_remote_identity(session, cookie)
        claimed_tenant = request.headers.get("x-tenant-id")
        claimed_version = request.headers.get("x-tenant-context-version")
        if claimed_tenant is not None or claimed_version is not None:
            if not claimed_tenant or not claimed_version:
                raise HTTPException(status_code=403, detail="页面企业上下文缺失")
            try:
                version = int(claimed_version)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="上下文版本非法") from exc
            if (
                claimed_tenant != (str(identity.tenant_id) if identity.tenant_id else "none")
                or version != identity.context_version
            ):
                raise HTTPException(status_code=409, detail="企业上下文已变更，请刷新后重试")
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        identity = await resolve_identity(session, cookie)
    # 挂到 request.state 供错误处理/审计引用
    request.state.identity = identity
    return identity


IdentityDep = Annotated[Identity, Depends(get_identity)]


def require_permission(*codes: str) -> Callable[..., Coroutine[Any, Any, Identity]]:
    """依赖工厂：要求当前身份具备全部给定权限码（平台∪企业实时解析）。"""

    async def checker(identity: IdentityDep) -> Identity:
        missing = [code for code in codes if code not in identity.permissions]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: {', '.join(missing)}",
            )
        return identity

    return checker


def require_platform(identity: IdentityDep) -> Identity:
    """平台管理入口：必须是平台管理员（super_admin 平台角色）。"""
    if not identity.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可执行此操作"
        )
    return identity


def require_active_tenant(identity: IdentityDep) -> Identity:
    """企业业务端点前置：当前企业必须已选择、有效，且本人为有效成员。"""
    if identity.tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="尚未选择企业，请先通过企业选择流程确定当前企业",
        )
    if identity.tenant_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="企业已停用，无法发起企业业务"
        )
    if identity.membership_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="成员身份无效（已停用或未加入）"
        )
    return identity


ActiveTenantIdentity = Annotated[Identity, Depends(require_active_tenant)]
PlatformIdentity = Annotated[Identity, Depends(require_platform)]


async def require_tenant_context(request: Request, identity: IdentityDep) -> Identity:
    """R02：会话上下文型业务写入口必须声明目标企业与上下文版本。

    请求头 `x-tenant-id` / `x-tenant-context-version` 是非授权性提示，服务端
    与实时会话逐一比对：写操作缺失 → 403（上下文丢失），不一致 → 409
    context_changed（其他标签页已切换）。授权判定仍以服务端会话为准；这里
    只保证旧页面不会把写入悄悄落进切换后的企业。未选择企业的管理写操作
    （平台角色等）显式声明 `x-tenant-id: none`，与"会话无企业"对齐。
    """
    claimed_tenant = request.headers.get("x-tenant-id")
    claimed_version = request.headers.get("x-tenant-context-version")
    method = request.method.upper()
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        if not claimed_tenant or not claimed_version:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="页面企业上下文缺失，请刷新后重试",
            )
    if claimed_tenant:
        current = str(identity.tenant_id) if identity.tenant_id else "none"
        if claimed_tenant != current:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="企业上下文已变更（可能在其他标签页切换），请刷新后重试",
            )
    if claimed_version:
        try:
            version = int(claimed_version)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="上下文版本非法"
            ) from exc
        if version != identity.context_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="企业上下文版本已变更，请刷新后重试",
            )
    return identity


TenantContextIdentity = Annotated[Identity, Depends(require_tenant_context)]


def verify_write_origin(request: Request) -> None:
    """cookie 驱动的写操作：校验可信 Origin（跨站写一律拒绝）。"""
    method = request.method.upper()
    if method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin is None:
        # 无 Origin 的非浏览器请求放行；浏览器跨站（Sec-Fetch-Site）拒绝
        sec_fetch = (request.headers.get("sec-fetch-site") or "").lower()
        if sec_fetch == "cross-site":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="跨站请求被拒绝")
        return
    if not _origin_allowed(origin, settings.csrf_allowed_origins):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="请求来源不被允许（CSRF 校验失败）"
        )


def _origin_allowed(origin: str, allowed: list[str]) -> bool:
    if origin in allowed:
        return True
    return origin in ("http://localhost:8100", "http://127.0.0.1:8100")


def require_internal_token(request: Request) -> None:
    """服务端内部端点（运行票据签发）：仅可信 BFF/服务可调用，浏览器拿不到该凭据。"""
    settings = get_settings()
    presented = request.headers.get("x-internal-token", "")
    if not settings.internal_api_token or not hmac.compare_digest(
        presented, settings.internal_api_token
    ):
        # 不区分缺密钥与错密钥：统一 401，不泄露内部接口形态
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="内部接口")
