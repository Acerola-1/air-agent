"""需求洞察独立配置（不导入根 Config，不触发图/Embedding 预热）.

所有模型地址与密钥只来自受保护部署配置（环境变量/.env），浏览器不能传入。
未配置 Embedding 时作业保持暂停而不是退回未批准供应商；未配置辅助模型时
只允许确定性标准化/拆分，不发送原文。

配置项全部带版本语义：`embedding_profile` + `embedding_model` + 维度进入
不可变分析 profile；阈值只在新建 profile 时读取，运行中改环境变量不会悄悄
改变既有历史簇的判定口径。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InsightsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, alias="INSIGHTS_ENABLED")
    worker_enabled: bool = Field(default=False, alias="INSIGHTS_WORKER_ENABLED")
    worker_database_uri: str = Field(default="", alias="INSIGHTS_WORKER_DATABASE_URI")
    worker_poll_seconds: int = Field(
        default=60, ge=5, le=3600, alias="INSIGHTS_WORKER_POLL_SECONDS"
    )
    worker_id: str = Field(default="insights-worker-1", alias="INSIGHTS_WORKER_ID")

    embedding_base_url: str = Field(default="", alias="INSIGHTS_EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="", alias="INSIGHTS_EMBEDDING_API_KEY")
    embedding_model: str = Field(default="", alias="INSIGHTS_EMBEDDING_MODEL")
    embedding_profile: str = Field(default="embed-v1", alias="INSIGHTS_EMBEDDING_PROFILE")
    embedding_dimension: int = Field(default=0, ge=0, le=4096, alias="INSIGHTS_EMBEDDING_DIMENSION")
    embedding_batch_chars: int = Field(
        default=131_072, ge=1, le=1_000_000, alias="INSIGHTS_EMBEDDING_BATCH_CHARS"
    )

    llm_base_url: str = Field(default="", alias="INSIGHTS_LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="INSIGHTS_LLM_API_KEY")
    llm_model: str = Field(default="", alias="INSIGHTS_LLM_MODEL")
    llm_profile: str = Field(default="llm-v1", alias="INSIGHTS_LLM_PROFILE")
    llm_max_output_tokens: int = Field(
        default=1024, ge=64, le=8192, alias="INSIGHTS_LLM_MAX_OUTPUT_TOKENS"
    )
    allow_insecure_http: bool = Field(default=False, alias="INSIGHTS_ALLOW_INSECURE_HTTP")

    timeout_seconds: float = Field(
        default=20.0, gt=0, le=120, alias="INSIGHTS_MODEL_TIMEOUT_SECONDS"
    )
    max_attempts: int = Field(default=3, ge=1, le=3, alias="INSIGHTS_MAX_ATTEMPTS")

    similarity_threshold: float = Field(
        default=0.90, gt=0, le=1, alias="INSIGHTS_SIMILARITY_THRESHOLD"
    )
    margin_threshold: float = Field(default=0.03, ge=0, le=1, alias="INSIGHTS_MARGIN_THRESHOLD")
    review_low: float = Field(default=0.80, ge=0, le=1, alias="INSIGHTS_REVIEW_LOW")
    max_candidates: int = Field(default=10, ge=1, le=50, alias="INSIGHTS_MAX_CANDIDATES")
    max_clusters: int = Field(default=2000, ge=1, le=20_000, alias="INSIGHTS_MAX_CLUSTERS")
    max_intents: int = Field(default=5, ge=1, le=5, alias="INSIGHTS_MAX_INTENTS")
    max_input_chars: int = Field(default=4000, ge=1, le=8000, alias="INSIGHTS_MAX_INPUT_CHARS")
    max_context_chars: int = Field(
        default=8000, ge=0, le=16_000, alias="INSIGHTS_MAX_CONTEXT_CHARS"
    )
    context_messages: int = Field(default=2, ge=0, le=2, alias="INSIGHTS_CONTEXT_MESSAGES")

    batch_size: int = Field(default=100, ge=1, le=500, alias="INSIGHTS_BATCH_SIZE")
    max_sources_per_job: int = Field(
        default=500, ge=1, le=5000, alias="INSIGHTS_MAX_SOURCES_PER_JOB"
    )
    max_tokens_per_job: int = Field(
        default=200_000, ge=1000, le=10_000_000, alias="INSIGHTS_MAX_TOKENS_PER_JOB"
    )
    backfill_max_business_days: int = Field(
        default=30, ge=1, le=365, alias="INSIGHTS_BACKFILL_MAX_BUSINESS_DAYS"
    )
    delegation_max_hours: int = Field(
        default=12, ge=1, le=12, alias="INSIGHTS_DELEGATION_MAX_HOURS"
    )

    coverage_comparable_threshold: float = Field(
        default=0.90, gt=0, le=1, alias="INSIGHTS_COMPARABLE_COVERAGE"
    )
    calibration_min_precision: float = Field(
        default=0.98, gt=0, le=1, alias="INSIGHTS_CALIBRATION_MIN_PRECISION"
    )
    calibration_min_coverage: float = Field(
        default=0.60, gt=0, le=1, alias="INSIGHTS_CALIBRATION_MIN_COVERAGE"
    )
    calibration_min_auto: int = Field(
        default=50, ge=0, le=10_000, alias="INSIGHTS_CALIBRATION_MIN_AUTO"
    )
    calibration_max_false_merge: int = Field(
        default=0, ge=0, le=1000, alias="INSIGHTS_CALIBRATION_MAX_FALSE_MERGE"
    )

    default_analysis_version: str = Field(default="qdi-v1", alias="INSIGHTS_ANALYSIS_VERSION")
    default_profile_key: str = Field(default="v1", alias="INSIGHTS_PROFILE_KEY")
    normalizer_version: str = Field(
        default="qdi-normalizer-v1", alias="INSIGHTS_NORMALIZER_VERSION"
    )
    # 游标签名复用服务端内部凭据；浏览器只能得到签名后的游标。
    internal_api_token: str = Field(default="", alias="INTERNAL_API_TOKEN")

    def model_post_init(self, __context) -> None:  # noqa: ANN001 - pydantic hook
        if self.review_low > self.similarity_threshold:
            raise ValueError("INSIGHTS_REVIEW_LOW 不能高于相似度阈值")
        if self.embedding_dimension and not self.embedding_model:
            raise ValueError("配置 Embedding 维度时必须同时配置模型")
        if self.embedding_model and not self.embedding_dimension:
            raise ValueError("配置 Embedding 模型时必须同时配置维度")
        for label, url in (
            ("INSIGHTS_EMBEDDING_BASE_URL", self.embedding_base_url),
            ("INSIGHTS_LLM_BASE_URL", self.llm_base_url),
        ):
            if url:
                self._validate_endpoint(label, url)

    def _validate_endpoint(self, label: str, raw: str) -> None:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{label} 必须是 http(s) 地址")
        local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme == "http" and not local and not self.allow_insecure_http:
            raise ValueError(f"{label} 远程地址必须使用 https")

    @property
    def embedding_configured(self) -> bool:
        return bool(
            self.embedding_base_url
            and self.embedding_api_key
            and self.embedding_model
            and self.embedding_dimension
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def worker_database_configured(self) -> bool:
        return bool(self.worker_database_uri.strip())

    @property
    def internal_cursor_secret(self) -> str:
        return self.internal_api_token


@lru_cache
def get_insights_settings() -> InsightsSettings:
    return InsightsSettings()
