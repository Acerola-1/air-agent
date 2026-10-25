"""Bounded OpenAI-compatible gateway probes; no business tools are executed."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from scripts.unified_campaign.sender import _iter_sse


def send_gateway_sample(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    workload: dict[str, Any],
    deadline_seconds: float,
    token_ceiling: int,
) -> dict[str, Any]:
    """Measure one physical attempt, preserving content/tool/reasoning clocks.

    The byte count is a deliberately conservative upper bound on input tokens.
    A request cannot be sent when its frozen reservation does not cover the
    full input plus maximum completion budget. Tool calls are parsed, never
    executed. Credentials and raw content are absent from the returned row.
    """
    started = time.monotonic()
    body = {
        "model": model,
        "messages": workload["messages"],
        "stream": bool(workload.get("stream", True)),
        "max_tokens": int(workload.get("max_tokens", 256)),
        **workload.get("parameters", {}),
    }
    # A parameter override cannot change model, messages, output limit or mode.
    for key in ("model", "messages", "stream", "max_tokens"):
        if key in workload.get("parameters", {}):
            raise ValueError(f"parameters 不得覆盖冻结的 {key}")
    if workload.get("tools"):
        body["tools"] = workload["tools"]
    input_bound = (
        len(
            json.dumps(
                {"messages": body["messages"], "tools": body.get("tools", [])},
                ensure_ascii=False,
            ).encode()
        )
        + 512
    )
    if input_bound + body["max_tokens"] > token_ceiling:
        raise ValueError("Token 预留不足以覆盖输入字节上界与最大输出，拒绝发送")
    first: dict[str, float] = {}
    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    actual_model: str | None = None
    finish_reason: str | None = None
    error_type: str | None = None
    http_status: int | None = None
    done = False

    def accept(value: dict[str, Any], *, streaming: bool) -> None:
        nonlocal usage, actual_model, finish_reason
        if value.get("model"):
            actual_model = str(value["model"])
        if isinstance(value.get("usage"), dict):
            usage = value["usage"]
        for choice in value.get("choices") or []:
            piece = choice.get("delta" if streaming else "message") or {}
            for field, kind in (
                ("content", "content"),
                ("reasoning_content", "reasoning"),
                ("reasoning", "reasoning"),
            ):
                text = piece.get(field)
                if isinstance(text, str) and text:
                    if streaming:
                        first.setdefault(kind, (time.monotonic() - started) * 1000)
                    if kind == "content":
                        content.append(text)
            for position, item in enumerate(piece.get("tool_calls") or []):
                if streaming:
                    first.setdefault("tool_call", (time.monotonic() - started) * 1000)
                index = int(item.get("index", position))
                call = calls.setdefault(index, {"name": "", "arguments": ""})
                function = item.get("function") or {}
                call["name"] += str(function.get("name") or "")
                call["arguments"] += str(function.get("arguments") or "")
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])

    headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
    url = base_url.rstrip("/") + "/chat/completions"
    deadline_reached = threading.Event()

    def close_at_deadline() -> None:
        deadline_reached.set()
        # The runner owns one client per physical call. Closing it also bounds
        # a stalled read in worker threads; per-read timeouts alone do not.
        client.close()

    timer = threading.Timer(
        max(0.001, deadline_seconds - (time.monotonic() - started)), close_at_deadline
    )
    timer.daemon = True
    timer.start()
    try:
        with client.stream(
            "POST", url, json=body, headers=headers, timeout=deadline_seconds
        ) as response:
            http_status = response.status_code
            if http_status >= 400:
                # Never persist response text: proxies may echo credentials.
                error_type = (
                    "provider_429" if http_status == 429 else "provider_http_error"
                )
            elif body["stream"]:
                for _, data in _iter_sse(response.iter_lines()):
                    if time.monotonic() - started > deadline_seconds:
                        error_type = "client_timeout"
                        break
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        value = json.loads(data)
                    except json.JSONDecodeError:
                        error_type = "protocol_error"
                        break
                    if value.get("error"):
                        error_type = "provider_stream_error"
                        break
                    accept(value, streaming=True)
                # A finish marker is valid even on gateways omitting [DONE].
                done = done or finish_reason is not None
            else:
                accept(json.loads(response.read()), streaming=False)
                done = finish_reason is not None
    except httpx.TimeoutException:
        error_type = "client_timeout"
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError, RuntimeError):
        error_type = "transport_or_protocol_error"
    finally:
        timer.cancel()
    if deadline_reached.is_set() or time.monotonic() - started > deadline_seconds:
        error_type = "client_timeout"
    answer = "".join(content)
    tool_valid = bool(calls)
    tool_names = {
        tool.get("function", {}).get("name") for tool in workload.get("tools", [])
    }
    for call in calls.values():
        try:
            arguments = json.loads(call["arguments"])
            tool_valid = (
                tool_valid
                and isinstance(arguments, dict)
                and call["name"] in tool_names
            )
        except json.JSONDecodeError:
            tool_valid = False
    mode = workload.get("mode", "text")
    valid = tool_valid if mode == "tool" else bool(answer.strip())
    if finish_reason in {"length", "content_filter"}:
        valid = False
    if not error_type and not done:
        error_type = "stream_incomplete"
    if not error_type and not valid:
        error_type = "invalid_tool_call" if mode == "tool" else "empty_answer"
    expected = workload.get("expected_terms", [])
    quality = valid and all(str(term) in answer for term in expected)
    duration = (time.monotonic() - started) * 1000
    usage = usage or {}

    def count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    known_total = count(usage.get("total_tokens"))
    completion_details = usage.get("completion_tokens_details")
    prompt_details = usage.get("prompt_tokens_details")
    return {
        "layer": "U",
        "workload_id": workload["workload_id"],
        "primary_result": "completed_success" if not error_type else error_type,
        "sent": http_status is not None,
        "http_status": http_status,
        "model_requested": model,
        "model_actual": actual_model,
        "client_complete_ms": duration,
        "model_ttft_ms": min(first.values()) if first else None,
        "model_first_content_ms": first.get("content"),
        "model_first_reasoning_ms": first.get("reasoning"),
        "model_first_tool_call_ms": first.get("tool_call"),
        "first_piece_kind": min(first, key=first.get) if first else None,
        "ttft_measurement_status": "measured" if first else "non_streaming_or_missing",
        "client_first_answer_ms": first.get("content")
        if mode == "text"
        else first.get("tool_call"),
        "first_answer_scope": "upstream_content_or_tool_arguments_not_product_body",
        "quality_status": "PASS" if quality else "FAIL",
        "business_status": "not_applicable",
        "tool_call_count": len(calls),
        "tool_calls_valid": tool_valid if calls else None,
        "visible_chars": len(answer),
        "input_tokens": count(usage.get("prompt_tokens")),
        "output_tokens": count(usage.get("completion_tokens")),
        "total_tokens": known_total,
        "reasoning_tokens": count(completion_details.get("reasoning_tokens"))
        if isinstance(completion_details, dict)
        else None,
        "cache_read_tokens": count(prompt_details.get("cached_tokens"))
        if isinstance(prompt_details, dict)
        else None,
        "tool_argument_chars": sum(len(call["arguments"]) for call in calls.values()),
        "tool_argument_tokens": None,
        "tool_argument_tokens_status": "not_separately_provided",
        "usage_status": "provided" if known_total is not None else "not_provided",
        "finish_reason": finish_reason,
        "error_type": error_type,
        "price": None,
        "cost": None,
    }
