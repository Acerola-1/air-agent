"""最终输出清理中间件."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, override

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from loguru import logger

from common.config import config
from common.context import get_message_content
from common.models import ModelRegistry

FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE = "正在整理最终答案"
FINAL_OUTPUT_CLEANUP_NODE = "final_output_cleanup"


class _StreamingModel(Protocol):
    """支持流式输出的 finalizer 模型协议."""

    def astream(self, messages: list[dict[str, str]]) -> AsyncIterator[Any]:
        """流式生成模型输出."""
        ...


def build_final_output_cleanup_prompt(answer: str) -> str:
    """构造只清理过程信息的 finalizer 提示词."""
    return f"""
你是最终答案清理器。你的任务只是在下方"待清理正文"中删除过程性信息和内部实现信息，除此之外必须原样保留正文。

必须删除的内容包括：
- 工具调用计划、执行进度、失败重试、下一步动作、内部分析过程。
- skill、工具、函数、参数、JSON、SQL、路径、ID、middleware、LangGraph、MCP、ToolMessage、AIMessage、字段名等内部实现信息。
- "我需要/我正在/接下来/已完成/让我/根据技能/调用某工具"等过程化自述。
- 工具错误消息、状态码（如 502、500）、超时提示（如 timeout）、失败原因（如 API 返回错误）等不可原样输出或转述。

必须保留的内容包括：
- 原有业务结论、依据、关键数据、风险提示和建议。
- 原有 Markdown 标题、列表、表格、段落顺序、日期、数值、单位、排序和专有名词。
- 已生成的正文结构和表达风格。
- 权限修正说明（如"因数据权限限制，已为您调整..."等），这些说明是用户必须知晓的重要信息，不可删除。

禁止行为：
- 不得重构、扩写、压缩、总结、重排、翻译、美化或改变业务正文。
- 不得补充新事实、新判断、新建议或新数据。
- 不得解释你做了哪些清理。
- 不得删除权限修正说明。

如果待清理正文已经干净，必须原样输出。
只输出清理后的最终正文，不要输出任何说明。

待清理正文：
{answer}
""".strip()


class FinalOutputCleanupMiddleware(AgentMiddleware):
    """在业务 Agent 完成后流式推送清理后的最终答案."""

    tools: Sequence[BaseTool] = ()

    def __init__(
        self,
        *,
        model: _StreamingModel | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """初始化最终输出清理中间件.

        Args:
            model: 可选的 finalizer 模型，测试或特殊部署可注入。
            timeout_seconds: 可选超时时间，默认读取配置。
        """
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def _finalizer_model(self) -> _StreamingModel:
        return self._model or ModelRegistry.deepseek_v4_flash

    @property
    def _timeout(self) -> int:
        return self._timeout_seconds or config.FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS

    def _extract_last_visible_answer(self, state: AgentState[Any]) -> str:
        """从消息列表中提取最后一条可见业务答案."""
        messages = state.get("messages", [])
        if not isinstance(messages, list):
            return ""

        for message in reversed(messages):
            if not isinstance(message, AIMessage):
                continue
            if getattr(message, "tool_calls", None):
                continue
            content = get_message_content(message).strip()
            if content:
                return content
        return ""

    def _push_progress(self, writer: Any) -> None:
        """推送最终整理开始事件."""
        writer(
            {
                "node": FINAL_OUTPUT_CLEANUP_NODE,
                "type": "progress",
                "message": FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
            }
        )

    async def _stream_cleaned_answer(self, *, answer: str, writer: Any) -> str:
        """调用 finalizer 并流式推送清理后的答案."""
        full_content = ""
        prompt = build_final_output_cleanup_prompt(answer)

        async with asyncio.timeout(self._timeout):
            async for chunk in self._finalizer_model.astream(
                [{"role": "user", "content": prompt}]
            ):
                content = getattr(chunk, "content", chunk)
                if isinstance(content, list):
                    content = "".join(str(item) for item in content)
                elif content is None:
                    content = ""
                else:
                    content = str(content)

                if not content:
                    continue

                full_content += content
                writer(
                    {
                        "node": FINAL_OUTPUT_CLEANUP_NODE,
                        "type": "final_output_delta",
                        "message": content,
                    }
                )

        cleaned = full_content.strip()
        if cleaned:
            writer(
                {
                    "node": FINAL_OUTPUT_CLEANUP_NODE,
                    "type": "final_output_done",
                    "message": cleaned,
                }
            )
        return cleaned

    @override
    async def aafter_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """在 Agent 完成后清理最终可见答案."""
        writer = getattr(runtime, "stream_writer", None)
        if not callable(writer):
            logger.debug("最终答案清理跳过：stream_writer 不可用")
            return None

        answer = self._extract_last_visible_answer(state)
        if not answer:
            logger.debug("最终答案清理跳过：未找到可见业务答案")
            return None

        self._push_progress(writer)

        try:
            cleaned = await self._stream_cleaned_answer(answer=answer, writer=writer)
        except Exception as exc:
            logger.warning("最终答案清理失败，已退化使用原始 Messages: {}", exc)
            return None

        if not cleaned:
            logger.warning("最终答案清理返回空结果，已退化使用原始 Messages")
        return None
