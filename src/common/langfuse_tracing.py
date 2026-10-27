"""Attach Langfuse tracing to a LangChain or LangGraph runnable when configured."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from langchain_core.runnables import Runnable
from loguru import logger

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
_PROPAGATION_SCOPES: ContextVar[tuple[tuple[object, Any], ...]] = ContextVar(
    "langfuse_run_scopes", default=()
)


class _AttributePropagatingRunnable(Runnable[InputT, OutputT]):
    """Enter Langfuse attribute propagation in the caller's execution context.

    LangChain dispatches sync callbacks through a copied context. The SDK's
    root-only ``propagate_attributes`` therefore reaches the root span but not
    generations/tools created in other LangGraph node tasks (langfuse#16177).
    The caller's OTel context is copied into child tasks. Streaming scopes end
    before yielding, so closing an iterator from another task cannot leak labels.
    """

    def __init__(
        self, runnable: Runnable[InputT, OutputT], attributes: dict[str, Any]
    ) -> None:
        self._runnable = runnable
        self._attributes = attributes

    async def __aenter__(self) -> Runnable[InputT, OutputT]:
        """Keep the propagation context open for the native worker's run task."""
        cm = self._propagation()
        cm.__enter__()
        _PROPAGATION_SCOPES.set((*_PROPAGATION_SCOPES.get(), (self, cm)))
        return self._runnable

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        scopes = _PROPAGATION_SCOPES.get()
        if scopes:
            owner, cm = scopes[-1]
            if owner is not self:
                raise RuntimeError("Langfuse attribute scopes must exit in entry order")
            _PROPAGATION_SCOPES.set(scopes[:-1])
            cm.__exit__(exc_type, exc, tb)
        return False

    @contextmanager
    def _propagation(self) -> Iterator[None]:
        from langfuse import propagate_attributes

        with propagate_attributes(**self._attributes):
            yield

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: A002
        with self._propagation():
            return self._runnable.invoke(input, config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: A002
        with self._propagation():
            return await self._runnable.ainvoke(input, config, **kwargs)

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Iterator[Any]:  # noqa: A002
        source = self._runnable.stream(input, config, **kwargs)
        try:
            while True:
                with self._propagation():
                    try:
                        item = next(source)
                    except StopIteration:
                        return
                yield item
        finally:
            if close := getattr(source, "close", None):
                with self._propagation():
                    close()

    def astream(
        self, input: Any, config: Any = None, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:  # noqa: A002
        return self._stream_async(
            self._runnable.astream, input, config, *args, **kwargs
        )

    def astream_events(
        self, input: Any, config: Any = None, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:  # noqa: A002
        return self._stream_async(
            self._runnable.astream_events, input, config, *args, **kwargs
        )

    async def _stream_async(
        self,
        method: Callable[..., AsyncIterator[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        source = method(*args, **kwargs)
        try:
            while True:
                with self._propagation():
                    try:
                        item = await anext(source)
                    except StopAsyncIteration:
                        return
                yield item
        finally:
            if close := getattr(source, "aclose", None):
                with self._propagation():
                    await close()

    def __getattr__(self, name: str) -> Any:
        if name == "_runnable":
            raise AttributeError(name)
        value = getattr(self._runnable, name)
        if name == "astream_protocol_events" and callable(value):
            return lambda *args, **kwargs: self._stream_async(value, *args, **kwargs)
        return value


def with_langfuse_tracing(  # noqa: UP047 - 冻结签名保持 TypeVar 形态
    runnable: Runnable[InputT, OutputT],
    *,
    config: dict[str, Any] | None = None,
) -> Runnable[InputT, OutputT]:
    """Bind a Langfuse callback to a runnable when credentials are available.

    Tracing stays disabled unless explicitly enabled and all client settings are
    present. The callback is inherited by nested LangChain and LangGraph runs.
    """
    if os.getenv("LANGFUSE_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return runnable

    required_settings = (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    )
    missing_settings = [name for name in required_settings if not os.getenv(name)]
    if missing_settings:
        logger.warning(
            "Langfuse tracing is enabled but required settings are missing: {}",
            ", ".join(missing_settings),
        )
        return runnable

    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    trace_context = None
    trace_metadata = {}
    trace_attributes: dict[str, Any] = {}
    if config is not None:
        from common.observability.context import observation_context_from_config

        ctx = observation_context_from_config(config)
        if ctx is None:
            return runnable
        # A thread contains multiple questions. Only the server-admitted attempt
        # (including its resume submissions) owns this distributed trace.
        trace_context = {
            "trace_id": Langfuse.create_trace_id(
                seed=f"air-agent:{ctx.tenant_id}:{ctx.attempt_id}"
            )
        }
        trace_metadata = {
            "langfuse_trace_name": ctx.graph_id,
            "langfuse_user_id": ctx.user_id,
            "langfuse_session_id": ctx.thread_id,
        }
        trace_attributes = {
            "trace_name": ctx.graph_id,
            "user_id": ctx.user_id,
            "session_id": ctx.thread_id,
        }
        if ctx.account:
            # Keep the stable UUID as user_id; expose the readable account as a
            # custom metadata dimension on the trace and every child observation.
            trace_metadata["user_account"] = ctx.account
            trace_attributes["metadata"] = {"user_account": ctx.account}

    # Capture all graph/model/tool content in the configured Langfuse project.
    os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "1000")
    os.environ.setdefault("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "100")
    Langfuse(
        debug=False,
        timeout=2,
        flush_at=100,
        flush_interval=2,
    )

    handler = CallbackHandler(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"], trace_context=trace_context
    )
    # The standard callback flag keeps SDK context and local correlation ordered.
    handler.run_inline = True
    try:
        # 观测域（change complete-agent-observability）反查真实 Langfuse trace id：
        # 同一 run_id 在所有 handler 间共享，注册表只存弱引用（不延长生命周期）。

        from common.observability.model_adapter import register_langfuse_handler

        register_langfuse_handler(handler)
    except Exception:  # noqa: BLE001 - 注册失败不影响追踪绑定
        pass
    if config is None:
        return runnable.with_config(callbacks=[handler])

    # LangGraph merges these defaults with the worker's invocation callbacks,
    # preserving its callback manager and parent. Copying worker callbacks into
    # both places would deliver events twice when callbacks are a plain list.
    bound = runnable.with_config(callbacks=[handler], metadata=trace_metadata)
    return _AttributePropagatingRunnable(bound, trace_attributes)
