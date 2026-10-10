"""集中管理的模型注册表."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from common.config import config
from common.config.logging_config import LoggingConfigurator

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
        max_tokens=8192,
        timeout=120,
        max_retries=1,
    )
