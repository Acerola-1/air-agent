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
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from common.identity.runtime import require_identity, revalidate
from common.model_capacity import ModelCapacity, capacity_for_model
from common.observability.deepagents_adapter import ObservedSummarizationMiddleware
from intelligent_tracing.policy import BudgetPolicy


class GuardedSummarizationMiddleware(ObservedSummarizationMiddleware):
    """按部署容量约束完整请求，摘要发送前复查可信身份."""

    def __init__(self, *, capacity: ModelCapacity, **kwargs: Any) -> None:
        """将容量绑定到每次SDK发送及恢复检查."""
        self.capacity = capacity
        super().__init__(**kwargs)

    def _input_budget(self, request: Any) -> int:
        return self.capacity.input_limit_tokens

    def _get_profile_limits(self) -> int:
        return self.capacity.window_tokens

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


def guarded_summarization(
    model: Any,
    backend: Any,
    *,
    capacity: ModelCapacity | None = None,
    role: str = "main",
) -> GuardedSummarizationMiddleware:
    """为当前模型绑定触发线、有效输入边界及近期原文保留预算."""
    from deepagents.middleware.summarization import (
        DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
    )

    from common.model_budget import request_metrics

    resolved = capacity or capacity_for_model(model, role=role)

    def count_tokens(messages: Any, *, tools: Any = ()) -> int:
        return request_metrics(messages, tools or (), capacity=resolved)["input_tokens"]

    keep = ("tokens", max(1, resolved.window_tokens // 10))
    return GuardedSummarizationMiddleware(
        capacity=resolved,
        model=model,
        backend=backend,
        trigger=("tokens", resolved.trigger_tokens),
        keep=keep,
        token_counter=count_tokens,
        summary_prompt=DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        truncate_args_settings={
            "trigger": ("tokens", resolved.trigger_tokens),
            "keep": keep,
        },
    )


class NoFilesystemToolsMiddleware(FilesystemMiddleware):
    """Filesystem middleware with an empty, non-dispatchable tool set."""

    def __init__(self, *, backend: Any = None) -> None:
        """Construct the SDK middleware then remove every file/execute tool."""
        super().__init__(backend=backend, tools=["read_file"])
        self._enabled_tools = frozenset()
        self.tools = []


class InputLimitMiddleware(AgentMiddleware):
    """Reject an oversized user turn before any lead model or tool dispatch."""

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Return a controlled message instead of calling the provider."""
        from common.context import get_message_content
        from common.input_limits import (
            count_user_characters,
            current_input_limit,
            input_overflow_message,
        )

        question = ""
        for message in reversed(getattr(request, "messages", []) or []):
            if getattr(message, "type", "") == "human":
                question = get_message_content(message)
                break
        limit = current_input_limit()
        count = count_user_characters(question)
        if count <= limit.max_chars:
            return await handler(request)
        return ExtendedModelResponse(
            model_response=ModelResponse(
                result=[
                    AIMessage(
                        content=input_overflow_message(count, limit.max_chars),
                        additional_kwargs={
                            "ui_visible": True,
                            "input_overflow": True,
                        },
                    )
                ]
            ),
            command=Command(update={"input_overflow": True}),
        )


class _RunBudgetState:
    """Per-run counters; a graph instance is shared by concurrent runs."""

    __slots__ = ("calls", "task_calls", "started", "slots")

    def __init__(self, max_concurrency: int) -> None:
        self.calls = 0
        self.task_calls = 0
        self.started = time.monotonic()
        self.slots = asyncio.Semaphore(max(1, max_concurrency))


class TracingBudgetMiddleware(AgentMiddleware):
    """Bound lead model calls and return a validated partial-result state."""

    def __init__(self, budget: BudgetPolicy) -> None:
        """Bind the frozen budget limits; clocks start per run."""
        self._budget = budget
        self._runs: dict[str, _RunBudgetState] = {}

    @staticmethod
    def _current_config() -> dict[str, Any]:
        try:
            from langgraph.config import get_config

            return dict(get_config() or {})
        except Exception:  # pragma: no cover - offline direct calls
            return {}

    @staticmethod
    def _run_key(config: dict[str, Any] | None = None) -> str:
        cfg = dict(config or {})
        configurable = cfg.get("configurable") or {}
        metadata = cfg.get("metadata") or {}
        return str(
            configurable.get("run_id")
            or metadata.get("run_id")
            or configurable.get("thread_id")
            or metadata.get("thread_id")
            or "default"
        )

    def _state(self) -> _RunBudgetState:
        key = self._run_key(self._current_config())
        state = self._runs.get(key)
        if state is None:
            state = _RunBudgetState(self._budget.max_concurrency)
            self._runs[key] = state
        return state

    async def abefore_agent(self, state: Any, runtime: Any = None) -> None:
        """Reset budget counters at the start of every run."""
        config = getattr(runtime, "config", None)
        key = self._run_key(config if isinstance(config, dict) else None)
        self._runs[key] = _RunBudgetState(self._budget.max_concurrency)
        return None

    async def aafter_agent(self, state: Any, runtime: Any = None) -> None:
        """Drop the finished run's counters; a later run starts fresh."""
        config = getattr(runtime, "config", None)
        key = self._run_key(config if isinstance(config, dict) else None)
        self._runs.pop(key, None)
        return None

    def _timed_out(self, state: _RunBudgetState) -> bool:
        return time.monotonic() - state.started > self._budget.total_timeout_seconds

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Count model calls; on exhaustion stop the loop without retrying."""
        state = self._state()
        state.calls += 1
        remaining = self._budget.total_timeout_seconds - (
            time.monotonic() - state.started
        )
        if remaining <= 0 or state.calls > self._budget.max_model_calls:
            return self._stop_model()
        try:
            async with asyncio.timeout(remaining):
                return await handler(request)
        except TimeoutError:
            return self._stop_model()

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
        state = self._state()
        call = getattr(request, "tool_call", {}) or {}
        name = str(call.get("name") or "")
        tool_call_id = str(call.get("id") or "")
        if self._timed_out(state):
            return self._stop_tool(tool_call_id, name, "总超时已耗尽")
        if name == "task":
            state.task_calls += 1
            if state.task_calls > self._budget.max_subagent_tasks:
                return self._stop_tool(tool_call_id, name, "task 数已达预算上限")
            async with state.slots:
                return await handler(request)
        attempts = 0
        while True:
            try:
                return await handler(request)
            except GraphBubbleUp:
                # Interrupt payloads/IDs may contain words such as "503" or
                # "timeout". They are control flow, never transport failures.
                raise
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
    if isinstance(exc, GraphBubbleUp):
        return False
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
