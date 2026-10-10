"""审计日志查询接口。

权限码：audit:read。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission
from app.models import AuditLog, User
from app.schemas import AuditLogOut, Page

router = APIRouter(tags=["audit"])

CanRead = Annotated[User, Depends(require_permission("audit:read"))]


async def _account_map(session: AsyncSession, actor_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """批量取操作人账号；操作人可能已被删除，故用 LEFT JOIN 语义。"""
    ids = [i for i in actor_ids if i is not None]
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.account).where(User.id.in_(ids)))
    return {user_id: account for user_id, account in rows.all()}


@router.get("", response_model=Page[AuditLogOut], summary="审计日志（分页）")
async def list_audit_logs(
    session: SessionDep,
    _actor: CanRead,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    action: str | None = Query(default=None, max_length=64, description="按动作精确筛选"),
    actor_id: uuid.UUID | None = Query(default=None, description="按操作人筛选"),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)

    total = await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = (
        await session.scalars(
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()

    accounts = await _account_map(session, [log.actor_id for log in rows if log.actor_id])
    return Page[AuditLogOut](
        items=[
            AuditLogOut(
                id=log.id,
                actor_id=log.actor_id,
                # 优先用冗余存的账号快照（用户被删也能追溯）；
                # 仅迁移回填不到的存量数据才回退到 join
                actor_account=log.actor_account
                or (accounts.get(log.actor_id) if log.actor_id else None),
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                detail=log.detail,
                # psycopg 把 INET 读成 ipaddress 对象，响应里统一转成字符串
                ip=str(log.ip) if log.ip is not None else None,
                created_at=log.created_at,
            )
            for log in rows
        ],
        total=total or 0,
        page=page,
        size=size,
    )
