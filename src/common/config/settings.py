"""应用配置管理模块.

从环境变量加载配置。敏感凭据(如密码、API密钥)必须通过环境变量设置,
不允许使用硬编码默认值。

本地化说明:
- 2026-08-05 重构: 移除 PostgreSQL/MySQL 全部配置,checkpointer 切到 SQLite
  (单文件存储, 项目根 db/ 目录, gitignore 忽略 db/*.db)。
- 保留字段: SQLite(checkpointer) / MCP(业务数据) / 公网 LLM(主推理) / 外部搜索。
- 知识库(Milvus / Ollama)与 text2sql 工具已下线,对应配置一并清理。
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus


def _get_env_required(key: str, description: str) -> str:
    """获取必需的环境变量."""
    value = os.getenv(key)
    if value is None or value == "":
        raise OSError(f"必需的环境变量 '{key}' 未设置。{description}")
    return value


def _get_env_optional(key: str, default: str) -> str:
    """获取可选的环境变量,带默认值."""
    return os.getenv(key, default) if os.getenv(key) else default


def _get_env_int(key: str, default: int) -> int:
    """获取整数类型的环境变量."""
    value = os.getenv(key)
    if value:
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _get_env_bool(key: str, default: bool) -> bool:
    """获取布尔类型的环境变量."""
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """应用配置类."""

    # ==================== SQLite 配置(checkpointer 存储)====================
    # 单文件数据库,项目根 db/ 目录,gitignored
    SQLITE_PATH: str = _get_env_optional("SQLITE_PATH", "db/checkpoints.db")
    # SQLITE_PATH_PARENT 用于确保父目录存在(默认取父目录)
    @property
    def SQLITE_PATH_PARENT(self) -> str:
        from pathlib import Path
        return str(Path(self.SQLITE_PATH).parent) or "."

    # ==================== Redis 配置(预留)====================
    REDIS_HOST: str = _get_env_optional("REDIS_HOST", "localhost")
    REDIS_PORT: int = _get_env_int("REDIS_PORT", 6379)
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = _get_env_int("REDIS_DB", 0)

    # ==================== MCP 服务器配置(凭据必需)====================
    MCP_SERVER_URL: str = _get_env_required(
        "MCP_SERVER_URL",
        "IPP Air 服务的 MCP 服务器 URL。",
    )
    MCP_SERVER_URL2: str = _get_env_required(
        "MCP_SERVER_URL2",
        "数据中心统计服务的 MCP 服务器 URL。",
    )

    # ==================== 元数据 API 配置 ====================
    METADATA_API_URL: str = _get_env_optional(
        "METADATA_API_URL",
        "http://localhost:8090/product/datacenter/api/v5/ipp-chat/sql/correction",
    )

    # ==================== SiliconFlow API 配置(凭据必需)====================
    SILICONFLOW_API_KEY: str = _get_env_required(
        "SILICONFLOW_API_KEY",
        "SiliconFlow API 密钥,用于 LLM 推理。",
    )
    SILICONFLOW_BASE_URL: str = _get_env_optional(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
    )
    SILICONFLOW_EMBEDDING_MODEL: str = _get_env_optional(
        "SILICONFLOW_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"
    )

    # ==================== Serper API 配置(凭据必需)====================
    SERPER_API_KEY: str = _get_env_required(
        "SERPER_API_KEY",
        "Serper API 密钥,用于网络搜索功能。",
    )

    # ==================== OpenCode Go DeepSeek V4 Flash 配置(凭据必需)====================
    OPENCODE_GO_DEEPSEEK_V4_FLASH_API_KEY: str = _get_env_required(
        "OPENCODE_GO_DEEPSEEK_V4_FLASH_API_KEY",
        "OpenCode Go DeepSeek V4 Flash API 密钥,用于 OpenAI 兼容 LLM 推理。",
    )
    OPENCODE_GO_DEEPSEEK_V4_FLASH_BASE_URL: str = _get_env_optional(
        "OPENCODE_GO_DEEPSEEK_V4_FLASH_BASE_URL", "https://opencode.ai/zen/go/v1"
    )
    OPENCODE_GO_DEEPSEEK_V4_FLASH_MODEL: str = _get_env_optional(
        "OPENCODE_GO_DEEPSEEK_V4_FLASH_MODEL", "deepseek-v4-flash"
    )

    # ==================== DuckDuckGo 配置(可选)====================
    DDGS_PROXY: str | None = os.getenv("DDGS_PROXY")
    DDGS_REGION: str = _get_env_optional("DDGS_REGION", "cn-zh")
    DDGS_TIMEOUT: int = _get_env_int("DDGS_TIMEOUT", 15)
    DDGS_BACKEND: str = _get_env_optional("DDGS_BACKEND", "bing,yandex")

    # ==================== 运行时性能开关 ====================
    MCP_RETRY_BACKOFF_SECONDS: int = _get_env_int("MCP_RETRY_BACKOFF_SECONDS", 30)
    EXPAND_QUESTION_ENABLED: bool = _get_env_bool("EXPAND_QUESTION_ENABLED", True)
    EXPAND_QUESTION_TIMEOUT_SECONDS: int = _get_env_int(
        "EXPAND_QUESTION_TIMEOUT_SECONDS", 20
    )
    PERMISSION_PROFILE_CACHE_TTL_SECONDS: int = _get_env_int(
        "PERMISSION_PROFILE_CACHE_TTL_SECONDS", 300
    )
    PERMISSION_REGION_CACHE_TTL_SECONDS: int = _get_env_int(
        "PERMISSION_REGION_CACHE_TTL_SECONDS", 3600
    )
    FINAL_OUTPUT_LLM_CLEANUP_FALLBACK: bool = _get_env_bool(
        "FINAL_OUTPUT_LLM_CLEANUP_FALLBACK", False
    )
    FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS: int = _get_env_int(
        "FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS", 20
    )


config = Config()
