"""关注城市读写，含「最多 5 个」的并发安全保证。

上限校验放在服务层事务内完成：

    1. 对目标用户行加行锁（`SELECT ... FOR UPDATE`）
    2. 在锁内计数并校验
    3. 写入

同一用户的其他并发修改会阻塞在第 1 步，因此「计数 → 写入」之间不存在竞态，
效果与 `BEFORE INSERT` 触发器等价，但逻辑留在 Python 里、便于测试与改动。

代价：绕过本服务直接写 SQL 时没有兜底。当前只有本服务这一个写入方，可以接受；
若将来出现第二个写入方（例如数据修复脚本），需要重新评估是否补回数据库层约束。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MembershipFollowedRegion,
    Region,
    TenantMembership,
    User,
    UserFollowedRegion,
)

MAX_FOLLOWED_REGIONS = 5


class FollowedRegionsError(Exception):
    """关注城市校验失败。"""


class TooManyRegionsError(FollowedRegionsError):
    pass


class UnknownRegionError(FollowedRegionsError):
    pass


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """按部署形态锁定用户聚合，序列化同一用户的并发写入。"""
    from app.services.remote_mode import identity_backend_mode

    if identity_backend_mode() == "remote":
        exists = await session.scalar(
            text("SELECT id FROM iam_reference_users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        if exists is None:
            raise FollowedRegionsError("用户引用不存在")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"air-agent.followed-user:{user_id}"},
        )
        return

    locked = await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if locked is None:
        raise FollowedRegionsError("用户不存在")


async def _resolve_regions(session: AsyncSession, codes: list[str]) -> list[Region]:
    """按传入顺序取出 Region；不存在的 code 直接报错（外键也会兜底）。"""
    if not codes:
        return []
    rows = (await session.scalars(select(Region).where(Region.code.in_(codes)))).all()
    by_code = {row.code: row for row in rows}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise UnknownRegionError(f"行政区划不存在: {', '.join(missing)}")
    return [by_code[code] for code in codes]


async def list_followed_regions(session: AsyncSession, user_id: uuid.UUID) -> list[Region]:
    rows = (
        await session.scalars(
            select(Region)
            .join(UserFollowedRegion, UserFollowedRegion.region_code == Region.code)
            .where(UserFollowedRegion.user_id == user_id)
            .order_by(UserFollowedRegion.created_at, Region.code)
        )
    ).all()
    return list(rows)


async def set_followed_regions(
    session: AsyncSession, user_id: uuid.UUID, codes: list[str]
) -> list[Region]:
    """整体替换某用户的关注城市。原子、幂等。

    采用「整体替换」而不是逐个增删：后台界面的交互就是「选一批城市 → 保存」，
    整体替换不会留下半完成状态。
    """
    unique_codes = list(dict.fromkeys(codes))  # 去重且保序
    if len(unique_codes) > MAX_FOLLOWED_REGIONS:
        raise TooManyRegionsError(f"关注城市最多 {MAX_FOLLOWED_REGIONS} 个")

    await _lock_user(session, user_id)
    regions = await _resolve_regions(session, unique_codes)

    # 逐个 ORM 删除而非批量语句：上限只有 5 行，且避免 session.execute()
    existing = (
        await session.scalars(
            select(UserFollowedRegion).where(UserFollowedRegion.user_id == user_id)
        )
    ).all()
    for row in existing:
        await session.delete(row)
    await session.flush()

    for code in unique_codes:
        session.add(UserFollowedRegion(user_id=user_id, region_code=code))
    await session.flush()
    return regions


# --------------------------------------------------------------- 成员作用域（企业上下文）


async def _lock_membership(
    session: AsyncSession, membership_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """remote 锁引用聚合并核对同租户绑定；local 保留旧成员行锁。"""
    from app.services.remote_mode import identity_backend_mode

    if identity_backend_mode() == "remote":
        exists = await session.scalar(
            text(
                "SELECT id FROM iam_reference_memberships"
                " WHERE id = :membership_id AND tenant_id = :tenant_id"
            ),
            {"membership_id": membership_id, "tenant_id": tenant_id},
        )
        if exists is None:
            raise FollowedRegionsError("成员引用不存在或企业绑定不匹配")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"air-agent.followed-membership:{membership_id}"},
        )
        return

    locked = await session.scalar(
        select(TenantMembership.id).where(TenantMembership.id == membership_id).with_for_update()
    )
    if locked is None:
        raise FollowedRegionsError("成员身份不存在")


async def list_membership_followed_regions(
    session: AsyncSession, membership_id: uuid.UUID
) -> list[Region]:
    rows = (
        await session.scalars(
            select(Region)
            .join(
                MembershipFollowedRegion,
                MembershipFollowedRegion.region_code == Region.code,
            )
            .where(MembershipFollowedRegion.membership_id == membership_id)
            .order_by(MembershipFollowedRegion.created_at, Region.code)
        )
    ).all()
    return list(rows)


async def set_membership_followed_regions(
    session: AsyncSession, membership_id: uuid.UUID, tenant_id: uuid.UUID, codes: list[str]
) -> list[Region]:
    """整体替换成员（企业作用域）的关注城市。同一账号在不同企业偏好互不影响。"""
    unique_codes = list(dict.fromkeys(codes))
    if len(unique_codes) > MAX_FOLLOWED_REGIONS:
        raise TooManyRegionsError(f"关注城市最多 {MAX_FOLLOWED_REGIONS} 个")

    await _lock_membership(session, membership_id, tenant_id)
    regions = await _resolve_regions(session, unique_codes)

    existing = (
        await session.scalars(
            select(MembershipFollowedRegion).where(
                MembershipFollowedRegion.membership_id == membership_id
            )
        )
    ).all()
    for row in existing:
        await session.delete(row)
    await session.flush()

    for code in unique_codes:
        session.add(
            MembershipFollowedRegion(
                membership_id=membership_id, region_code=code, tenant_id=tenant_id
            )
        )
    await session.flush()
    return regions
