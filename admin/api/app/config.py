"""服务配置。

`DATABASE_URI` 与 `AUTH_SECRET` 都复用仓库根目录 `.env` 的既有取值：
- `AUTH_SECRET` 是**旧** HMAC 会话密钥，仅保留给迁移工具做兼容核验；统一会话
  （0003 起）不再用它签发 cookie。
- `DATABASE_URI` 在宿主机上是 `localhost:15432`，在 compose 网络内是 `postgres:5432`。

企业身份域新增的密钥与连接（缺任何一项服务拒绝启动——失败关闭）：
- `RUNTIME_DATABASE_URI`：运行角色（非 owner、非 BYPASSRLS）连接，所有请求路径
  走它，让 RLS 真实生效；`DATABASE_URI` 只留给迁移与引导脚本。
- `RUNTIME_TICKET_KEYS`：JSON 对象 `{"<当前kid>": "<密钥>", "<上一kid>": "<密钥>"}`
  （首个条目用于签发；推荐形态，kid 与密钥内容绑定，交错部署/重排不失效——R09）。
  过渡兼容旧字符串数组 `["<当前密钥>", "<上一密钥>"]`（按 v1..vN 索引编号）。
  运行票据独立于浏览器会话密钥，支持有界轮换。
- `RUNTIME_TICKET_AUDIENCE` / `RUNTIME_TICKET_TTL_SECONDS`：票据受众与时效。
- `INTERNAL_API_TOKEN`：Next 服务端与 admin API 之间的内部端点凭据（运行票据签发）。
- `CSRF_ALLOWED_ORIGINS`：cookie 写接口允许的 Origin 精确列表。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    agent_interactions_enabled: bool = False
    agent_interactions_enabled_at: dt.datetime | None = None
    agent_capability_selection_enabled: bool = False

    @field_validator("agent_interactions_enabled_at", mode="before")
    @classmethod
    def empty_collection_timestamp(cls, value):
        return None if value == "" else value

    ledger_content_retention_days: int = Field(default=90, ge=1, le=3650)
    ledger_content_retention_days_by_tenant: dict[str, int] = Field(default_factory=dict)

    @field_validator("ledger_content_retention_days_by_tenant")
    @classmethod
    def validate_retention_policy(cls, values: dict[str, int]) -> dict[str, int]:
        """租户保留策略来自可信部署配置，非法值拒绝启动."""
        if any(not 1 <= days <= 3650 for days in values.values()):
            raise ValueError("企业原文保留天数必须在1到3650之间")
        return {str(uuid.UUID(tenant)): days for tenant, days in values.items()}

    # 平台/前端共用的库（线程、checkpoint、store 都在里面）
    database_uri: str = "postgresql://air_agent:air_agent_dev@localhost:15432/air_agent"
    # 仅迁移/引导使用：运行角色连接（RLS 生效路径）。缺失 → 启动失败。
    # 别名 ADMIN_RUNTIME_DATABASE_URI：与 langgraph 容器内的 RUNTIME_DATABASE_URI 区分
    # （host 与 compose 网络指向不同的连接串）。
    runtime_database_uri: str = Field(default="", alias="ADMIN_RUNTIME_DATABASE_URI")
    # 旧 HMAC 会话密钥：只用于“识别并拒绝旧 cookie”与迁移工具核验，不再签发
    auth_secret: str = ""

    # 运行票据（服务端 → LangGraph），不复用浏览器旧密钥
    runtime_ticket_keys: str = ""
    # F05：服务级只读校准密钥（与用户运行票据**不同**的密钥集，独立轮换）。
    # 未配置 = 不签发服务校准票据（校准按"阻塞"如实报告，不降级复用用户会话）。
    ledger_reconcile_keys: str = ""
    ledger_reconcile_audience: str = "ledger-reconcile"
    runtime_ticket_audience: str = "langgraph-runtime"
    runtime_ticket_ttl_seconds: int = 60

    # Next/BFF 服务端内部端点凭据（运行票据签发）；浏览器不可获得
    internal_api_token: str = ""

    host: str = "0.0.0.0"
    port: int = 8100
    # 本地开发允许直连 admin 前端；上线请收敛为具体来源
    cors_origins: list[str] = ["http://localhost:3100", "http://127.0.0.1:3100"]
    # cookie 写接口允许的 Origin（含聊天前端）
    csrf_allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    ]
    # 本地是 http，必须为 False；生产 https 下置 True
    cookie_secure: bool = False

    root_account: str = "root"
    root_display_name: str = "超级管理员"
    root_password: str = "root"
    # 仅本地学习/开发环境显式置 true；生产必须用 ADMIN_BOOTSTRAP_* 显式授权平台账号
    allow_default_root_bootstrap: bool = False

    @property
    def sqlalchemy_uri(self) -> str:
        """把 `postgresql://` 规整成 SQLAlchemy async 驱动需要的 `postgresql+psycopg://`.

        容器里 `DATABASE_URI` 必须是裸 `postgresql://`（Go core-server 用 libpq 解析，
        不认 `+psycopg` 前缀），所以转换只能在这里做，不能改环境变量本身。
        """
        return _normalize_pg_uri(self.database_uri)

    @property
    def runtime_sqlalchemy_uri(self) -> str:
        return _normalize_pg_uri(self.runtime_database_uri)

    @property
    def ledger_reconcile_key_pairs(self) -> list[tuple[str, str]]:
        """`LEDGER_RECONCILE_KEYS` → [(kid, secret)]；与运行票据同样支持轮换形态."""
        return _parse_key_pairs(self.ledger_reconcile_keys, "LEDGER_RECONCILE_KEYS")

    @property
    def runtime_ticket_key_pairs(self) -> list[tuple[str, str]]:
        """`RUNTIME_TICKET_KEYS` → [(kid, secret)]，当前签发密钥在前。

        与 Agent 侧 `src/common/identity/ticket.py::parse_keys` 保持字节兼容：
        推荐 JSON 对象 `{"kid": "secret", ...}`（kid 与密钥内容绑定，重排/前插
        不会使旧票据失效，R09）；过渡兼容纯字符串数组的 `v1..vN` 索引形态。
        """
        return _parse_key_pairs(self.runtime_ticket_keys, "RUNTIME_TICKET_KEYS")


def _parse_key_pairs(raw_value: str, label: str) -> list[tuple[str, str]]:
    """`{"kid": "secret"} | ["secret", ...]` → [(kid, secret)]（空值 = 未配置）.

    与 Agent 侧 `common.identity.ticket.parse_keys` 字节兼容；用于运行票据与服务级
    只读校准票据两套**独立**密钥（互不复用，独立轮换）。
    """
    if not raw_value:
        return []
    raw = raw_value.strip()
    # 容忍被外层单引号包裹的 JSON 值（不同 dotenv 实现差异）
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 必须是 JSON 对象或字符串数组") from exc
    pairs: list[tuple[str, str]] = []
    if isinstance(parsed, dict):
        pairs = [(str(kid), value) for kid, value in parsed.items()]
    elif isinstance(parsed, list) and all(isinstance(k, str) and k for k in parsed):
        pairs = [(f"v{index + 1}", secret) for index, secret in enumerate(parsed)]
    else:
        raise ValueError(f"{label} 必须是 JSON 对象或字符串数组")
    if not pairs or not all(kid and isinstance(sec, str) and sec for kid, sec in pairs):
        raise ValueError(f"{label} 必须是非空 kid/密钥映射")
    if len({kid for kid, _ in pairs}) != len(pairs):
        raise ValueError(f"{label} 的 kid 必须唯一")
    return pairs


def _normalize_pg_uri(uri: str) -> str:
    if uri.startswith("postgresql+"):
        return uri
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://") :]
    return uri


@lru_cache
def get_settings() -> Settings:
    return Settings()
