"""认证接口：登录 / 登出 / 当前用户。

签发的 cookie 与前端现有实现完全兼容（同名 `air_agent_session`、同 HMAC 格式、
同 30 天有效期），因此 `frontend/middleware.ts` 的路由保护无需改动即可识别。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.models import User
from app.routers.permissions import load_permissions
from app.schemas import (
    FollowedRegionsIn,
    LoginIn,
    MeOut,
    PermissionNode,
    RegionOut,
    user_out,
)
from app.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    hash_password,
    sign_session,
    verify_password,
    verify_session,
)
from app.services.audit import record_audit
from app.services.followed_regions import (
    FollowedRegionsError,
    list_followed_regions,
    set_followed_regions,
)
from app.services.permission_tree import build_tree
from app.services.rbac import get_permission_codes, get_roles

router = APIRouter(tags=["auth"])

# 账号不存在时也做一次 bcrypt 比对，避免用响应时间探测账号是否存在。
# 代价是启动时多一次哈希计算（约 50-100ms），可以接受。
_DUMMY_HASH = hash_password("timing-equalization-dummy")


def set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    token = sign_session(str(user.id), user.account, settings.auth_secret)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def build_me(session: AsyncSession, user: User) -> MeOut:
    roles = await get_roles(session, user.id)
    permissions = await get_permission_codes(session, user.id)
    regions = await list_followed_regions(session, user.id)
    base = user_out(
        user,
        roles=roles,
        followed_region_codes=[region.code for region in regions],
    )
    return MeOut(**base.model_dump(), permissions=sorted(permissions))


@router.post("/login", response_model=MeOut, summary="登录")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
) -> MeOut:
    user = await session.scalar(select(User).where(User.account == payload.account))

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误"
        )

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误"
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    user.last_login_at = datetime.now(UTC)
    record_audit(
        session,
        actor_id=user.id,
        actor_account=user.account,
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    await session.commit()

    set_session_cookie(response, user)
    return await build_me(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="登出")
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    """清除会话 cookie。未登录时也返回 204，避免暴露会话状态。"""
    payload = verify_session(
        request.cookies.get(SESSION_COOKIE), get_settings().auth_secret
    )
    actor_id: uuid.UUID | None = None
    actor_account: str | None = None
    if payload is not None:
        actor_account = payload.account
        try:
            actor_id = uuid.UUID(payload.user_id)
        except ValueError:
            actor_id = None

    if actor_id is not None:
        record_audit(
            session,
            actor_id=actor_id,
            actor_account=actor_account,
            action="auth.logout",
            target_type="user",
            target_id=str(actor_id),
            request=request,
        )
        await session.commit()

    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut, summary="当前登录用户")
async def me(user: CurrentUser, session: SessionDep) -> MeOut:
    return await build_me(session, user)


@router.get("/menus", response_model=list[PermissionNode], summary="当前用户可见菜单树")
async def menus(user: CurrentUser, session: SessionDep) -> list[PermissionNode]:
    """按当前用户的权限码过滤出的菜单树，供侧边栏渲染。

    不需要额外权限：任何人都有权知道自己能看到哪些菜单。
    """
    permissions = await load_permissions(session)
    granted = await get_permission_codes(session, user.id)
    return build_tree(permissions, allowed=granted)


@router.get(
    "/me/followed-regions", response_model=list[RegionOut], summary="我的关注城市"
)
async def my_followed_regions(user: CurrentUser, session: SessionDep) -> list[RegionOut]:
    regions = await list_followed_regions(session, user.id)
    return [RegionOut.of(region) for region in regions]


@router.put(
    "/me/followed-regions", response_model=list[RegionOut], summary="设置我的关注城市（最多 5 个）"
)
async def put_my_followed_regions(
    payload: FollowedRegionsIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> list[RegionOut]:
    """用户管理自己的关注城市，不需要 followed:manage（那是给管理员改别人的）。"""
    try:
        regions = await set_followed_regions(session, user.id, payload.region_codes)
    except FollowedRegionsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_audit(
        session,
        actor_id=user.id,
        actor_account=user.account,
        action="profile.set_followed_regions",
        target_type="user",
        target_id=str(user.id),
        detail={"region_codes": [region.code for region in regions]},
        request=request,
    )
    await session.commit()
    return [RegionOut.of(region) for region in regions]
