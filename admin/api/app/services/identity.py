"""统一身份解析：cookie → 会话 → 用户 → 企业上下文 → 实时权限。

所有查询都发生在运行角色连接 + 事务本地 GUC 上：RLS 是执行层，不是提示层。
权限每次现算（不落 token、不缓存），使撤销/停用即时生效。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import TenantContext, apply_tenant_context
from app.models import (
    AuthSession,
    MembershipRole,
    Permission,
    Role,
    RolePermission,
    Tenant,
    TenantMembership,
    User,
)
from app.security import parse_session_cookie, token_hash_of


@dataclass(frozen=True)
class Identity:
    """已验证的请求身份。业务授权判断只允许使用这里的字段。"""

    user: User
    session: AuthSession
    tenant: Tenant | None  # 当前企业上下文（未选择为 None）
    membership: TenantMembership | None  # 当前企业在上下文时解析（可能停用）
    platform_admin: bool
    permissions: frozenset[str]
    # 操作者当前成员身份持有的企业角色码（防越级授权判定输入）
    membership_role_codes: frozenset[str] = frozenset()
    # 平台角色（id/code/name）：me 响应展示与平台域授权来源
    platform_roles: tuple[tuple[str, str, str], ...] = ()
    tenant_status: str | None = None
    membership_status: str | None = None

    remote_tenants: tuple[dict, ...] | None = None
    remote_verification_ref: str | None = None

    @property
    def tenant_id(self) -> uuid.UUID | None:
        return self.tenant.id if self.tenant else None

    @property
    def context_version(self) -> int:
        return self.session.ctx_version


@dataclass(frozen=True)
class _SessionProbe:
    """cookie 拆出的会话标识（未验证前不可用于任何授权）。"""

    session_id: uuid.UUID
    token: str


def _parse_cookie(cookie_value: str | None) -> _SessionProbe | None:
    session_id, token = parse_session_cookie(cookie_value)
    if not session_id or not token:
        return None
    try:
        return _SessionProbe(session_id=uuid.UUID(session_id), token=token)
    except ValueError:
        return None


async def resolve_identity(
    session: AsyncSession,
    cookie_value: str | None,
) -> Identity:
    """验证会话并解析实时权限。未认证抛 401；账号停用抛 403。

    步骤与 GUC 顺序（全部事务本地，随请求事务结束失效）：
    1. 以 session_id GUC 读 auth_sessions（RLS 仅放行本会话行）+ 摘要比对；
    2. 以 user_id GUC 读 users；
    3. 读平台授权：user_roles 只对平台上下文可见，先以 platform GUC 受限读，
       解析完立即复位——租户查询绝不能继承平台旁路；
    4. 若会话带 active_tenant_id：读 tenant 与 membership；企业/成员停用不抛错，
       由业务端点的 require_active_tenant 拒绝，/auth/me 仍可展示状态。
    """
    probe = _parse_cookie(cookie_value)
    if probe is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录、会话已撤销或 cookie 格式不受支持，请重新登录",
        )

    # 1. 会话行：先用 session_id 作用域查（RLS 策略只放行这一行）
    await apply_tenant_context(session, TenantContext(session_id=str(probe.session_id)))
    session_row = (
        (
            await session.execute(
                text(
                    "SELECT user_id, active_tenant_id, ctx_version, user_auth_revision,"
                    " token_hash, revoked_at, expires_at"
                    " FROM auth_sessions WHERE id = :sid"
                ),
                {"sid": str(probe.session_id)},
            )
        )
        .mappings()
        .first()
    )
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话不存在或已被撤销，请重新登录"
        )
    if session_row["revoked_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已登出或被撤销，请重新登录"
        )
    if session_row["expires_at"] <= dt.datetime.now(dt.UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已过期，请重新登录"
        )
    if session_row["token_hash"] != token_hash_of(probe.token):
        # session_id 只是定位符（UUID），真正凭据是高熵 token 的摘要；不符即伪造
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话凭据无效，请重新登录"
        )

    user_id = session_row["user_id"]

    # 2. 用户行（本人作用域）
    await apply_tenant_context(
        session, TenantContext(user_id=str(user_id), session_id=str(probe.session_id))
    )
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    if user.auth_revision != session_row["user_auth_revision"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号凭证已更新，请重新登录",
        )

    # 3. 平台角色与权限；解析完立即复位 platform GUC
    platform_admin, platform_codes, platform_role_rows = await _resolve_platform_grants(
        session, user_id
    )
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=str(user_id),
            session_id=str(probe.session_id),
            platform=platform_admin,
        ),
    )

    # 4. 当前企业上下文
    tenant: Tenant | None = None
    membership: TenantMembership | None = None
    tenant_status: str | None = None
    membership_status: str | None = None
    tenant_codes: set[str] = set()
    membership_role_codes: frozenset[str] = frozenset()
    active_tenant_id = session_row["active_tenant_id"]
    if active_tenant_id is not None:
        await apply_tenant_context(
            session,
            TenantContext(
                user_id=str(user_id),
                tenant_id=str(active_tenant_id),
                session_id=str(probe.session_id),
                platform=platform_admin,
            ),
        )
        tenant = await session.get(Tenant, active_tenant_id)
        if tenant is not None:
            tenant_status = tenant.status
            membership = await session.scalar(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == active_tenant_id,
                    TenantMembership.user_id == user_id,
                )
            )
            if membership is not None:
                membership_status = membership.status
                if membership.status == "active":
                    tenant_codes = await _resolve_membership_codes(session, membership.id)
                    membership_role_codes = frozenset(
                        await _membership_role_codes(session, membership.id)
                    )

    auth_session = await session.get(AuthSession, probe.session_id)
    assert auth_session is not None  # 上方已验证行存在

    return Identity(
        user=user,
        session=auth_session,
        tenant=tenant,
        membership=membership,
        platform_admin=platform_admin,
        permissions=frozenset(platform_codes | tenant_codes),
        membership_role_codes=membership_role_codes,
        platform_roles=tuple((str(r["id"]), r["code"], r["name"]) for r in platform_role_rows),
        tenant_status=tenant_status,
        membership_status=membership_status,
    )


async def _membership_role_codes(session: AsyncSession, membership_id: uuid.UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                select(Role.code)
                .join(MembershipRole, MembershipRole.role_id == Role.id)
                .where(MembershipRole.membership_id == membership_id)
            )
        ).all()
    )


async def _resolve_platform_grants(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[bool, set[str], list[dict]]:
    """平台角色（user_roles → scope=platform roles → permissions）。

    user_roles 的 RLS 只放行平台上下文：这里用 platform GUC 开一条**受限读**，
    仅解析“当前会话用户本人”的平台授权（WHERE user_id=本人）。调用方解析后
    必须复位 GUC——写入路径不经过这里。
    """
    await apply_tenant_context(session, TenantContext(user_id=str(user_id), platform=True))
    role_rows = (
        await session.execute(
            text(
                "SELECT r.id, r.code, r.name FROM user_roles ur"
                " JOIN roles r ON r.id = ur.role_id"
                " WHERE ur.user_id = :u AND r.scope = 'platform'"
            ),
            {"u": str(user_id)},
        )
    ).mappings()
    roles = list(role_rows)
    is_platform_admin = any(r["code"] == "super_admin" for r in roles)
    codes: set[str] = set()
    if roles:
        codes = set(
            (
                await session.scalars(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id.in_([r["id"] for r in roles]))
                    .where(Permission.is_active.is_(True))
                    .distinct()
                )
            ).all()
        )
    return is_platform_admin, codes, roles


async def _resolve_membership_codes(session: AsyncSession, membership_id: uuid.UUID) -> set[str]:
    """成员角色 → 权限码（仅企业角色；grantable_to_tenants=false 一律剔除）。

    membership_roles 行本身受 RLS 企业过滤；SQL 同时显式带角色作用域条件，
    与“查询必须显式包含授权范围”的设计一致。
    """
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(MembershipRole, MembershipRole.role_id == Role.id)
        .where(
            MembershipRole.membership_id == membership_id,
            Role.scope == "tenant",
            Permission.is_active.is_(True),
            Permission.grantable_to_tenants.is_(True),
        )
        .distinct()
    )
    return set((await session.scalars(stmt)).all())
