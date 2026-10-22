"""Attach Langfuse tracing to a LangChain or LangGraph runnable when configured."""

from __future__ import annotations

import os
from typing import Any, TypeVar

from langchain_core.runnables import Runnable
from loguru import logger

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


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
    return runnable.with_config(callbacks=[handler], metadata=trace_metadata)
