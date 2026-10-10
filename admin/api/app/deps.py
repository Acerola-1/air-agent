"""FastAPI 依赖：会话解析与权限校验。

`get_session` 在 FastAPI 里是按请求缓存的，所以 `get_current_user` 和路由函数拿到的是
同一个 AsyncSession 实例，不会出现多连接/事务分裂。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.security import SESSION_COOKIE, verify_session
from app.services.rbac import get_permission_codes

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, session: SessionDep) -> User:
    """从 session cookie 解析当前用户。

    只信任签名与身份；授权状态（权限码）每次现查，见 `services/rbac.py`。
    """
    token = request.cookies.get(SESSION_COOKIE)
    payload = verify_session(token, get_settings().auth_secret)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或会话已过期"
        )

    try:
        user_id = uuid.UUID(payload.user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话内容非法"
        ) from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(*codes: str) -> Callable[..., Coroutine[Any, Any, User]]:
    """依赖工厂：要求当前用户具备全部给定权限码。

    用法: `user: Annotated[User, Depends(require_permission("user:create"))]`
    """

    async def checker(user: CurrentUser, session: SessionDep) -> User:
        granted = await get_permission_codes(session, user.id)
        missing = [code for code in codes if code not in granted]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: {', '.join(missing)}",
            )
        return user

    return checker
