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


# 模型调用用途（与 observability/invocation_config 的 role 对齐）：
# - planning: 带工具 schema 的非流式工具规划（供应商兼容模式）
# - answer:   无工具最终正文，显式 astream，使用真实流式
# - cleanup:  数据分析最终整理，无工具流式
# - subagent: DeepAgents 专员，内部输出不进入正文
# - summary:  内部摘要，永不公开
MODEL_ROLES = ("planning", "answer", "cleanup", "subagent", "summary")


class ModelRegistry:
    """Agent 运行时使用的模型实例."""

    _main_llm_api_key = SecretStr(config.MAIN_LLM_API_KEY)

    # 主通道与生成预留由部署配置提供；推理输出计入生成额度时需一并预留。
    main_llm = ChatOpenAI(
        api_key=_main_llm_api_key,
        base_url=config.MAIN_LLM_BASE_URL,
        model=config.MAIN_LLM_MODEL,
        temperature=0.1,
        max_completion_tokens=config.MAIN_LLM_MAX_OUTPUT_TOKENS,
        timeout=120,
        # 重试由节点执行并再次验证授权；SDK 内部重发会绕过运行守卫。
        max_retries=0,
        # 当前网关在“长系统提示 + 多工具”流式调用中会丢失 tool_call 参数
        # （G1 实测 freeze_tracing_request 收到空 args）。带工具时改用非流式
        # 请求；纯文本回答仍可流式。
        disable_streaming="tool_calling",
        http_async_client=httpx.AsyncClient(auth=TenantOutboundAuth()),
        http_client=httpx.Client(auth=TenantOutboundAuth()),
    )

    @classmethod
    def for_role(cls, role: str):
        """Return the model instance for a call role.

        The shared instance is intentional: ``disable_streaming="tool_calling"``
        only disables streaming when tools are bound, so the no-tool answer and
        cleanup roles still stream through an explicit ``astream`` call.
        """
        if role not in MODEL_ROLES:
            raise ValueError(f"unknown model role: {role}")
        if role == "summary":
            return cls.summary_llm
        return cls.main_llm

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
        http_client=httpx.Client(auth=TenantOutboundAuth()),
    )
