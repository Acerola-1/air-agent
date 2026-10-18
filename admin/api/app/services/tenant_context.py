"""会话上下文组装：本人企业列表与固定形状的 me/登录/切换响应。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MembershipRole, Role, Tenant, TenantMembership, UserFollowedRegion
from app.schemas import (
    MembershipBrief,
    RoleBrief,
    SessionContextOut,
    SessionOut,
    TenantBrief,
    TenantListItem,
    user_out,
)
from app.services.identity import Identity


async def list_active_tenants(session: AsyncSession, user_id: uuid.UUID) -> list[TenantListItem]:
    """本人“有效成员 × 有效企业”列表；不含任何其他企业的信息。"""
    rows = (
        await session.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == "active",
                Tenant.status == "active",
            )
            .order_by(Tenant.name)
        )
    ).all()
    items: list[TenantListItem] = []
    for membership, tenant in rows:
        role_codes = await _membership_role_codes(session, membership)
        items.append(
            TenantListItem(
                tenant=TenantBrief(
                    id=tenant.id, code=tenant.code, name=tenant.name, status=tenant.status
                ),
                membership=MembershipBrief(
                    id=membership.id,
                    tenant_id=membership.tenant_id,
                    department_id=membership.department_id,
                    status=membership.status,
                    revision=membership.revision,
                    role_codes=role_codes,
                ),
            )
        )
    return items


async def _membership_role_codes(session: AsyncSession, membership: TenantMembership) -> list[str]:
    codes = await session.scalars(
        select(Role.code)
        .join(MembershipRole, MembershipRole.role_id == Role.id)
        .where(
            MembershipRole.membership_id == membership.id,
            Role.tenant_id == membership.tenant_id,
        )
    )
    return sorted(codes.all())


async def build_session_context(
    session: AsyncSession,
    identity: Identity,
    *,
    include_tenants: bool = True,
) -> SessionContextOut:
    """固定类型外壳：user/session/current_tenant/membership/permissions/context_version。"""
    followed = await session.scalars(
        select(UserFollowedRegion.region_code).where(UserFollowedRegion.user_id == identity.user.id)
    )
    platform_role_briefs = [
        RoleBrief(id=uuid.UUID(rid), code=code, name=name)
        for rid, code, name in identity.platform_roles
    ]
    user = user_out(
        identity.user,
        roles=platform_role_briefs,
        followed_region_codes=list(followed.all()),
    )
    current_tenant = (
        TenantBrief(
            id=identity.tenant.id,
            code=identity.tenant.code,
            name=identity.tenant.name,
            status=identity.tenant.status,
        )
        if identity.tenant is not None
        else None
    )
    membership_brief: MembershipBrief | None = None
    if identity.membership is not None:
        membership_brief = MembershipBrief(
            id=identity.membership.id,
            tenant_id=identity.membership.tenant_id,
            department_id=identity.membership.department_id,
            status=identity.membership.status,
            revision=identity.membership.revision,
            role_codes=(
                sorted(identity.membership_role_codes)
                if identity.remote_tenants is not None
                else await _membership_role_codes(session, identity.membership)
            ),
        )
    tenants = (
        (
            remote_tenant_items(identity)
            if identity.remote_tenants is not None
            else await list_active_tenants(session, identity.user.id)
        )
        if include_tenants
        else []
    )
    needs_selection = identity.tenant is None and len(tenants) > 1 and not identity.platform_admin
    return SessionContextOut(
        user=user,
        session=SessionOut(
            session_id=identity.session.id,
            active_tenant_id=identity.session.active_tenant_id,
            ctx_version=identity.session.ctx_version,
            created_at=identity.session.created_at,
            expires_at=identity.session.expires_at,
        ),
        current_tenant=current_tenant,
        membership=membership_brief,
        permissions=sorted(identity.permissions),
        platform_admin=identity.platform_admin,
        context_version=identity.session.ctx_version,
        tenants=tenants,
        needs_tenant_selection=needs_selection,
    )


def remote_tenant_items(identity: Identity) -> list[TenantListItem]:
    """只展示 IAM 本人可切换企业；未选企业成员 ID/修订保持未知。"""
    items = []
    for row in identity.remote_tenants or ():
        tenant_id = uuid.UUID(row["tenant_id"])
        selected = identity.membership if identity.tenant_id == tenant_id else None
        items.append(
            TenantListItem(
                tenant=TenantBrief(
                    id=tenant_id, code=row["code"], name=row["name"], status=row["status"]
                ),
                membership=MembershipBrief(
                    id=selected.id if selected else None,
                    tenant_id=tenant_id,
                    department_id=selected.department_id if selected else None,
                    status=row["member_status"],
                    revision=selected.revision if selected else None,
                    role_codes=["tenant_admin"] if row.get("is_owner") is True else [],
                ),
            )
        )
    return items
