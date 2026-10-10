"""异步数据库连接。

用 psycopg3 的 async 驱动（`postgresql+psycopg://`）。库本身是平台共用的那个
PG（线程/checkpoint/store 都在里面），这里只额外挂 RBAC 与行政区划表。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.sqlalchemy_uri,
    pool_pre_ping=True,  # 容器重启/连接被回收后自动重连，避免首个请求必失败
    pool_size=5,
    max_overflow=5,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话。"""
    async with SessionLocal() as session:
        yield session
