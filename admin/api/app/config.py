"""服务配置。

`DATABASE_URI` 与 `AUTH_SECRET` 都复用仓库根目录 `.env` 的既有取值：
- `AUTH_SECRET` 必须和前端 (`frontend/.env.local`) 完全一致，否则签发的
  session cookie 前端验不过，`frontend/middleware.ts` 的路由保护会立刻失效。
- `DATABASE_URI` 在宿主机上是 `localhost:15432`，在 compose 网络内是 `postgres:5432`。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 平台/前端共用的库（线程、checkpoint、store 都在里面）
    database_uri: str = "postgresql://air_agent:air_agent_dev@localhost:15432/air_agent"
    # 与 frontend/.env.local 保持一致
    auth_secret: str = ""

    host: str = "0.0.0.0"
    port: int = 8100
    # 本地开发允许直连 admin 前端；上线请收敛为具体来源
    cors_origins: list[str] = ["http://localhost:3100", "http://127.0.0.1:3100"]
    # 本地是 http，必须为 False；生产 https 下置 True
    cookie_secure: bool = False

    root_account: str = "root"
    root_display_name: str = "超级管理员"
    root_password: str = "root"

    @property
    def sqlalchemy_uri(self) -> str:
        """把 `postgresql://` 规整成 SQLAlchemy async 驱动需要的 `postgresql+psycopg://`.

        容器里 `DATABASE_URI` 必须是裸 `postgresql://`（Go core-server 用 libpq 解析，
        不认 `+psycopg` 前缀），所以转换只能在这里做，不能改环境变量本身。
        """
        uri = self.database_uri
        if uri.startswith("postgresql+"):
            return uri
        if uri.startswith("postgresql://"):
            return "postgresql+psycopg://" + uri[len("postgresql://") :]
        return uri


@lru_cache
def get_settings() -> Settings:
    return Settings()
