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

    _opencode_go_api_key = SecretStr(config.OPENCODE_GO_DEEPSEEK_V4_FLASH_API_KEY)

    # OpenAI 兼容 /v1/chat/completions 通道（OpenCode Go）。
    # 实测（2026-07-31）较 minimax-m3 首 token 延迟低约一半、生成速率快约 68%。
    # 注意：该通道不支持强制 tool_choice / json_schema（稳定 400），
    # 结构化抽取场景需用 bind_tools(auto) + Pydantic model_validate 校验。
    deepseek_v4_flash = ChatOpenAI(
        api_key=_opencode_go_api_key,
        base_url=config.OPENCODE_GO_DEEPSEEK_V4_FLASH_BASE_URL,
        model=config.OPENCODE_GO_DEEPSEEK_V4_FLASH_MODEL,
        temperature=0.1,
        max_tokens=8192,
        timeout=120,
        max_retries=1,
    )
