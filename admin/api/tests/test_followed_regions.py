"""`app.services.followed_regions` 的集成测试。

需要可用的 PostgreSQL（`DATABASE_URI`）与已导入的行政区划数据：

    .venv/bin/python -m pytest tests/test_followed_regions.py -q

每个用例自建临时用户并在结束时清理，不污染既有数据。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Region, User
from app.security import hash_password
from app.services.followed_regions import (
    MAX_FOLLOWED_REGIONS,
    TooManyRegionsError,
    UnknownRegionError,
    list_followed_regions,
    set_followed_regions,
)


async def _district_codes(session, count: int) -> list[str]:
    """取若干个真实的区县编码，避免把测试写死到具体数据集版本上。"""
    rows = (
        await session.scalars(select(Region.code).where(Region.level == 3).limit(count))
    ).all()
    if len(rows) < count:
        pytest.skip("regions 表为空或区划数据不足，请先运行 scripts/import_regions.py")
    return list(rows)


@pytest.fixture
async def user_id() -> uuid.UUID:
    async with SessionLocal() as session:
        user = User(
            account=f"tmp_{uuid.uuid4().hex[:12]}",
            display_name="测试用户",
            password_hash=hash_password("tmp-pass"),
        )
        session.add(user)
        await session.commit()
        uid = user.id

    yield uid

    async with SessionLocal() as session:
        victim = await session.get(User, uid)
        if victim is not None:
            await session.delete(victim)  # 级联删除 user_followed_regions
            await session.commit()


async def test_set_and_list(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        codes = await _district_codes(session, 3)
        saved = await set_followed_regions(session, user_id, codes)
        await session.commit()
        assert [r.code for r in saved] == codes

    async with SessionLocal() as session:
        listed = await list_followed_regions(session, user_id)
        assert {r.code for r in listed} == set(codes)


async def test_accepts_exactly_max(user_id: uuid.UUID) -> None:
    """边界值：正好 5 个必须通过。"""
    async with SessionLocal() as session:
        codes = await _district_codes(session, MAX_FOLLOWED_REGIONS)
        await set_followed_regions(session, user_id, codes)
        await session.commit()

    async with SessionLocal() as session:
        listed = await list_followed_regions(session, user_id)
        assert len(listed) == MAX_FOLLOWED_REGIONS


async def test_rejects_over_max(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        codes = await _district_codes(session, MAX_FOLLOWED_REGIONS + 1)
        with pytest.raises(TooManyRegionsError):
            await set_followed_regions(session, user_id, codes)
        await session.rollback()


async def test_rejects_unknown_code(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        with pytest.raises(UnknownRegionError):
            await set_followed_regions(session, user_id, ["999999"])
        await session.rollback()


async def test_deduplicates(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        codes = await _district_codes(session, 2)
        saved = await set_followed_regions(session, user_id, [codes[0], codes[0], codes[1]])
        await session.commit()
        assert [r.code for r in saved] == codes

    async with SessionLocal() as session:
        assert len(await list_followed_regions(session, user_id)) == 2


async def test_replace_clears_previous(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        codes = await _district_codes(session, 3)
        await set_followed_regions(session, user_id, codes[:2])
        await session.commit()

    async with SessionLocal() as session:
        await set_followed_regions(session, user_id, [codes[2]])
        await session.commit()

    async with SessionLocal() as session:
        listed = await list_followed_regions(session, user_id)
        assert [r.code for r in listed] == [codes[2]]


async def test_empty_clears_all(user_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        codes = await _district_codes(session, 2)
        await set_followed_regions(session, user_id, codes)
        await session.commit()

    async with SessionLocal() as session:
        await set_followed_regions(session, user_id, [])
        await session.commit()

    async with SessionLocal() as session:
        assert await list_followed_regions(session, user_id) == []


async def test_province_and_city_levels_allowed(user_id: uuid.UUID) -> None:
    """省/市/区县都可选（最小到区县），不是只允许区县。"""
    async with SessionLocal() as session:
        province = await session.scalar(select(Region.code).where(Region.level == 1).limit(1))
        city = await session.scalar(select(Region.code).where(Region.level == 2).limit(1))
        assert province and city
        saved = await set_followed_regions(session, user_id, [province, city])
        await session.commit()
        assert {r.code for r in saved} == {province, city}
