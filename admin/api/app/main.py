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
from app.db import engine_dispose_all
from app.routers import (
    audit,
    auth,
    bindings,
    departments,
    feedback,
    ledger,
    permissions,
    regions,
    roles,
    tenants,
    users,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 早失败（任务 2.5/3.3：配置缺失必须失败关闭，绝不回退匿名/JSON 身份）
    if not settings.runtime_database_uri:
        raise RuntimeError(
            "RUNTIME_DATABASE_URI 未设置：请求路径必须走非 owner 运行角色（RLS 生效）。"
        )
    if not settings.runtime_ticket_key_pairs:
        raise RuntimeError("RUNTIME_TICKET_KEYS 未设置：无法签发/校验运行票据。")
    if not settings.internal_api_token:
        raise RuntimeError(
            "INTERNAL_API_TOKEN 未设置：内部票据端点将全部拒绝，等于服务不可用；拒绝启动。"
        )
    yield
    await engine_dispose_all()


app = FastAPI(
    title="air_agent admin API",
    description=(
        "统一身份后台：企业/成员/角色 RBAC、可撤销会话、运行票据、MCP 绑定、行政区划关注城市"
    ),
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
app.include_router(tenants.router, prefix="/tenants")
app.include_router(departments.router, prefix="/departments")
app.include_router(users.router, prefix="/users")
app.include_router(roles.router, prefix="/roles")
app.include_router(permissions.router, prefix="/permissions")
app.include_router(regions.router, prefix="/regions")
app.include_router(audit.router, prefix="/audit-logs")
app.include_router(bindings.router, prefix="/mcp-bindings")
app.include_router(ledger.router, prefix="/agent")
app.include_router(feedback.router, prefix="/agent")
# change complete-agent-observability：观测查询/价格管理（本域新增；不改既有 19 路径）
from app.routers import observability as observability_router  # noqa: E402

app.include_router(observability_router.router, prefix="/agent")


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
