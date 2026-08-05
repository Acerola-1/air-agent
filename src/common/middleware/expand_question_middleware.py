"""问题扩展后置中间件.

在 DeepAgent aafter_agent 钩子中执行，流式生成推荐追问并推送前端。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

try:
    from typing import override
except ImportError:
    from typing_extensions import override

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.runtime import Runtime
from loguru import logger

from common.config import config
from common.context import get_message_content
from common.models import ModelRegistry
from common.prompts import expand_question_prompt


class ExpandQuestionMiddleware(AgentMiddleware):
    """问题扩展后置中间件，在 DeepAgent 完成回答后生成推荐追问."""

    tools: Sequence[BaseTool] = ()

    def _enabled(self) -> bool:
        """根据运行时 configurable 和环境配置判断是否启用追问."""
        try:
            runtime_config = get_config() or {}
        except RuntimeError:
            runtime_config = {}
        configurable = runtime_config.get("configurable", {})
        raw_value = configurable.get(
            "expand_question_enabled",
            configurable.get("enable_expand_question", None),
        )
        if raw_value is None:
            return bool(config.EXPAND_QUESTION_ENABLED)
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw_value)

    @override
    async def aafter_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """在 DeepAgent 完成回答后执行问题扩展.

        从 state.messages 中提取问题和答案，调用 LLM 生成推荐追问，
        通过 runtime.stream_writer 推送给前端。
        """
        writer = runtime.stream_writer
        messages = state.get("messages", [])

        # 从 state.messages 中提取最后一条 HumanMessage 和最后一条纯文本 AIMessage
        question = ""
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                question = get_message_content(message)
                break
        logger.debug(f"问题: {question}")

        # 逆序遍历，找到最后一条纯文本 AIMessage（无 tool_calls）作为答案
        answer = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                if getattr(msg, "tool_calls", None):
                    continue
                content = msg.content
                if isinstance(content, str) and content.strip():
                    answer = content
                    break

        if not answer:
            answer = str(messages[-1].content) if messages else ""

        # 清理思考标签
        answer_clean = re.sub(r"<tool_call>.*?ັ", "", answer, flags=re.DOTALL)

        if not self._enabled():
            logger.debug("推荐追问生成已禁用")
            return None

        await self._generate_and_push_questions(
            question=str(question),
            answer=answer_clean,
            writer=writer,
        )
        return None

    async def _generate_and_push_questions(
        self,
        *,
        question: str,
        answer: str,
        writer: Any,
    ) -> None:
        """在当前 graph 生命周期内生成推荐追问并推送给前端."""
        llm = ModelRegistry.deepseek_v4_flash
        prompt = expand_question_prompt(question, answer)

        try:
            full_content = ""
            async with asyncio.timeout(config.EXPAND_QUESTION_TIMEOUT_SECONDS):
                async for chunk in llm.astream([{"role": "user", "content": prompt}]):
                    content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if isinstance(content, list):
                        content = str(content)
                    full_content += content

            if full_content.strip() == "无":
                expanded_questions = []
            else:
                expanded_questions = [
                    q.strip() for q in full_content.split("\n") if q.strip()
                ]

            writer(
                {
                    "node": "expand_question",
                    "type": "expanded_questions",
                    "message": expanded_questions,
                }
            )
        except Exception as exc:
            logger.warning("推荐追问生成失败，已跳过: {}", exc)
