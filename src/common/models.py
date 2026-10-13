"""集中管理的模型注册表."""

from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from common.config import config
from common.config.logging_config import LoggingConfigurator
from common.identity.outbound import TenantOutboundAuth

LoggingConfigurator.configure()
logger.info("模型注册表初始化开始")


class ModelRegistry:
    """Agent 运行时使用的模型实例."""

    _main_llm_api_key = SecretStr(config.MAIN_LLM_API_KEY)

    # 主对话通道：本机 workbuddy2api 网关的 OpenAI 兼容 /v1/chat/completions。
    # hy3 为推理模型（reasoning_tokens 计入 max_tokens，额度需留足）。
    # 实测（2026-10-10）plain / tool_calls / SSE stream 三路径均可用。
    main_llm = ChatOpenAI(
        api_key=_main_llm_api_key,
        base_url=config.MAIN_LLM_BASE_URL,
        model=config.MAIN_LLM_MODEL,
        temperature=0.1,
        max_completion_tokens=config.MAIN_LLM_MAX_OUTPUT_TOKENS,
        timeout=120,
        # 重试由节点执行并再次验证授权；SDK 内部重发会绕过运行守卫。
        max_retries=0,
        http_async_client=httpx.AsyncClient(auth=TenantOutboundAuth()),
    )

    summary_llm = ChatOpenAI(
        api_key=SecretStr(config.SUMMARY_LLM_API_KEY),
        base_url=config.SUMMARY_LLM_BASE_URL,
        model=config.SUMMARY_LLM or config.MAIN_LLM_MODEL,
        temperature=0,
        max_completion_tokens=config.SUMMARY_MODEL_OUTPUT_TOKENS,
        timeout=config.DATA_PROCESSING_TIMEOUT_SECONDS,
        max_retries=0,
        tags=["nostream", "internal-summary"],
        http_async_client=httpx.AsyncClient(auth=TenantOutboundAuth()),
    )
