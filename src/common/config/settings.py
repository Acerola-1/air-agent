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

import math
import os


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


def _get_env_float(key: str, default: float) -> float:
    """读取有限浮点配置，非法配置使用默认值."""
    try:
        value = float(os.getenv(key) or default)
        return value if math.isfinite(value) else default
    except ValueError:
        return default


class Config:
    """应用配置类."""

    # ==================== SQLite 配置(checkpointer 存储)====================
    # 单文件数据库,项目根 db/ 目录,gitignored
    SQLITE_PATH: str = _get_env_optional("SQLITE_PATH", "db/checkpoints.db")

    # SQLITE_PATH_PARENT 用于确保父目录存在(默认取父目录)
    @property
    def SQLITE_PATH_PARENT(self) -> str:
        """返回 SQLite 文件父目录."""
        from pathlib import Path

        return str(Path(self.SQLITE_PATH).parent) or "."

    # ==================== Redis 配置(预留)====================
    REDIS_HOST: str = _get_env_optional("REDIS_HOST", "localhost")
    REDIS_PORT: int = _get_env_int("REDIS_PORT", 6379)
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = _get_env_int("REDIS_DB", 0)

    # ==================== MCP 连接配置 ====================
    # 旧环境变量：仅为兼容历史配置保留属性，**不再是任何授权/发现来源**。
    # 生产连接由 `AGENT_MCP_CONNECTIONS_JSON` 或
    # `AGENT_MCP_CONNECTIONS_FILE` 的受保护部署配置提供。
    MCP_SERVER_URL: str = _get_env_optional("MCP_SERVER_URL", "")
    MCP_SERVER_NAME: str = _get_env_optional("MCP_SERVER_NAME", "ipp")
    AGENT_MCP_CONNECTIONS_JSON: str = _get_env_optional(
        "AGENT_MCP_CONNECTIONS_JSON", ""
    )
    AGENT_MCP_CONNECTIONS_FILE: str = _get_env_optional(
        "AGENT_MCP_CONNECTIONS_FILE", ""
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

    # ==================== 主 LLM 通道配置(凭据必需)====================
    # 默认走本机 workbuddy2api 网关(OpenAI 兼容 /v1/chat/completions)。
    # 192.168.64.1 是 Apple Container 网桥上的宿主机地址,容器内可直接访问。
    MAIN_LLM_API_KEY: str = _get_env_required(
        "MAIN_LLM_API_KEY",
        "主 LLM API 密钥,用于 OpenAI 兼容 LLM 推理。",
    )
    MAIN_LLM_BASE_URL: str = _get_env_optional(
        "MAIN_LLM_BASE_URL", "http://192.168.64.1:8787/v1"
    )
    MAIN_LLM_MODEL: str = _get_env_optional("MAIN_LLM_MODEL", "hy3")
    MODEL_TOKENIZER_ENCODING: str = _get_env_optional("MODEL_TOKENIZER_ENCODING", "")
    SUMMARY_TOKENIZER_ENCODING: str = _get_env_optional(
        "SUMMARY_TOKENIZER_ENCODING", ""
    )
    MODEL_TOKEN_ESTIMATE_MARGIN: float = _get_env_float(
        "MODEL_TOKEN_ESTIMATE_MARGIN", 1.25
    )

    # ==================== DuckDuckGo 配置(可选)====================
    DDGS_PROXY: str | None = os.getenv("DDGS_PROXY")
    DDGS_REGION: str = _get_env_optional("DDGS_REGION", "cn-zh")
    DDGS_TIMEOUT: int = _get_env_int("DDGS_TIMEOUT", 15)
    DDGS_BACKEND: str = _get_env_optional("DDGS_BACKEND", "bing,yandex")

    # ==================== 运行时性能开关 ====================
    MCP_RETRY_BACKOFF_SECONDS: int = _get_env_int("MCP_RETRY_BACKOFF_SECONDS", 30)
    PERMISSION_PROFILE_CACHE_TTL_SECONDS: int = _get_env_int(
        "PERMISSION_PROFILE_CACHE_TTL_SECONDS", 300
    )
    PERMISSION_REGION_CACHE_TTL_SECONDS: int = _get_env_int(
        "PERMISSION_REGION_CACHE_TTL_SECONDS", 3600
    )

    # ==================== 模型上下文与工具治理 ====================
    MODEL_MAX_CONTEXT_TOKENS: int = _get_env_int("MODEL_MAX_CONTEXT_TOKENS", 192000)
    # 常规摘要水位 = 模型窗口 × 比例。普通部署只需理解窗口和比例，
    # 不再维护另一组绝对 token 触发值或应用输入上限。
    CONVERSATION_SUMMARY_TRIGGER_RATIO: float = _get_env_float(
        "CONVERSATION_SUMMARY_TRIGGER_RATIO", 0.8
    )
    # 明确的服务商输入上限（0 表示未单独声明，不构造新的固定上限）。
    MODEL_PROVIDER_INPUT_LIMIT_TOKENS: int = _get_env_int(
        "MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 0
    )
    MAX_INPUT_CHARS: int = _get_env_int("MAX_INPUT_CHARS", 8000)
    TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS: int = _get_env_int(
        "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 20000
    )
    SUMMARY_LLM: str | None = os.getenv("SUMMARY_LLM") or None
    MODEL_TOOL_DESCRIPTION_CHARS: int = _get_env_int(
        "MODEL_TOOL_DESCRIPTION_CHARS", 256
    )
    MODEL_TOOL_PARAMETER_DESCRIPTION_CHARS: int = _get_env_int(
        "MODEL_TOOL_PARAMETER_DESCRIPTION_CHARS", 120
    )
    MODEL_TOOL_SCHEMA_BUDGET_PERCENT: int = _get_env_int(
        "MODEL_TOOL_SCHEMA_BUDGET_PERCENT", 40
    )
    MAIN_LLM_MAX_OUTPUT_TOKENS: int = _get_env_int("MAIN_LLM_MAX_OUTPUT_TOKENS", 8192)
    MODEL_CONTEXT_SAFETY_TOKENS: int = _get_env_int("MODEL_CONTEXT_SAFETY_TOKENS", 4096)
    SUMMARY_LLM_BASE_URL: str = _get_env_optional(
        "SUMMARY_LLM_BASE_URL", MAIN_LLM_BASE_URL
    )
    SUMMARY_LLM_API_KEY: str = _get_env_optional(
        "SUMMARY_LLM_API_KEY", MAIN_LLM_API_KEY
    )
    SUMMARY_MODEL_CONTEXT_TOKENS: int = _get_env_int(
        "SUMMARY_MODEL_CONTEXT_TOKENS", MODEL_MAX_CONTEXT_TOKENS
    )
    SUMMARY_MODEL_OUTPUT_TOKENS: int = _get_env_int("SUMMARY_MODEL_OUTPUT_TOKENS", 4096)
    SUMMARY_MODEL_SAFETY_TOKENS: int = _get_env_int("SUMMARY_MODEL_SAFETY_TOKENS", 2048)
    SUMMARY_MODEL_PROVIDER_INPUT_LIMIT_TOKENS: int = _get_env_int(
        "SUMMARY_MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 0
    )
    DATA_PROCESSING_TIMEOUT_SECONDS: int = _get_env_int(
        "DATA_PROCESSING_TIMEOUT_SECONDS", 90
    )
    SUMMARY_MAX_BATCHES: int = _get_env_int("SUMMARY_MAX_BATCHES", 256)
    QUICKJS_TIMEOUT_SECONDS: int = _get_env_int("QUICKJS_TIMEOUT_SECONDS", 5)
    QUICKJS_MEMORY_MIB: int = _get_env_int("QUICKJS_MEMORY_MIB", 64)
    QUICKJS_MAX_INPUT_BYTES: int = _get_env_int(
        "QUICKJS_MAX_INPUT_BYTES", 16 * 1024 * 1024
    )
    QUICKJS_MAX_RESULT_BYTES: int = _get_env_int("QUICKJS_MAX_RESULT_BYTES", 128 * 1024)
    QUICKJS_MAX_CORRECTIONS: int = _get_env_int("QUICKJS_MAX_CORRECTIONS", 1)
    TOOL_ARTIFACT_RETENTION_DAYS: int = _get_env_int("TOOL_ARTIFACT_RETENTION_DAYS", 30)
    FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS: int = _get_env_int(
        "FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS", 45
    )
    CONVERSATION_SUMMARY_KEEP_TURNS: int = _get_env_int(
        "CONVERSATION_SUMMARY_KEEP_TURNS", 3
    )
    AGENT_CAPABILITY_SELECTION_ENABLED: bool = _get_env_bool(
        "AGENT_CAPABILITY_SELECTION_ENABLED", False
    )


config = Config()

# 旧版固定阈值已停用：只报告键名，不打印值，也不在任何计算路径继续读取。
_DEPRECATED_CONTEXT_KEYS = (
    "MODEL_CONTEXT_BUDGET_TOKENS",
    "CONVERSATION_SUMMARY_THRESHOLD_TOKENS",
)


def deprecated_context_setting_keys() -> tuple[str, ...]:
    """返回仍出现在环境中的旧上下文配置键名（无秘密、无取值）."""
    return tuple(key for key in _DEPRECATED_CONTEXT_KEYS if os.getenv(key))


def warn_deprecated_context_settings() -> tuple[str, ...]:
    """对旧配置残留给出明确、无秘密的弃用提示."""
    present = deprecated_context_setting_keys()
    if present:
        from loguru import logger

        logger.warning(
            "检测到已停用的上下文配置键：{}；请改用 MODEL_MAX_CONTEXT_TOKENS 与 "
            "CONVERSATION_SUMMARY_TRIGGER_RATIO，旧值不再参与请求或工具配额。",
            ", ".join(present),
        )
    return present


warn_deprecated_context_settings()
