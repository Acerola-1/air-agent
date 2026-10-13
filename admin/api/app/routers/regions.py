"""行政区划（省/市/区县）读取接口。

只读，且**任何已登录用户**都可访问：这是选关注城市的候选来源，普通用户也要浏览。
写操作（维护区划）不在本期范围内，将来若要开放，用 `region:manage` 权限码保护。

三级共 3347 行，前端用级联选择器逐级拉取，所以提供按父级查询而不是一次性返回全树。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, get_identity
from app.models import Region
from app.schemas import RegionOut

router = APIRouter(tags=["regions"], dependencies=[Depends(get_identity)])


async def _parents_with_children(session: AsyncSession, codes: list[str]) -> set[str]:
    """一次性查出哪些 code 有子节点，避免逐行 N+1。"""
    if not codes:
        return set()
    rows = await session.scalars(
        select(Region.parent_code).where(Region.parent_code.in_(codes)).group_by(Region.parent_code)
    )
    return {code for code in rows.all() if code is not None}


@router.get("/children", response_model=list[RegionOut], summary="按父级取子节点")
async def list_children(
    session: SessionDep,
    parent_code: str | None = Query(
        default=None, description="父级区划编码；不传则返回省级（level=1）"
    ),
) -> list[RegionOut]:
    if parent_code is None:
        rows = (
            await session.scalars(
                select(Region).where(Region.level == 1).order_by(Region.sort, Region.code)
            )
        ).all()
    else:
        rows = (
            await session.scalars(
                select(Region)
                .where(Region.parent_code == parent_code)
                .order_by(Region.sort, Region.code)
            )
        ).all()

    parents = await _parents_with_children(session, [region.code for region in rows])
    return [RegionOut.of(region, has_children=region.code in parents) for region in rows]


@router.get("/search", response_model=list[RegionOut], summary="按名称搜索区划")
async def search_regions(
    session: SessionDep,
    q: str = Query(min_length=1, max_length=32, description="名称关键词"),
    limit: int = Query(default=30, ge=1, le=200),
) -> list[RegionOut]:
    rows = (
        await session.scalars(
            select(Region)
            .where(Region.name.ilike(f"%{q}%"))
            .order_by(Region.level, Region.code)
            .limit(limit)
        )
    ).all()
    parents = await _parents_with_children(session, [region.code for region in rows])
    return [RegionOut.of(region, has_children=region.code in parents) for region in rows]


@router.get("/{code}", response_model=RegionOut, summary="区划详情")
async def get_region(code: str, session: SessionDep) -> RegionOut:
    region = await session.get(Region, code)
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="行政区划不存在")
    children = await session.scalar(
        select(func.count()).select_from(Region).where(Region.parent_code == code)
    )
    return RegionOut.of(region, has_children=bool(children))
