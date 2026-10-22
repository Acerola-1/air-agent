"""air_agent 后台管理服务入口。

独立容器部署，不与 langgraph-api 同进程：后台是低频非关键路径，agent 是关键路径，
两者不应共享生死；后台还要独立跑数据库迁移与后台界面。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import engine_dispose_all, runtime_engine
from app.routers import (
    audit,
    auth,
    console,
    demand_insights,
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
    from app.services.remote_mode import identity_backend_mode

    if identity_backend_mode() == "remote":
        from common.identity.remote import require_remote_config

        require_remote_config()
    from app.services.physical_separation import verify_database_mode

    async with runtime_engine.connect() as connection:
        await verify_database_mode(connection, identity_backend_mode())
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
    if identity_backend_mode() == "remote":
        from common.identity.db import close_pool
        from common.identity.remote import close_shared_client

        await close_pool()
        close_shared_client()


app = FastAPI(
    title="air_agent admin API",
    description=(
        "统一身份后台：可撤销会话、运行票据、运行台账与业务能力目录；身份与授权以 IAM 为准"
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

# I2 remote：AGENT_IDENTITY_BACKEND=remote 时先挂 IAM SSO/会话门面（同名路由优先命中，
# /auth/login、/switch-tenant、/me、/logout 由 remote 门面接管；auth.router 其余端点
# ——/menus、/me/followed-regions、/runtime-ticket（业务与内部）仍可用。local 不注册（零变化）。
if os.environ.get("AGENT_IDENTITY_BACKEND", "").strip().lower() == "remote":
    from app.routers import iam_remote as _iam_remote

    app.include_router(_iam_remote.router)

    from common.identity.db import IdentityBackendUnavailable
    from common.identity.remote import IdentityBackendConfigError, RemoteProtocolError
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import SQLAlchemyError

    async def identity_dependency_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "身份依赖暂不可用，请稍后重试"})

    async def data_dependency_unavailable(request, exc):
        # 数据库迁移/连接失败不能被伪装成 IAM 故障，否则会把运维问题导向错误方向。
        return JSONResponse(status_code=503, content={"detail": "后台数据服务暂不可用，请稍后重试"})

    for error_type in (
        IdentityBackendUnavailable,
        IdentityBackendConfigError,
        RemoteProtocolError,
    ):
        app.add_exception_handler(error_type, identity_dependency_unavailable)
    app.add_exception_handler(SQLAlchemyError, data_dependency_unavailable)

    @app.middleware("http")
    async def remote_identity_routes(request, call_next):
        from fastapi.responses import JSONResponse

        path = request.url.path
        if (
            any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in (
                    "/users",
                    "/tenants",
                    "/departments",
                    "/roles",
                    "/permissions",
                    "/audit-logs",
                )
            )
            or path == "/auth/change-password"
        ):
            return JSONResponse(
                # 410：功能已迁出本服务。不能复用 409——前端把 409 视为
                # context_changed，会误报"企业上下文已被其他标签页变更"。
                status_code=410,
                content={
                    "code": "management_moved",
                    "detail": "用户、企业与权限请在统一身份管理后台操作",
                    "iam_url": (
                        os.environ.get("IAM_ADMIN_URL", "").strip().rstrip("/")
                        or "http://127.0.0.1:19101"
                    )
                    + "/admin",
                },
            )
        return await call_next(request)


app.include_router(auth.router, prefix="/auth")
app.include_router(tenants.router, prefix="/tenants")
app.include_router(departments.router, prefix="/departments")
app.include_router(users.router, prefix="/users")
app.include_router(roles.router, prefix="/roles")
app.include_router(permissions.router, prefix="/permissions")
app.include_router(regions.router, prefix="/regions")
app.include_router(audit.router, prefix="/audit-logs")
app.include_router(ledger.router, prefix="/agent")
app.include_router(feedback.router, prefix="/agent")
# change add-agent-admin-console：运营读模型与轻量反馈处理（本域新增，旧端点不变）
app.include_router(console.router, prefix="/agent")
# change add-question-demand-insights：需求洞察（本域新增，不改 graph/MCP/依赖锁）
app.include_router(demand_insights.router, prefix="/agent")
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
