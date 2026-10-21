# ruff: noqa: D103, D107, T201
"""Controlled slow sources used by streaming acceptance probes.

These helpers exist only for tests/probes.  They never add sleeps to product
code: the product publisher has no artificial delay.  A probe blocks the fake
*source* (model/tool) at a barrier, verifies a public delta already reached the
consumer, then releases it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk


class ControlledChatModel:
    """Fake chat model that yields twelve fragments and blocks after one."""

    profile = None

    def __init__(
        self,
        fragments: list[str] | None = None,
        *,
        barrier_after: int = 1,
        answer: str | None = None,
    ) -> None:
        self.fragments = fragments or [f"第{i}段安全正文。" for i in range(1, 13)]
        self.barrier_after = barrier_after
        self.answer = answer or "".join(self.fragments)
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    def bind_tools(self, schemas: list[Any]) -> ControlledChatModel:
        """Planning binding is a no-op for the controlled model."""
        return self

    async def astream(
        self, messages: list[Any], config: dict[str, Any] | None = None
    ) -> AsyncIterator[AIMessageChunk]:
        """Yield fragments; block after ``barrier_after`` until released."""
        for index, text in enumerate(self.fragments):
            yield AIMessageChunk(content=text)
            if index == self.barrier_after:
                self.reached.set()
                await self.release.wait()
        self.finished = True

    async def ainvoke(
        self, messages: list[Any], config: dict[str, Any] | None = None
    ) -> AIMessage:
        """Return one deterministic response (planning/cleanup use)."""
        return AIMessage(content=self.answer)


class ScriptedPlanModel:
    """Scripted planning/answer model for the tracing graph probe."""

    profile = None
    _llm_type = "controlled-scripted"

    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = list(responses)
        self.bound_tools: dict[str, Any] = {}

    def with_retry(self, **kwargs: Any) -> ScriptedPlanModel:
        """Summarization middleware compatibility."""
        return self

    def with_config(self, **kwargs: Any) -> ScriptedPlanModel:
        """Runnable compatibility."""
        return self

    def bind_tools(self, tools: list[Any]) -> ScriptedPlanModel:
        """Record tool names, return self."""
        for tool in tools:
            name = getattr(tool, "name", None) or (
                tool.get("name") if isinstance(tool, dict) else None
            )
            if name:
                self.bound_tools[str(name)] = tool
        return self

    async def ainvoke(self, messages: list[Any], config: dict[str, Any] | None = None):
        """Consume the next scripted response."""
        if not self.responses:
            return AIMessage(content="")
        return self.responses.pop(0)

    async def astream(self, messages: list[Any], config: dict[str, Any] | None = None):
        """Stream the next scripted response as one chunk."""
        response = await self.ainvoke(messages, config)
        yield AIMessageChunk(content=str(response.content))
