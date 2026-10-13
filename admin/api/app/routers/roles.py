"""角色管理接口（平台 / 企业双作用域）。

- 平台角色（scope=platform）：仅平台管理员可管理；企业侧只读模板。
- 企业角色（scope=tenant）：企业管理员管理**本企业**角色；RLS WITH CHECK 保证
  非平台上下文只能创建/修改自己企业的角色。
- 内置模板（is_system）可读；平台模板仅平台可改，企业模板仅本企业可改。
- 权限授予范围（任务 4.3）：企业角色只能取 `grantable_to_tenants=true` 的码；
  非平台操作者只能授予自己实际持有的码——授权不得超过操作者可授予范围。
- 角色分配走成员接口（/tenants/{id}/members/...）；旧 user_roles 不再参与租户授权。

权限码：role:read / role:create / role:update / role:delete / role:assign_permission。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission, require_tenant_context
from app.models import MembershipRole, Permission, Role, RolePermission, TenantMembership
from app.schemas import RoleCreate, RoleDetail, RoleOut, RolePermissionsIn, RoleUpdate
from app.services.audit import record_audit
from app.services.identity import Identity

# R02：角色写操作（平台/企业均会改变授权）必须声明会话上下文并保持一致。
router = APIRouter(tags=["roles"], dependencies=[Depends(require_tenant_context)])

CanRead = Annotated[Identity, Depends(require_permission("role:read"))]
CanCreate = Annotated[Identity, Depends(require_permission("role:create"))]
CanUpdate = Annotated[Identity, Depends(require_permission("role:update"))]
CanDelete = Annotated[Identity, Depends(require_permission("role:delete"))]
CanAssign = Annotated[Identity, Depends(require_permission("role:assign_permission"))]


async def _counts(
    session: AsyncSession, role_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """每个角色的权限数与成员数（批量，避免 N+1）。

    成员数按 membership_roles 统计（租户授权真相源）；RLS 保证非平台上下文
    只数得出本企业成员。
    """
    if not role_ids:
        return {}, {}
    permission_rows = await session.execute(
        select(RolePermission.role_id, func.count())
        .where(RolePermission.role_id.in_(role_ids))
        .group_by(RolePermission.role_id)
    )
    member_rows = await session.execute(
        select(MembershipRole.role_id, func.count())
        .where(MembershipRole.role_id.in_(role_ids))
        .group_by(MembershipRole.role_id)
    )
    return (
        {role_id: count for role_id, count in permission_rows.all()},
        {role_id: count for role_id, count in member_rows.all()},
    )


async def _permission_codes(session: AsyncSession, role_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list(rows.all())


def _to_out(role: Role, permission_count: int, member_count: int) -> RoleOut:
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        sort=role.sort,
        created_at=role.created_at,
        permission_count=permission_count,
        user_count=member_count,
        scope=role.scope,
        tenant_id=role.tenant_id,
    )


def _to_detail(
    role: Role, permission_codes: list[str], permission_count: int, member_count: int
) -> RoleDetail:
    return RoleDetail(
        **_to_out(role, permission_count, member_count).model_dump(),
        permission_codes=permission_codes,
    )


def _require_active_enterprise(actor: Identity, *, what: str) -> None:
    """F04：企业业务的写入口统一校验"有效企业 + 有效成员"。

    平台管理员例外——停用企业的恢复/管理走明确平台入口（tenant:update 等），
    不会被本检查误阻断；非平台操作者所在企业一旦停用/成员停用，所有企业
    角色写操作拒绝。
    """
    if actor.platform_admin:
        return
    if actor.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="尚未选择企业，无法执行该企业业务操作",
        )
    if actor.tenant_status != "active" or actor.membership_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="企业或成员身份已停用，无法执行该企业业务操作",
        )


async def _bump_role_holder_revisions(session: AsyncSession, role: Role) -> None:
    """F02：角色授权变化与受影响成员修订推进处于**同一可靠事务**。

    membership_revision 是运行票据/运行身份 revalidate 的比较项——权限被
    替换或角色被删除后，旧票据的下一次模型/工具外呼即被拒绝，而不是等
    会话自然失效。平台角色不携带租户成员，跳过。
    """
    if role.scope != "tenant":
        return
    member_ids = (
        await session.scalars(
            select(MembershipRole.membership_id).where(MembershipRole.role_id == role.id)
        )
    ).all()
    if not member_ids:
        return
    await session.execute(
        update(TenantMembership)
        .where(TenantMembership.id.in_(list(member_ids)))
        .values(revision=TenantMembership.revision + 1)
    )


async def _get_role_or_404(session: AsyncSession, role_id: uuid.UUID) -> Role:
    """RLS 已保证跨企业角色不可见；“存在但不属于你的企业”与“不存在”一律 404。"""
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    return role


def _require_manageable(role: Role, actor: Identity) -> None:
    if role.scope == "platform" and not actor.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="平台角色仅平台管理员可修改"
        )
    if (
        role.scope == "tenant"
        and not actor.platform_admin
        and (actor.tenant_id is None or role.tenant_id != actor.tenant_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    # F04：企业角色的修改/删除/权限分配都是企业业务写入口——非平台操作者所在
    # 企业或成员停用后拒绝；平台走明确平台权限做恢复性管理，不受此限。
    if role.scope == "tenant":
        _require_active_enterprise(actor, what="修改企业角色")


@router.get("", response_model=list[RoleOut], summary="角色列表（作用域隔离）")
async def list_roles(session: SessionDep, _user: CanRead) -> list[RoleOut]:
    """平台模板对已登录用户可读（定义）；企业角色仅本企业（RLS）。"""
    roles = (await session.scalars(select(Role).order_by(Role.scope, Role.sort, Role.code))).all()
    ids = [r.id for r in roles]
    permission_counts, member_counts = await _counts(session, ids)
    return [
        _to_out(role, permission_counts.get(role.id, 0), member_counts.get(role.id, 0))
        for role in roles
    ]


@router.post("", response_model=RoleDetail, status_code=status.HTTP_201_CREATED, summary="新建角色")
async def create_role(
    payload: RoleCreate, request: Request, session: SessionDep, actor: CanCreate
) -> RoleDetail:
    """创建角色。非平台管理员只能创建当前企业的角色（RLS WITH CHECK + 显式校验）。"""
    scope = "platform" if (payload.scope == "platform" and actor.platform_admin) else "tenant"
    tenant_id: uuid.UUID | None = None
    if scope == "tenant":
        if payload.tenant_id is not None:
            if not actor.platform_admin or payload.tenant_id != actor.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="平台管理员创建企业角色时才能指定目标企业",
                )
            tenant_id = payload.tenant_id
        elif actor.tenant is not None:
            tenant_id = actor.tenant.id
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="尚未选择企业，无法创建企业角色"
            )
        # F04：企业角色创建属于企业业务写入口
        _require_active_enterprise(actor, what="创建企业角色")

    stmt = select(Role.id).where(Role.code == payload.code, Role.scope == scope)
    if tenant_id is not None:
        stmt = stmt.where(Role.tenant_id == tenant_id)
    if scope == "platform":
        stmt = select(Role.id).where(Role.code == payload.code, Role.scope == "platform")
    if await session.scalar(stmt) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="角色 code 已存在")

    role = Role(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        sort=payload.sort,
        is_system=False,
        scope=scope,
        tenant_id=tenant_id,
    )
    session.add(role)
    await session.flush()

    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="role.create",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "name": role.name, "scope": scope},
        request=request,
    )
    await session.commit()
    return _to_detail(role, [], 0, 0)


@router.get("/{role_id}", response_model=RoleDetail, summary="角色详情")
async def get_role(role_id: uuid.UUID, session: SessionDep, _user: CanRead) -> RoleDetail:
    role = await _get_role_or_404(session, role_id)
    permission_counts, member_counts = await _counts(session, [role.id])
    return _to_detail(
        role,
        await _permission_codes(session, role.id),
        permission_counts.get(role.id, 0),
        member_counts.get(role.id, 0),
    )


@router.patch("/{role_id}", response_model=RoleDetail, summary="修改角色")
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    request: Request,
    session: SessionDep,
    actor: CanUpdate,
) -> RoleDetail:
    role = await _get_role_or_404(session, role_id)
    _require_manageable(role, actor)
    changes = payload.model_dump(exclude_unset=True)
    if role.is_system and "code" in changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="内置角色不允许改 code")

    for field, value in changes.items():
        setattr(role, field, value)
    await session.flush()

    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="role.update",
        target_type="role",
        target_id=str(role.id),
        detail=changes,
        request=request,
    )
    await session.commit()

    permission_counts, member_counts = await _counts(session, [role.id])
    return _to_detail(
        role,
        await _permission_codes(session, role.id),
        permission_counts.get(role.id, 0),
        member_counts.get(role.id, 0),
    )


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除角色")
async def delete_role(
    role_id: uuid.UUID, request: Request, session: SessionDep, actor: CanDelete
) -> None:
    role = await _get_role_or_404(session, role_id)
    _require_manageable(role, actor)
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="内置角色不允许删除")

    affected_members = await session.scalar(
        select(func.count()).select_from(MembershipRole).where(MembershipRole.role_id == role.id)
    )
    # F02：成员修订必须在删除角色（级联清 membership_roles）之前、同一事务推进
    await _bump_role_holder_revisions(session, role)
    await session.delete(role)
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="role.delete",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "affected_members": affected_members},
        request=request,
    )
    await session.commit()


@router.put("/{role_id}/permissions", response_model=RoleDetail, summary="设置角色权限")
async def set_role_permissions(
    role_id: uuid.UUID,
    payload: RolePermissionsIn,
    request: Request,
    session: SessionDep,
    actor: CanAssign,
) -> RoleDetail:
    """整体替换角色的权限集合。

    约束：
    1. 平台专属权限（grantable_to_tenants=false）不能授予企业角色——防止租户角色
       提权成平台能力；内置平台模板仅平台管理员可改；
    2. 非平台操作者只能授予自己实际持有的码——授权不得超过操作者可授予范围；
    3. 企业角色改动只影响本企业授权。
    """
    role = await _get_role_or_404(session, role_id)
    _require_manageable(role, actor)

    wanted = set(payload.permission_codes)
    if wanted:
        rows = await session.execute(
            select(Permission.code, Permission.grantable_to_tenants).where(
                Permission.code.in_(wanted)
            )
        )
        found = dict(rows.all())
        unknown = wanted - set(found)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"权限码不存在: {', '.join(sorted(unknown))}",
            )
        if role.scope == "tenant":
            not_grantable = sorted(c for c, g in found.items() if not g)
            if not_grantable:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"以下权限不可授予企业角色: {', '.join(not_grantable)}",
                )
        if not actor.platform_admin:
            beyond = sorted(c for c in wanted if c not in actor.permissions)
            if beyond:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"不能授予操作者未持有的权限: {', '.join(beyond)}",
                )

    await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    if wanted:
        ids = (
            await session.scalars(select(Permission.id).where(Permission.code.in_(wanted)))
        ).all()
        session.add_all([RolePermission(role_id=role.id, permission_id=pid) for pid in ids])
    await session.flush()
    # F02：权限替换与持有成员修订推进同事务提交；任何一步失败整体回滚，
    # 不存在"权限变了但旧运行仍被放行的修订"半状态。
    await _bump_role_holder_revisions(session, role)

    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="role.assign_permission",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "permission_count": len(wanted)},
        request=request,
    )
    # 先读后提交：GUC 事务本地，commit 后 role_permissions 在租户作用域会重新不可见
    permission_counts, member_counts = await _counts(session, [role.id])
    codes = await _permission_codes(session, role.id)
    await session.commit()
    return _to_detail(role, codes, permission_counts.get(role.id, 0), member_counts.get(role.id, 0))
