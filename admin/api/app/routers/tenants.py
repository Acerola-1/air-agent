"""企业与成员管理接口（任务 4.1/4.3）。

职责边界（设计 §2）：
- 企业创建/停用：平台专属（tenant:create / tenant:update）；
- 成员关联：平台授权制（member:link 为平台专属码）——企业管理员**不能**把
  其他账号拉进本企业，也不能枚举全局用户；
- 企业管理员：管理本企业成员的状态/部门/角色（member:manage / member:remove），
  不影响同一用户在别企业的成员或全局登录身份；
- 最后管理员保护：除平台明确的企业停用流程外，任何操作不得让企业失去全部
  活跃管理员（以内置 `tenant_admin` 角色判定）；
- 列表/搜索/分页/count 全部走同一企业范围（RLS + 显式条件双保险）。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission, require_tenant_context
from app.models import (
    Department,
    MembershipRole,
    Permission,
    Role,
    RolePermission,
    Tenant,
    TenantMembership,
    User,
)
from app.schemas import (
    MemberCreate,
    MemberOut,
    MemberRolesIn,
    MemberUpdate,
    Page,
    TenantCreate,
    TenantOut,
    TenantUpdate,
)
from app.services.audit import record_audit
from app.services.identity import Identity
from app.services.tenant_seed import ensure_tenant_admin_role

# R02：企业/成员写操作必须声明会话企业上下文并保持一致（平台管理入口以
# `x-tenant-id: none` 或本人当前企业声明，服务端与实时会话比对）。
router = APIRouter(tags=["tenants"], dependencies=[Depends(require_tenant_context)])

CanRead = Annotated[Identity, Depends(require_permission("tenant:read"))]
CanCreate = Annotated[Identity, Depends(require_permission("tenant:create"))]
CanUpdate = Annotated[Identity, Depends(require_permission("tenant:update"))]
CanLinkMember = Annotated[Identity, Depends(require_permission("member:link"))]
CanReadMembers = Annotated[Identity, Depends(require_permission("member:read"))]
CanManageMember = Annotated[Identity, Depends(require_permission("member:manage"))]
CanRemoveMember = Annotated[Identity, Depends(require_permission("member:remove"))]

# 判定“企业管理员”的内置角色码；最后管理员保护以此为准
ADMIN_ROLE_CODE = "tenant_admin"


def _tenant_out(tenant: Tenant) -> TenantOut:
    return TenantOut(
        id=tenant.id,
        code=tenant.code,
        name=tenant.name,
        status=tenant.status,
        auth_revision=tenant.auth_revision,
        created_at=tenant.created_at,
    )


@router.get("", response_model=list[TenantOut], summary="企业列表（平台）")
async def list_tenants(
    session: SessionDep,
    actor: CanRead,
    q: str | None = Query(default=None, max_length=64),
) -> list[TenantOut]:
    """企业清单是平台职责；企业管理员只通过成员/企业详情看自己企业。"""
    if not actor.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="企业列表仅平台管理员可访问"
        )
    stmt = select(Tenant)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Tenant.code.ilike(pattern), Tenant.name.ilike(pattern)))
    rows = (await session.scalars(stmt.order_by(Tenant.created_at))).all()
    return [_tenant_out(t) for t in rows]


@router.post(
    "", response_model=TenantOut, status_code=status.HTTP_201_CREATED, summary="创建企业（平台）"
)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    session: SessionDep,
    actor: CanCreate,
) -> TenantOut:
    if not actor.platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可创建企业")
    if await session.scalar(select(Tenant.id).where(Tenant.code == payload.code)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="企业 code 已存在")
    tenant = Tenant(code=payload.code, name=payload.name)
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="企业 code 已存在"
        ) from exc
    # 新企业自动实例化内置企业管理员模板（任务 4.5：种子幂等、两种管理员菜单差异）
    await ensure_tenant_admin_role(session, tenant.id)
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="tenant.create",
        target_type="tenant",
        target_id=str(tenant.id),
        detail={"code": tenant.code, "name": tenant.name},
        request=request,
    )
    await session.commit()
    return _tenant_out(tenant)


async def _get_tenant_or_404(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="企业不存在")
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut, summary="企业详情（成员/平台可见）")
async def get_tenant(tenant_id: uuid.UUID, session: SessionDep, _actor: CanRead) -> TenantOut:
    """RLS：成员可读自己企业；平台可读全部；其余企业不可见（404 不泄露存在性）。"""
    return _tenant_out(await _get_tenant_or_404(session, tenant_id))


@router.patch("/{tenant_id}", response_model=TenantOut, summary="修改/停用企业（平台）")
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    request: Request,
    session: SessionDep,
    actor: CanUpdate,
) -> TenantOut:
    if not actor.platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可修改企业")
    tenant = await _get_tenant_or_404(session, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(tenant, field, value)
    # 企业授权修订：停用/恢复影响运行票据复查
    tenant.auth_revision += 1
    await session.flush()
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="tenant.update",
        target_type="tenant",
        target_id=str(tenant.id),
        detail=changes,
        request=request,
    )
    await session.commit()
    return _tenant_out(tenant)


# --------------------------------------------------------------------------- 成员


async def _member_out_map(
    session: AsyncSession, memberships: list[TenantMembership]
) -> list[MemberOut]:
    if not memberships:
        return []
    user_ids = [m.user_id for m in memberships]
    users = (await session.scalars(select(User).where(User.id.in_(user_ids)))).all()
    users_by_id = {u.id: u for u in users}
    rows = await session.execute(
        select(MembershipRole.membership_id, Role.code).join(
            Role, Role.id == MembershipRole.role_id
        )
    )
    codes_by_member: dict[uuid.UUID, list[str]] = {}
    for mid, code in rows.all():
        codes_by_member.setdefault(mid, []).append(code)
    items: list[MemberOut] = []
    for m in memberships:
        user = users_by_id.get(m.user_id)
        if user is None:
            continue
        items.append(
            MemberOut(
                id=m.id,
                tenant_id=m.tenant_id,
                user_id=m.user_id,
                account=user.account,
                display_name=user.display_name,
                user_is_active=user.is_active,
                department_id=m.department_id,
                status=m.status,
                revision=m.revision,
                role_codes=sorted(codes_by_member.get(m.id, [])),
                created_at=m.created_at,
            )
        )
    return items


async def _require_tenant_access(
    session: AsyncSession, actor: Identity, tenant_id: uuid.UUID
) -> Tenant:
    """成员子资源的企业校验：平台放行；否则必须是当前上下文企业（避免跨企业操作，
    即使 RLS 兜底也给出一致的 404/403 语义）。"""
    tenant = await _get_tenant_or_404(session, tenant_id)
    if actor.platform_admin:
        return tenant
    if actor.tenant_id is None or actor.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="企业或成员不存在")
    return tenant


def _require_writable_tenant(tenant: Tenant, actor: Identity) -> Tenant:
    """R11：成员写入口统一"有效企业"检查。

    停用企业的普通业务写全部拒绝；平台管理员可在停用企业上做恢复性成员
    调整（remove/status），但不得新增成员或授予角色——由调用点区分。
    """
    if tenant.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="企业已停用，成员写操作被拒绝"
        )
    return tenant


async def _get_membership_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, membership_id: uuid.UUID
) -> TenantMembership:
    membership = await session.get(TenantMembership, membership_id)
    if membership is None or membership.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员不存在")
    return membership


async def _active_admin_membership_ids(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = await session.execute(
        text(
            "SELECT m.id FROM tenant_memberships m"
            " JOIN membership_roles mr ON mr.membership_id = m.id"
            " JOIN roles r ON r.id = mr.role_id"
            " WHERE m.tenant_id = :t AND m.status = 'active' AND r.code = :code"
        ),
        {"t": str(tenant_id), "code": ADMIN_ROLE_CODE},
    )
    return [row[0] for row in rows.all()]


async def _protect_last_admin(
    session: AsyncSession,
    actor: Identity,
    tenant_id: uuid.UUID,
    affected_membership_id: uuid.UUID,
    will_remove_admin: bool,
) -> None:
    """保护最后一名活跃企业管理员（平台也不例外——平台的豁免路径是明确的
    “停用企业”流程 tenant:update，而不是悄悄清空管理员授权）。"""
    if not will_remove_admin:
        return
    admins = await _active_admin_membership_ids(session, tenant_id)
    if len(admins) <= 1 and affected_membership_id in admins:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="企业必须保留至少一名活跃管理员；请先授予其他成员管理员角色",
        )


@router.get("/{tenant_id}/members", response_model=Page[MemberOut], summary="成员列表（分页）")
async def list_members(
    tenant_id: uuid.UUID,
    session: SessionDep,
    actor: CanReadMembers,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    q: str | None = Query(default=None, max_length=64, description="按账号或用户名模糊搜索"),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(active|disabled)$"),
) -> Page[MemberOut]:
    await _require_tenant_access(session, actor, tenant_id)
    stmt = select(TenantMembership).where(TenantMembership.tenant_id == tenant_id)
    if status_filter:
        stmt = stmt.where(TenantMembership.status == status_filter)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            TenantMembership.user_id.in_(
                select(User.id).where(
                    or_(User.account.ilike(pattern), User.display_name.ilike(pattern))
                )
            )
        )
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (
        await session.scalars(
            stmt.order_by(TenantMembership.created_at.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    return Page[MemberOut](
        items=await _member_out_map(session, list(rows)),
        total=total or 0,
        page=page,
        size=size,
    )


@router.post(
    "/{tenant_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="关联成员（平台授权）",
)
async def link_member(
    tenant_id: uuid.UUID,
    payload: MemberCreate,
    request: Request,
    session: SessionDep,
    actor: CanLinkMember,
) -> MemberOut:
    """平台明确授权后关联已有全局账号为本企业成员。

    - 不接受“按同名自动关联”；账号名必须精确匹配已存在的全局用户；
    - 企业管理员没有 member:link 码，无法把账号拉入企业（防跨企业绑架）；
    - 同名账号冲突不会泄露该用户的其他企业信息。
    """
    if not actor.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="成员关联需平台授权（member:link）；企业管理员请走成员管理",
        )
    tenant = await _require_tenant_access(session, actor, tenant_id)
    _require_writable_tenant(tenant, actor)
    if payload.user_id is not None:
        user = await session.get(User, payload.user_id)
    elif payload.account:
        user = await session.scalar(select(User).where(User.account == payload.account))
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供 user_id 或 account 之一"
        )
    if user is None:
        # 不区分“账号不存在”与“无权得知”，只说明无法按明确授权关联
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="全局账号不存在，无法关联"
        )

    membership = TenantMembership(tenant_id=tenant_id, user_id=user.id)
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该用户已是本企业成员"
        ) from exc
    if payload.role_codes:
        await _apply_member_roles(session, membership, payload.role_codes, actor, replace=True)
    if payload.department_id is not None:
        await _validate_department(session, tenant_id, payload.department_id)
        membership.department_id = payload.department_id
    membership.revision += 1
    await session.flush()

    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="member.link",
        target_type="membership",
        target_id=str(membership.id),
        detail={"tenant_id": str(tenant_id), "user_id": str(user.id)},
        request=request,
    )
    item = (await _member_out_map(session, [membership]))[0]
    await session.commit()
    return item


@router.patch("/{tenant_id}/members/{membership_id}", response_model=MemberOut, summary="修改成员")
async def update_member(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberUpdate,
    request: Request,
    session: SessionDep,
    actor: CanManageMember,
) -> MemberOut:
    """企业管理员：改本企业成员的部门/状态。只影响本企业成员属性。"""
    tenant = await _require_tenant_access(session, actor, tenant_id)
    if not actor.platform_admin:
        _require_writable_tenant(tenant, actor)
    membership = await _get_membership_or_404(session, tenant_id, membership_id)
    changes = payload.model_dump(exclude_unset=True)

    if "department_id" in changes and changes["department_id"] is not None:
        await _validate_department(session, tenant_id, changes["department_id"])
        membership.department_id = changes["department_id"]
    if changes.get("department_id") is None and "department_id" in changes:
        membership.department_id = None

    if "status" in changes and changes["status"] is not None:
        if membership.status == "active" and changes["status"] == "disabled":
            admins = await _active_admin_membership_ids(session, tenant_id)
            await _protect_last_admin(
                session, actor, tenant_id, membership.id, membership.id in admins
            )
        membership.status = changes["status"]

    membership.revision += 1
    await session.flush()
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="member.update",
        target_type="membership",
        target_id=str(membership.id),
        detail={"tenant_id": str(tenant_id), **changes},
        request=request,
    )
    # commit 前读：GUC 事务本地，commit 后成员行按当前作用域不可见
    item = (await _member_out_map(session, [membership]))[0]
    await session.commit()
    return item


@router.put(
    "/{tenant_id}/members/{membership_id}/roles",
    response_model=MemberOut,
    summary="设置成员角色",
)
async def set_member_roles(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRolesIn,
    request: Request,
    session: SessionDep,
    actor: CanManageMember,
) -> MemberOut:
    """成员角色整体替换：只接受本企业角色；不能超出操作者可授予范围。"""
    tenant = await _require_tenant_access(session, actor, tenant_id)
    _require_writable_tenant(tenant, actor)
    membership = await _get_membership_or_404(session, tenant_id, membership_id)
    was_admin = membership.id in await _active_admin_membership_ids(session, tenant_id)
    target_role_codes = list(dict.fromkeys(payload.role_codes))
    roles = (
        await session.scalars(
            select(Role).where(
                Role.code.in_(target_role_codes or ["__none__"]),
                Role.scope == "tenant",
                Role.tenant_id == tenant_id,
            )
        )
    ).all()
    by_code = {r.code: r for r in roles}
    unknown = [c for c in target_role_codes if c not in by_code]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色不存在: {', '.join(sorted(unknown))}",
        )
    for role in roles:
        if role.scope != "tenant" or role.tenant_id != tenant_id:
            # 跨企业角色或平台角色：拒绝（审计对象保持原企业归属）
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"角色 {role.code} 不可授予成员（作用域/企业不符）",
            )
    if not actor.platform_admin and roles:
        # 防“自我提升”：被授予角色的全部权限码必须是操作者实际持有的码，
        # 且角色本身必须在操作者的可授予角色范围内（本企业、操作者持有）
        role_ids = [r.id for r in roles]
        role_perm_rows = await session.execute(
            select(RolePermission.role_id, Permission.code)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id.in_(role_ids))
        )
        perms_by_role: dict[uuid.UUID, set[str]] = {}
        for rid, code in role_perm_rows.all():
            perms_by_role.setdefault(rid, set()).add(code)
        for role in roles:
            granted = perms_by_role.get(role.id, set())
            beyond = sorted(granted - actor.permissions)
            if beyond:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"角色 {role.code} 含操作者未持有的权限，不能授予",
                )
            if role.code not in set(actor.membership_role_codes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"不能授予操作者未持有的角色: {role.code}",
                )
    will_be_admin = ADMIN_ROLE_CODE in target_role_codes
    await _protect_last_admin(
        session, actor, tenant_id, membership.id, was_admin and not will_be_admin
    )
    await _apply_member_roles(session, membership, target_role_codes, actor, replace=True)
    membership.revision += 1
    await session.flush()
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="member.assign_role",
        target_type="membership",
        target_id=str(membership.id),
        detail={"tenant_id": str(tenant_id), "role_codes": target_role_codes},
        request=request,
    )
    item = (await _member_out_map(session, [membership]))[0]
    await session.commit()
    return item


@router.delete(
    "/{tenant_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除成员（解除企业关联，不删全局账号）",
)
async def remove_member(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    actor: CanRemoveMember,
) -> None:
    tenant = await _require_tenant_access(session, actor, tenant_id)
    if tenant.status != "active" and not actor.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="企业已停用；仅平台可执行解除成员等恢复性操作",
        )
    membership = await _get_membership_or_404(session, tenant_id, membership_id)
    admins = await _active_admin_membership_ids(session, tenant_id)
    await _protect_last_admin(session, actor, tenant_id, membership.id, membership.id in admins)
    await session.delete(membership)
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="member.remove",
        target_type="membership",
        target_id=str(membership_id),
        detail={"tenant_id": str(tenant_id)},
        request=request,
    )
    await session.commit()


# --------------------------------------------------------------------------- 内部辅助


async def _validate_department(
    session: AsyncSession, tenant_id: uuid.UUID, department_id: uuid.UUID
) -> None:
    dept = await session.get(Department, department_id)
    if dept is None or dept.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="部门不存在或不属于本企业",
        )


async def _apply_member_roles(
    session: AsyncSession,
    membership: TenantMembership,
    role_codes: list[str],
    actor: Identity,
    *,
    replace: bool,
) -> None:
    if replace:
        existing = (
            await session.scalars(
                select(MembershipRole).where(MembershipRole.membership_id == membership.id)
            )
        ).all()
        for row in existing:
            await session.delete(row)
        await session.flush()
    if not role_codes:
        return
    # R11：角色按 (scope=tenant, tenant_id) 精确查找。历史上这里只按 code 取，
    # 双企业同名 tenant_admin 会各插一行造成复合外键 IntegrityError/跨企业授权。
    roles = (
        await session.scalars(
            select(Role).where(
                Role.code.in_(role_codes),
                Role.scope == "tenant",
                Role.tenant_id == membership.tenant_id,
            )
        )
    ).all()
    by_code = {r.code: r for r in roles}
    unknown = [c for c in role_codes if c not in by_code]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色不存在或不属于本企业: {', '.join(sorted(unknown))}",
        )
    for role in by_code.values():
        session.add(
            MembershipRole(
                membership_id=membership.id,
                role_id=role.id,
                tenant_id=membership.tenant_id,
                granted_by=actor.user.id,
            )
        )
    await session.flush()


# RolePermission 延迟导入，避免循环
