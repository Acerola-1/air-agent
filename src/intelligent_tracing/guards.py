"""Tracing-specific guards for summarization and tool boundaries."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelResponse,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from common.identity.runtime import require_identity, revalidate
from common.observability.deepagents_adapter import ObservedSummarizationMiddleware
from intelligent_tracing.policy import BudgetPolicy


class GuardedSummarizationMiddleware(ObservedSummarizationMiddleware):
    """Revalidate trusted identity before every summary-model call."""

    @property
    def name(self) -> str:
        """Replace the SDK summarization slot by its public name."""
        return "SummarizationMiddleware"

    def _current_config(self) -> dict[str, Any]:
        from langgraph.config import get_config

        return dict(get_config() or {})

    # The tracing graph is invoked asynchronously by the platform; the sync
    # branch is intentionally not used for guarded production runs.
    async def _acreate_summary(self, messages_to_summarize):
        identity = require_identity(self._current_config())
        await revalidate(identity)
        return await super()._acreate_summary(messages_to_summarize)


def guarded_summarization(model: Any, backend: Any) -> GuardedSummarizationMiddleware:
    """Build the guarded middleware with the SDK's adaptive defaults."""
    from deepagents.middleware.summarization import (
        DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        compute_summarization_defaults,
    )

    defaults = compute_summarization_defaults(model)
    return GuardedSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=defaults["trigger"],
        keep=defaults["keep"],
        summary_prompt=DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        truncate_args_settings=defaults["truncate_args_settings"],
    )


class NoFilesystemToolsMiddleware(FilesystemMiddleware):
    """Filesystem middleware with an empty, non-dispatchable tool set."""

    def __init__(self, *, backend: Any = None) -> None:
        """Construct the SDK middleware then remove every file/execute tool."""
        super().__init__(backend=backend, tools=["read_file"])
        self._enabled_tools = frozenset()
        self.tools = []


class TracingBudgetMiddleware(AgentMiddleware):
    """Bound lead model calls and return a validated partial-result state."""

    def __init__(self, budget: BudgetPolicy) -> None:
        """Bind the frozen budget limits and start the wall clock."""
        self._budget = budget
        self._calls = 0
        self._task_calls = 0
        self._started = time.monotonic()
        self._task_slots = asyncio.Semaphore(max(1, budget.max_concurrency))

    def _timed_out(self) -> bool:
        return time.monotonic() - self._started > self._budget.total_timeout_seconds

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Count model calls; on exhaustion stop the loop without retrying."""
        self._calls += 1
        remaining = self._remaining()
        if remaining <= 0 or self._calls > self._budget.max_model_calls:
            return self._stop_model()
        try:
            async with asyncio.timeout(remaining):
                return await handler(request)
        except TimeoutError:
            return self._stop_model()

    def _remaining(self) -> float:
        return self._budget.total_timeout_seconds - (time.monotonic() - self._started)

    @staticmethod
    def _stop_model() -> ExtendedModelResponse:
        return ExtendedModelResponse(
            model_response=ModelResponse(
                result=[
                    AIMessage(
                        content="运行预算或总超时已耗尽，停止追加调查。",
                        additional_kwargs={"ui_visible": False},
                    )
                ]
            ),
            command=Command(update={"budget_exhausted": True}),
        )

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Bound task count/concurrency, total time and transient retries."""
        call = getattr(request, "tool_call", {}) or {}
        name = str(call.get("name") or "")
        tool_call_id = str(call.get("id") or "")
        if self._timed_out():
            return self._stop_tool(tool_call_id, name, "总超时已耗尽")
        if name == "task":
            self._task_calls += 1
            if self._task_calls > self._budget.max_subagent_tasks:
                return self._stop_tool(tool_call_id, name, "task 数已达预算上限")
            async with self._task_slots:
                return await handler(request)
        attempts = 0
        while True:
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001 - classified below
                attempts += 1
                if attempts > self._budget.max_tool_retries or not _is_transient(exc):
                    raise

    @staticmethod
    def _stop_tool(tool_call_id: str, name: str, reason: str) -> Command:
        return Command(
            update={
                "budget_exhausted": True,
                "messages": [
                    ToolMessage(
                        content=f"{reason}，停止追加调查。",
                        tool_call_id=tool_call_id or "budget",
                        name=name or "budget",
                    )
                ],
            }
        )


def _is_transient(exc: BaseException) -> bool:
    """Retry only transport-like failures, never auth/validation."""
    text = f"{type(exc).__name__} {exc}".lower()
    if any(
        word in text
        for word in (
            "authorization",
            "permission",
            "validation",
            "forbidden",
            "401",
            "403",
        )
    ):
        return False
    return any(
        word in text
        for word in ("timeout", "timed out", "connection", "temporarily", "503")
    )


class ToolBoundaryMiddleware:
    """Marker class kept for explicit wiring documentation."""

    # The actual boundary is enforced by TenantRuntimeGuardMiddleware(
    # inject_mcp_tools=False) plus FilesystemMiddleware(tools=[]).
