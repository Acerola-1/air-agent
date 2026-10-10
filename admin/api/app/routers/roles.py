"""角色管理接口。

权限码：role:read / role:create / role:update / role:delete / role:assign_permission。

内置角色（`is_system`）只允许改描述与排序，不允许删除或改 code——`super_admin` 是
兜底身份，删掉会导致没人能再配置权限。已分配给用户的角色删除时会连带解除分配
（关联表 ON DELETE CASCADE），因此删除前会返回受影响人数供前端确认。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission
from app.models import Permission, Role, RolePermission, User, UserRole
from app.schemas import RoleCreate, RoleDetail, RoleOut, RolePermissionsIn, RoleUpdate
from app.services.audit import record_audit

router = APIRouter(tags=["roles"])

CanRead = Annotated[User, Depends(require_permission("role:read"))]
CanCreate = Annotated[User, Depends(require_permission("role:create"))]
CanUpdate = Annotated[User, Depends(require_permission("role:update"))]
CanDelete = Annotated[User, Depends(require_permission("role:delete"))]
CanAssign = Annotated[User, Depends(require_permission("role:assign_permission"))]


async def _counts(session: AsyncSession) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """一次性取回「每个角色的权限数」和「每个角色的用户数」，避免逐行查询。"""
    permission_rows = await session.execute(
        select(RolePermission.role_id, func.count()).group_by(RolePermission.role_id)
    )
    user_rows = await session.execute(
        select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
    )
    return (
        {role_id: count for role_id, count in permission_rows.all()},
        {role_id: count for role_id, count in user_rows.all()},
    )


async def _permission_codes(session: AsyncSession, role_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list(rows.all())


def _to_detail(
    role: Role, permission_codes: list[str], permission_count: int, user_count: int
) -> RoleDetail:
    return RoleDetail(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        sort=role.sort,
        created_at=role.created_at,
        permission_count=permission_count,
        user_count=user_count,
        permission_codes=permission_codes,
    )


async def _get_role_or_404(session: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")
    return role


@router.get("", response_model=list[RoleOut], summary="角色列表")
async def list_roles(session: SessionDep, _user: CanRead) -> list[RoleOut]:
    roles = (
        await session.scalars(select(Role).order_by(Role.sort, Role.code))
    ).all()
    permission_counts, user_counts = await _counts(session)
    return [
        RoleOut(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            sort=role.sort,
            created_at=role.created_at,
            permission_count=permission_counts.get(role.id, 0),
            user_count=user_counts.get(role.id, 0),
        )
        for role in roles
    ]


@router.post(
    "", response_model=RoleDetail, status_code=status.HTTP_201_CREATED, summary="新建角色"
)
async def create_role(
    payload: RoleCreate, request: Request, session: SessionDep, actor: CanCreate
) -> RoleDetail:
    exists = await session.scalar(select(Role.id).where(Role.code == payload.code))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="角色 code 已存在")

    role = Role(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        sort=payload.sort,
        is_system=False,
    )
    session.add(role)
    await session.flush()

    record_audit(
        session,
        actor_id=actor.id,
        actor_account=actor.account,
        action="role.create",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "name": role.name},
        request=request,
    )
    await session.commit()
    return _to_detail(role, [], 0, 0)


@router.get("/{role_id}", response_model=RoleDetail, summary="角色详情")
async def get_role(role_id: uuid.UUID, session: SessionDep, _user: CanRead) -> RoleDetail:
    role = await _get_role_or_404(session, role_id)
    permission_counts, user_counts = await _counts(session)
    return _to_detail(
        role,
        await _permission_codes(session, role.id),
        permission_counts.get(role.id, 0),
        user_counts.get(role.id, 0),
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
    changes = payload.model_dump(exclude_unset=True)
    if role.is_system and "code" in changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="内置角色不允许改 code"
        )

    for field, value in changes.items():
        setattr(role, field, value)
    await session.flush()

    record_audit(
        session,
        actor_id=actor.id,
        actor_account=actor.account,
        action="role.update",
        target_type="role",
        target_id=str(role.id),
        detail=changes,
        request=request,
    )
    await session.commit()

    permission_counts, user_counts = await _counts(session)
    return _to_detail(
        role,
        await _permission_codes(session, role.id),
        permission_counts.get(role.id, 0),
        user_counts.get(role.id, 0),
    )


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除角色")
async def delete_role(
    role_id: uuid.UUID, request: Request, session: SessionDep, actor: CanDelete
) -> None:
    role = await _get_role_or_404(session, role_id)
    if role.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="内置角色不允许删除"
        )

    affected_users = await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)
    )
    await session.delete(role)
    record_audit(
        session,
        actor_id=actor.id,
        actor_account=actor.account,
        action="role.delete",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "affected_users": affected_users},
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
    """整体替换角色的权限集合。传入的权限码必须都存在。"""
    role = await _get_role_or_404(session, role_id)

    wanted = set(payload.permission_codes)
    if wanted:
        found = set(
            (
                await session.scalars(
                    select(Permission.code).where(Permission.code.in_(wanted))
                )
            ).all()
        )
        unknown = wanted - found
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"权限码不存在: {', '.join(sorted(unknown))}",
            )

    await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    if wanted:
        ids = (
            await session.scalars(select(Permission.id).where(Permission.code.in_(wanted)))
        ).all()
        session.add_all(
            [RolePermission(role_id=role.id, permission_id=pid) for pid in ids]
        )
    await session.flush()

    record_audit(
        session,
        actor_id=actor.id,
        actor_account=actor.account,
        action="role.assign_permission",
        target_type="role",
        target_id=str(role.id),
        detail={"code": role.code, "permission_count": len(wanted)},
        request=request,
    )
    await session.commit()

    permission_counts, user_counts = await _counts(session)
    return _to_detail(
        role,
        await _permission_codes(session, role.id),
        permission_counts.get(role.id, 0),
        user_counts.get(role.id, 0),
    )
