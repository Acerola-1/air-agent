"""Attach Langfuse tracing to a LangChain or LangGraph runnable when configured."""

from __future__ import annotations

import os
from typing import TypeVar

from langchain_core.runnables import Runnable
from loguru import logger

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def with_langfuse_tracing(
    runnable: Runnable[InputT, OutputT],
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

    from langfuse.langchain import CallbackHandler

    return runnable.with_config(callbacks=[CallbackHandler()])
