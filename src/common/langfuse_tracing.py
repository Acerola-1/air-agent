"""Attach Langfuse tracing to a LangChain or LangGraph runnable when configured."""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, TypeVar

from langchain_core.runnables import Runnable
from loguru import logger

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def privacy_mask(*, data: Any) -> Any:
    """Langfuse统一出口：只允许可校验标识和计数，不保留正文或任意嵌套配置.

    同时作用于现有自动CallbackHandler的input/output/metadata。
    """
    if not isinstance(data, dict):
        return None
    safe = {}
    for key in (
        "tenant_id",
        "user_id",
        "membership_id",
        "request_id",
        "attempt_id",
        "submission_id",
        "observation_id",
        "logical_call_id",
    ):
        value = data.get(key)
        try:
            safe[key] = str(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            pass
    for key in ("input_tokens", "output_tokens", "total_tokens", "duration_ms"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[key] = value
    if data.get("graph_id") in {
        "basic-qa",
        "data-analysis",
        "intelligent-tracing",
    }:
        safe["graph_id"] = data["graph_id"]
    return safe or None


def privacy_otel_mask(*, params):
    """在SDK支持的导出钩子覆盖原生OTel跨度，删除任意正文与自由元数据."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    safe_scalar = {
        "langfuse.observation.type",
        "langfuse.observation.level",
        "langfuse.observation.model.name",
    }
    for identifier, span in params.spans.items():
        attributes = dict(span.attributes)
        keep = {}
        for key, value in attributes.items():
            if (
                key in safe_scalar
                and isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", value)
            ):
                keep[key] = value
            elif key.startswith(
                ("langfuse.observation.metadata.", "langfuse.trace.metadata.")
            ):
                field = key.rsplit(".", 1)[-1]
                canonical = field.removeprefix("ledger_")
                if canonical in {
                    "tenant_id",
                    "user_id",
                    "membership_id",
                    "request_id",
                    "attempt_id",
                    "submission_id",
                    "observation_id",
                    "logical_call_id",
                    "thread_id",
                    "run_id",
                }:
                    try:
                        keep[key] = str(uuid.UUID(str(value)))
                    except (ValueError, TypeError):
                        pass
                elif canonical == "graph_id" and value in {
                    "basic-qa",
                    "data-analysis",
                    "intelligent-tracing",
                }:
                    keep[key] = value
            elif key in {"langfuse.observation.metadata", "langfuse.trace.metadata"}:
                try:
                    parsed = json.loads(value) if isinstance(value, str) else value
                except (ValueError, TypeError):
                    parsed = {}
                safe = privacy_mask(data=parsed)
                if safe:
                    keep[key] = json.dumps(safe)
            elif key == "langfuse.observation.usage_details":
                try:
                    parsed = json.loads(value) if isinstance(value, str) else value
                except (ValueError, TypeError):
                    parsed = {}
                if isinstance(parsed, dict):
                    safe = {
                        k: v
                        for k, v in parsed.items()
                        if k
                        in {
                            "input",
                            "output",
                            "total",
                            "input_cached_tokens",
                            "output_reasoning_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        }
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and v >= 0
                    }
                    if safe:
                        keep[key] = json.dumps(safe)
        patches[identifier] = OtelSpanPatch(
            delete_attributes=tuple(attributes), set_attributes=keep
        )
    return MaskOtelSpansResult(span_patches=patches)


def _configure_existing_sdk_privacy(client):
    # Native LangGraph may initialize this project's SDK singleton before graphs.
    # Configure the installed SDK's documented transforming-exporter hook as well.
    from langfuse._client.span_exporter import LangfuseTransformingSpanExporter
    from langfuse._client.span_processor import LangfuseSpanProcessor

    resources = client._resources
    resources.mask = privacy_mask
    resources.mask_otel_spans = privacy_otel_mask
    for processor in resources.tracer_provider._active_span_processor._span_processors:
        if (
            not isinstance(processor, LangfuseSpanProcessor)
            or processor.public_key != resources.public_key
        ):
            continue
        exporter = processor.span_exporter
        if isinstance(exporter, LangfuseTransformingSpanExporter):
            exporter._mask_otel_spans = privacy_otel_mask
            previous = getattr(
                processor, "_aa_original_export_filter", processor._should_export_span
            )
            processor._aa_original_export_filter = previous
            # Attribute mask cannot rewrite OTel events/status descriptions. Drop such
            # spans before export so raw exception bodies cannot bypass the masker.
            processor._should_export_span = lambda span, allow=previous: (
                not getattr(span, "events", ())
                and not getattr(getattr(span, "status", None), "description", None)
                and allow(span)
            )
        else:
            # Unsupported exporter fails closed for observation export, never for answers.
            processor._should_export_span = lambda span: False
            logger.warning("Langfuse export disabled: privacy hook unavailable")


def with_langfuse_tracing(  # noqa: UP047 - 冻结签名保持 TypeVar 形态
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

    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    # SDK singleton may already exist for prompt lookup. Always mask this callback's
    # client too; only the installed SDK's masking seam is used, never monkeypatch export.
    os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "1000")
    os.environ.setdefault("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "100")
    Langfuse(
        mask=privacy_mask,
        mask_otel_spans=privacy_otel_mask,
        timeout=2,
        flush_at=100,
        flush_interval=2,
    )

    class PrivacyCallbackHandler(CallbackHandler):
        def _log_debug_event(self, *args, **kwargs):
            # SDK debug messages contain unmasked prompts before the export masker.
            return None

        def _notify(self, run_id):
            from common.observability.model_adapter import notify_langfuse_trace

            observation = self._runs.get(run_id)
            if observation is not None:
                notify_langfuse_trace(run_id, observation.trace_id, observation.id)

        def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
            super().on_chat_model_start(serialized, messages, run_id=run_id, **kwargs)
            self._notify(run_id)

        def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
            super().on_llm_start(serialized, prompts, run_id=run_id, **kwargs)
            self._notify(run_id)

        def on_llm_error(self, error, **kwargs):
            super().on_llm_error(RuntimeError("model_call_failed"), **kwargs)

        def on_tool_error(self, error, **kwargs):
            super().on_tool_error(RuntimeError("tool_call_failed"), **kwargs)

        def on_chain_error(self, error, **kwargs):
            super().on_chain_error(RuntimeError("chain_call_failed"), **kwargs)

    handler = PrivacyCallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])
    handler._langfuse_client._mask = privacy_mask
    _configure_existing_sdk_privacy(handler._langfuse_client)
    try:
        # 观测域（change complete-agent-observability）反查真实 Langfuse trace id：
        # 同一 run_id 在所有 handler 间共享，注册表只存弱引用（不延长生命周期）。

        from common.observability.model_adapter import register_langfuse_handler

        register_langfuse_handler(handler)
    except Exception:  # noqa: BLE001 - 注册失败不影响追踪绑定
        pass
    return runnable.with_config(callbacks=[handler])
