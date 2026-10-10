"""air_agent 后台管理服务入口。

独立容器部署，不与 langgraph-api 同进程：后台是低频非关键路径，agent 是关键路径，
两者不应共享生死；后台还要独立跑数据库迁移与后台界面。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import engine
from app.routers import audit, auth, permissions, regions, roles, users

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 早失败：AUTH_SECRET 缺失时签发的 cookie 前端验不过，问题会很难定位
    if not settings.auth_secret:
        raise RuntimeError(
            "AUTH_SECRET 未设置。session token 依赖它，请确认仓库根目录 .env 存在。"
        )
    yield
    await engine.dispose()


app = FastAPI(
    title="air_agent admin API",
    description="用户后台：RBAC 权限、用户/角色管理、行政区划关注城市",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # 会话依赖 cookie，必须显式指定来源而不能用 "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth")
app.include_router(users.router, prefix="/users")
app.include_router(roles.router, prefix="/roles")
app.include_router(permissions.router, prefix="/permissions")
app.include_router(regions.router, prefix="/regions")
app.include_router(audit.router, prefix="/audit-logs")


@app.get("/health", tags=["meta"], summary="健康检查")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
