"""用户管理接口。

平台职责接口（任务 4.2/4.3 收敛）：本路由只服务**全局账号**管理，要求平台
权限码（user:read / user:create / user:update / user:delete / user:assign_role /
user:reset_password，均 grantable_to_tenants=false）。租户管理员没有这些码，
旧接口对其直接 403——不存在“接口兼容获得平台管理能力”的旁路；企业成员/部门/
角色管理走 /tenants/{tenant_id}/members 等新接口。

自我保护：不允许删除或停用自己——否则管理员会把自己锁在系统之外。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission
from app.models import Role, User, UserFollowedRegion, UserRole
from app.schemas import (
    FollowedRegionsIn,
    Page,
    RegionOut,
    RoleBrief,
    UserCreate,
    UserOut,
    UserPasswordIn,
    UserRolesIn,
    UserUpdate,
)
from app.security import hash_password
from app.services.audit import record_audit
from app.services.followed_regions import (
    FollowedRegionsError,
    list_followed_regions,
    set_followed_regions,
)
from app.services.identity import Identity

router = APIRouter(tags=["users"])

CanRead = Annotated[Identity, Depends(require_permission("user:read"))]
CanCreate = Annotated[Identity, Depends(require_permission("user:create"))]
CanUpdate = Annotated[Identity, Depends(require_permission("user:update"))]
CanDelete = Annotated[Identity, Depends(require_permission("user:delete"))]
CanAssignRole = Annotated[Identity, Depends(require_permission("user:assign_role"))]
CanResetPassword = Annotated[Identity, Depends(require_permission("user:reset_password"))]
CanReadFollowed = Annotated[Identity, Depends(require_permission("followed:read"))]
CanManageFollowed = Annotated[Identity, Depends(require_permission("followed:manage"))]


async def _roles_by_user(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Role]]:
    """批量取用户的角色，避免逐行查询。"""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(UserRole.user_id, Role)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
        .order_by(Role.sort, Role.code)
    )
    result: dict[uuid.UUID, list[Role]] = {}
    for user_id, role in rows.all():
        result.setdefault(user_id, []).append(role)
    return result


async def _followed_by_user(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(UserFollowedRegion.user_id, UserFollowedRegion.region_code)
        .where(UserFollowedRegion.user_id.in_(user_ids))
        .order_by(UserFollowedRegion.created_at, UserFollowedRegion.region_code)
    )
    result: dict[uuid.UUID, list[str]] = {}
    for user_id, code in rows.all():
        result.setdefault(user_id, []).append(code)
    return result


def _to_out(user: User, roles: list[Role], followed: list[str]) -> UserOut:
    return UserOut(
        id=user.id,
        account=user.account,
        display_name=user.display_name,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        roles=[RoleBrief.of(r) for r in roles],
        followed_region_codes=followed,
    )


async def _get_user_or_404(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


async def _resolve_role_ids(
    session: AsyncSession, role_codes: list[str], *, platform_only: bool = False
) -> list[uuid.UUID]:
    if not role_codes:
        return []
    wanted = set(role_codes)
    stmt = select(Role.code, Role.id, Role.scope).where(Role.code.in_(wanted))
    rows = await session.execute(stmt)
    found = {}
    for code, role_id, scope in rows.all():
        if platform_only and scope != "platform":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"角色 {code} 不是平台角色；企业角色请走成员接口",
            )
        found[code] = role_id
    unknown = wanted - set(found)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色不存在: {', '.join(sorted(unknown))}",
        )
    return [found[code] for code in role_codes]


# --------------------------------------------------------------------------- 列表 / 详情


@router.get("", response_model=Page[UserOut], summary="用户列表（分页）")
async def list_users(
    session: SessionDep,
    _actor: CanRead,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    q: str | None = Query(default=None, max_length=64, description="按账号或用户名模糊搜索"),
    is_active: bool | None = Query(default=None),
    role_code: str | None = Query(default=None),
) -> Page[UserOut]:
    stmt = select(User)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(User.account.ilike(pattern), User.display_name.ilike(pattern)))
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if role_code:
        stmt = (
            stmt.join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == role_code)
        )

    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (
        await session.scalars(
            stmt.order_by(User.created_at.desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    ids = [user.id for user in rows]
    roles_map = await _roles_by_user(session, ids)
    followed_map = await _followed_by_user(session, ids)

    return Page[UserOut](
        items=[
            _to_out(user, roles_map.get(user.id, []), followed_map.get(user.id, []))
            for user in rows
        ],
        total=total or 0,
        page=page,
        size=size,
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="新建用户")
async def create_user(
    payload: UserCreate, request: Request, session: SessionDep, actor: CanCreate
) -> UserOut:
    exists = await session.scalar(select(User.id).where(User.account == payload.account))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="账号已存在")

    user = User(
        account=payload.account,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_active=payload.is_active,
    )
    session.add(user)
    await session.flush()

    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.create",
        target_type="user",
        target_id=str(user.id),
        detail={"account": user.account, "display_name": user.display_name},
        request=request,
    )
    await session.commit()
    return _to_out(user, [], [])


@router.get("/{user_id}", response_model=UserOut, summary="用户详情")
async def get_user(user_id: uuid.UUID, session: SessionDep, _actor: CanRead) -> UserOut:
    user = await _get_user_or_404(session, user_id)
    roles_map = await _roles_by_user(session, [user.id])
    followed_map = await _followed_by_user(session, [user.id])
    return _to_out(user, roles_map.get(user.id, []), followed_map.get(user.id, []))


# --------------------------------------------------------------------------- 修改 / 删除


@router.patch("/{user_id}", response_model=UserOut, summary="修改用户")
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    session: SessionDep,
    actor: CanUpdate,
) -> UserOut:
    user = await _get_user_or_404(session, user_id)
    changes = payload.model_dump(exclude_unset=True)

    if user.id == actor.user.id and changes.get("is_active") is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能停用自己的账号")

    for field, value in changes.items():
        setattr(user, field, value)
    await session.flush()

    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.update",
        target_type="user",
        target_id=str(user.id),
        detail=changes,
        request=request,
    )
    # 先读关联再提交：GUC 是事务本地，commit 后 user_roles 会重新不可见
    roles_map = await _roles_by_user(session, [user.id])
    followed_map = await _followed_by_user(session, [user.id])
    await session.commit()
    return _to_out(user, roles_map.get(user.id, []), followed_map.get(user.id, []))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除用户")
async def delete_user(
    user_id: uuid.UUID, request: Request, session: SessionDep, actor: CanDelete
) -> None:
    user = await _get_user_or_404(session, user_id)
    if user.id == actor.user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己的账号")

    account = user.account
    await session.delete(user)
    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.delete",
        target_type="user",
        target_id=str(user_id),
        detail={"account": account},
        request=request,
    )
    await session.commit()


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT, summary="重置密码")
async def reset_password(
    user_id: uuid.UUID,
    payload: UserPasswordIn,
    request: Request,
    session: SessionDep,
    actor: CanResetPassword,
) -> None:
    user = await _get_user_or_404(session, user_id)
    user.password_hash = hash_password(payload.password)
    # 有权主体改密：auth_revision 递增 + 撤销全部既有会话（任务 3.1）
    user.auth_revision += 1
    await session.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"u": str(user_id)},
    )
    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.reset_password",
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    await session.commit()


@router.put("/{user_id}/roles", response_model=UserOut, summary="设置用户角色")
async def set_user_roles(
    user_id: uuid.UUID,
    payload: UserRolesIn,
    request: Request,
    session: SessionDep,
    actor: CanAssignRole,
) -> UserOut:
    user = await _get_user_or_404(session, user_id)
    role_ids = await _resolve_role_ids(
        session, list(dict.fromkeys(payload.role_codes)), platform_only=True
    )

    existing = (await session.scalars(select(UserRole).where(UserRole.user_id == user.id))).all()
    for row in existing:
        await session.delete(row)
    await session.flush()

    for role_id in role_ids:
        session.add(UserRole(user_id=user.id, role_id=role_id, granted_by=actor.user.id))
    await session.flush()

    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.assign_role",
        target_type="user",
        target_id=str(user.id),
        detail={"role_codes": payload.role_codes},
        request=request,
    )
    roles_map = await _roles_by_user(session, [user.id])
    followed_map = await _followed_by_user(session, [user.id])
    await session.commit()
    return _to_out(user, roles_map.get(user.id, []), followed_map.get(user.id, []))


# --------------------------------------------------------------------------- 关注城市


@router.get("/{user_id}/followed-regions", response_model=list[str], summary="用户的关注城市编码")
async def get_followed_regions(
    user_id: uuid.UUID, session: SessionDep, _actor: CanReadFollowed
) -> list[str]:
    await _get_user_or_404(session, user_id)
    regions = await list_followed_regions(session, user_id)
    return [region.code for region in regions]


@router.put(
    "/{user_id}/followed-regions",
    response_model=list[str],
    summary="设置用户的关注城市（最多 5 个）",
)
async def put_followed_regions(
    user_id: uuid.UUID,
    payload: FollowedRegionsIn,
    request: Request,
    session: SessionDep,
    actor: CanManageFollowed,
) -> list[str]:
    await _get_user_or_404(session, user_id)
    try:
        regions = await set_followed_regions(session, user_id, payload.region_codes)
    except FollowedRegionsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_audit(
        session,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="user.set_followed_regions",
        target_type="user",
        target_id=str(user_id),
        detail={"region_codes": [region.code for region in regions]},
        request=request,
    )
    await session.commit()
    return [region.code for region in regions]


@router.get(
    "/{user_id}/followed-regions/detail",
    response_model=list[RegionOut],
    summary="关注城市详情（含名称）",
)
async def get_followed_regions_detail(
    user_id: uuid.UUID, session: SessionDep, _actor: CanReadFollowed
) -> list[RegionOut]:
    await _get_user_or_404(session, user_id)
    regions = await list_followed_regions(session, user_id)
    return [RegionOut.of(region) for region in regions]
