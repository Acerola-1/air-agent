"""Stream a final answer through the shared safety buffer and publisher.

This module is the only place where a business graph turns model output into
public answer text.  Tool planning stays non-streaming and internal; the final
answer role is invoked without tool schemas and is explicitly streamed.

Policy equivalence: the complete answer still passes the existing knowledge
policy.  If that final check would rewrite text that was already public, the
stream is aborted instead of pretending the rewrite was a validation.  Questions
that require concrete regulation/limit handling are detected before generation
and use a single policy-bound unit, never streamed model paraphrases.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage
from loguru import logger

from common.knowledge.policy import (
    CITE_RE,
    apply_answer_policy,
    citation_ids_from_messages,
    is_specific_regulation_text,
)
from common.output_streaming.contract import (
    DELIVERY_MODE_POLICY_BOUND,
    DELIVERY_MODE_TOKEN,
)
from common.output_streaming.publisher import OutputAborted, OutputPublisher
from common.output_streaming.safety import SafeTextStream, StreamSafetyError


@dataclass
class StreamAnswerResult:
    """Outcome of one final-answer generation."""

    message: AIMessage
    text: str
    delivery_mode: str
    aborted: bool = False
    abort_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    units: int = 0


def strip_reasoning_text(text: str) -> str:
    """Remove complete or truncated ``<think>`` blocks from non-streamed text."""
    import re

    value = re.sub(r"<think>[\s\S]*?</think>", "", text or "")
    return re.sub(r"<think>[\s\S]*$", "", value).strip()


def chunk_text(chunk: Any) -> str:
    """Extract visible text from a chat-model chunk, ignoring reasoning blocks."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                part_type = str(part.get("type") or "")
                if part_type in {"text", "text_delta", "output_text"}:
                    parts.append(str(part.get("text") or part.get("content") or ""))
        return "".join(parts)
    return ""


def _usage_from_chunk(chunk: Any, current: dict[str, int]) -> dict[str, int]:
    usage = getattr(chunk, "usage_metadata", None)
    if not isinstance(usage, dict):
        return current
    merged = dict(current)
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            merged[key] = value
    return merged


def _json_failure(text: str) -> str | None:
    """Return a controlled message when the answer is an internal JSON error."""
    try:
        payload = json.loads(text.strip())
    except (ValueError, TypeError):
        return None
    if isinstance(payload, dict) and (
        payload.get("success") is False
        or (str(payload.get("code", "")).isdigit() and int(payload["code"]) >= 400)
    ):
        return "查询未完成，请调整条件或稍后重试。"
    return None


def _validate_public_unit(unit: str, policy_messages: list[AnyMessage]) -> str | None:
    """Return a public abort reason when a unit cannot be published on its own."""
    citations = citation_ids_from_messages(policy_messages)
    for match in CITE_RE.finditer(unit):
        if match.group(1).lower() not in citations:
            return "unknown_citation"
    if is_specific_regulation_text(unit):
        # The complete-answer policy replaces such paragraphs with verified
        # source quotations.  Publishing the model wording first would be a
        # retraction, so a mid-stream concrete claim is a hard stop.
        return "policy_requires_complete_answer"
    return None


async def stream_final_answer(
    model: Any,
    messages: list[AnyMessage],
    config: dict[str, Any],
    *,
    publisher: OutputPublisher,
    policy_messages: list[AnyMessage] | None = None,
    question: str = "",
    force_policy_bound: bool = False,
    invocation_metadata: dict[str, Any] | None = None,
) -> StreamAnswerResult:
    """Generate the final answer once and publish verified increments.

    ``messages`` are the model input; ``policy_messages`` are the graph messages
    used by the knowledge policy (tool citations).  Callers must not have sent
    any answer bytes for this output before invoking this function.
    """
    policy_context = list(policy_messages if policy_messages is not None else messages)
    policy_bound = force_policy_bound or is_specific_regulation_text(question or "")
    extra_config: dict[str, Any] = {
        "tags": ["nostream", "final-answer"],
    }
    if invocation_metadata:
        extra_config["metadata"] = dict(invocation_metadata)

    if policy_bound:
        result = await _stream_policy_bound(
            model, messages, config, publisher, policy_context, extra_config
        )
        return result

    publisher.start()
    safety = SafeTextStream()
    usage: dict[str, int] = {}
    units = 0
    published_text = ""

    try:
        async for chunk in model.astream(messages, config={**config, **extra_config}):
            if isinstance(chunk, AIMessageChunk):
                usage = _usage_from_chunk(chunk, usage)
            metadata = getattr(chunk, "response_metadata", None) or {}
            if metadata.get("finish_reason") in {"length", "content_filter"}:
                await publisher.abort(
                    "generation_failed", detail="truncated_model_output"
                )
                return _aborted_result(
                    publisher,
                    safety.clean_text,
                    usage,
                    units,
                    "truncated_model_output",
                )
            if getattr(chunk, "tool_calls", None):
                await publisher.abort(
                    "generation_failed", detail="tool_calls_in_answer_role"
                )
                return _aborted_result(
                    publisher,
                    "",
                    usage,
                    publisher.total_chars,
                    "tool_calls_in_answer_role",
                )
            text = chunk_text(chunk)
            for unit in safety.feed(text):
                reason = _validate_public_unit(unit, policy_context)
                if reason:
                    await publisher.abort(reason, detail="unit_policy")
                    return _aborted_result(
                        publisher, safety.clean_text, usage, units, reason
                    )
                if _json_failure(unit):
                    await publisher.abort(
                        "generation_failed", detail="json_error_payload"
                    )
                    return _aborted_result(
                        publisher, safety.clean_text, usage, units, "json_error_payload"
                    )
                if await publisher.publish(unit):
                    units += 1
        for unit in safety.flush():
            reason = _validate_public_unit(unit, policy_context)
            if reason:
                await publisher.abort(reason, detail="tail_policy")
                return _aborted_result(
                    publisher, safety.clean_text, usage, units, reason
                )
            if _json_failure(unit):
                await publisher.abort("generation_failed", detail="json_error_payload")
                return _aborted_result(
                    publisher, safety.clean_text, usage, units, "json_error_payload"
                )
            if await publisher.publish(unit):
                units += 1
        published_text = publisher.emitted_text
        full_text = safety.clean_text
    except StreamSafetyError as exc:
        await publisher.abort(exc.reason_code, detail="safety_buffer")
        return _aborted_result(publisher, "", usage, units, exc.reason_code)
    except OutputAborted as exc:
        return _aborted_result(publisher, "", usage, units, exc.reason_code)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.shield(
                publisher.abort("generation_failed", detail="cancelled")
            )
        raise
    except Exception as exc:  # noqa: BLE001 - stream errors must not fake success
        logger.bind(output_stream=True).warning(
            "final answer stream failed class={}", type(exc).__name__
        )
        await publisher.abort("generation_failed", detail=type(exc).__name__)
        return _aborted_result(publisher, "", usage, units, "generation_failed")

    # Complete-answer policy: the published prefix may only be extended.  A
    # rewrite is a failed stream, never a silent replacement.
    if not full_text.strip():
        await publisher.abort("generation_failed", detail="empty_answer")
        return _aborted_result(publisher, "", usage, units, "empty_answer")
    candidate = AIMessage(content=full_text)
    policy_result = apply_answer_policy(candidate, policy_context)
    final_text = (
        policy_result.content if isinstance(policy_result.content, str) else full_text
    )
    blocked = bool(policy_result.additional_kwargs.get("knowledge_policy_blocked"))
    if blocked or not final_text.startswith(published_text):
        reason = (
            str(policy_result.additional_kwargs.get("knowledge_policy_blocked"))
            if blocked
            else "final_policy_rewrite"
        )
        await publisher.abort("policy_blocked", detail=reason)
        controlled_text = final_text if blocked else full_text
        return _aborted_result(publisher, controlled_text, usage, units, reason)

    try:
        final_text = await publisher.finish(final_text)
    except OutputAborted as exc:
        return _aborted_result(publisher, full_text, usage, units, exc.reason_code)
    except Exception as exc:  # noqa: BLE001
        logger.bind(output_stream=True).warning(
            "final answer confirmation failed class={}", type(exc).__name__
        )
        await publisher.abort("generation_failed", detail=type(exc).__name__)
        return _aborted_result(publisher, full_text, usage, units, "generation_failed")

    message = AIMessage(
        content=final_text,
        id=publisher.message_id,
        additional_kwargs={
            "ui_visible": True,
            "output_protocol_version": 1,
            "output_id": publisher.output_id,
        },
        usage_metadata=usage or None,
    )
    return StreamAnswerResult(
        message=message,
        text=final_text,
        delivery_mode=DELIVERY_MODE_TOKEN,
        usage=usage,
        units=units,
    )


async def _stream_policy_bound(
    model: Any,
    messages: list[AnyMessage],
    config: dict[str, Any],
    publisher: OutputPublisher,
    policy_context: list[AnyMessage],
    extra_config: dict[str, Any],
) -> StreamAnswerResult:
    """Buffered single unit for questions that require exact source wording."""
    publisher.start()
    try:
        response = await model.ainvoke(messages, config={**config, **extra_config})
    except Exception as exc:  # noqa: BLE001
        await publisher.abort("generation_failed", detail=type(exc).__name__)
        return _aborted_result(publisher, "", {}, 0, "generation_failed")
    raw = (
        chunk_text(response)
        if not isinstance(response.content, str)
        else response.content
    )
    raw = strip_reasoning_text(raw)
    policy_result = apply_answer_policy(AIMessage(content=raw), policy_context)
    final_text = (
        policy_result.content if isinstance(policy_result.content, str) else raw
    )
    if not final_text.strip():
        final_text = "未生成完整答案，请重试。"
    try:
        complete = await publisher.finish(final_text)
    except OutputAborted as exc:
        return _aborted_result(publisher, final_text, {}, 0, exc.reason_code)
    usage = (
        dict(response.usage_metadata)
        if isinstance(getattr(response, "usage_metadata", None), dict)
        else {}
    )
    message = AIMessage(
        content=complete,
        id=publisher.message_id,
        additional_kwargs={
            "ui_visible": True,
            "output_protocol_version": 1,
            "output_id": publisher.output_id,
            "policy_bound": True,
        },
        usage_metadata=usage or None,
    )
    return StreamAnswerResult(
        message=message,
        text=complete,
        delivery_mode=DELIVERY_MODE_POLICY_BOUND,
        usage=usage,
        units=1,
    )


def _aborted_result(
    publisher: OutputPublisher,
    text: str,
    usage: dict[str, int],
    units: int,
    reason: str,
) -> StreamAnswerResult:
    """Build a non-confirmed message for an interrupted output."""
    return StreamAnswerResult(
        message=AIMessage(
            content=text,
            id=publisher.message_id,
            additional_kwargs={
                "ui_visible": False,
                "output_protocol_version": 1,
                "output_id": publisher.output_id,
                "output_aborted": reason,
            },
        ),
        text=text,
        delivery_mode=DELIVERY_MODE_TOKEN,
        aborted=True,
        abort_reason=reason,
        usage=usage,
        units=units,
    )
