"""数据库连接：迁移角色与运行角色分离（设计 §5）。

- `runtime_engine` / `SessionLocal`：请求路径**必须**走运行角色
  （非 owner、非 BYPASSRLS）+ 事务本地 GUC，让 RLS 真实生效。
- `owner_engine`：仅迁移/引导脚本使用；请求代码不得导入。

GUC 语义：`set_config(..., true)` 是事务本地——提交/回滚即失效，连接回池后
不会把上一个请求的作用域带给下一个请求。缺失上下文时策略求值为 false，
失败关闭。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_settings = get_settings()

runtime_engine: AsyncEngine = create_async_engine(
    _settings.runtime_sqlalchemy_uri,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = async_sessionmaker(runtime_engine, expire_on_commit=False, class_=AsyncSession)

# owner 连接只允许迁移/脚本使用（alembic env / bootstrap / seed / 迁移工具）。
# 应用请求代码禁止导入 owner_engine / OwnerSessionLocal——它们绕过 RLS。
owner_engine: AsyncEngine = create_async_engine(
    _settings.sqlalchemy_uri,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=2,
)

OwnerSessionLocal = async_sessionmaker(owner_engine, expire_on_commit=False, class_=AsyncSession)


@dataclass(frozen=True)
class TenantContext:
    """事务本地身份上下文。None 值写入 GUC 空串：策略按“未选择”失败关闭。"""

    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    platform: bool = False
    login: bool = False


async def apply_tenant_context(session: AsyncSession, ctx: TenantContext) -> None:
    """在当前事务内设置 air_agent.* GUC。

    必须在任何业务查询之前调用；事务结束后自动失效（is_local=true）。
    """
    await session.execute(text("SELECT 1"))  # 确保事务已打开
    pairs: list[tuple[str, str]] = [
        ("air_agent.user_id", ctx.user_id or ""),
        ("air_agent.tenant_id", ctx.tenant_id or ""),
        ("air_agent.session_id", ctx.session_id or ""),
        ("air_agent.platform", "on" if ctx.platform else ""),
        ("air_agent.login", "on" if ctx.login else ""),
    ]
    for key, value in pairs:
        await session.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话。"""
    async with SessionLocal() as session:
        yield session


async def engine_dispose_all() -> None:
    """测试/停机辅助：同时释放两个引擎。"""
    await runtime_engine.dispose()
    await owner_engine.dispose()
