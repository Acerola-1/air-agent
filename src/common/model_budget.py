"""完整请求预算。未知 tokenizer 使用带余量的多语言估算，不以字节冒充 token."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from common.config import config


class ContextBudgetExceeded(ValueError):
    """请求超出已配置的输入上限."""


_CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff]")


@lru_cache(maxsize=16)
def _encoding(model_name: str, tokenizer_encoding: str) -> Any:
    """缓存编码及不可用结果；未知别名不反复尝试加载外部词表."""
    try:
        import tiktoken

        return (
            tiktoken.get_encoding(tokenizer_encoding)
            if tokenizer_encoding
            else tiktoken.encoding_for_model(model_name)
        )
    except Exception:
        return None


def estimate_text_tokens(
    text: str, model_name: str = "", *, tokenizer_encoding: str = ""
) -> tuple[int, str]:
    """专用编码优先；缺失时区分 ASCII／中日韩／其他 Unicode 并增加估算余量.

    ASCII 约三字符一个 token，中日韩约每字符 1.5 token；其他 Unicode
    按字节保留。此估算不是严格数学上界，必须与提供商 usage 分开展示。
    """
    encoding = _encoding(model_name, tokenizer_encoding)
    if encoding is not None:
        return len(
            encoding.encode(text, disallowed_special=())
        ), f"tokenizer:{encoding.name}"
    margin = config.MODEL_TOKEN_ESTIMATE_MARGIN
    if not math.isfinite(margin) or margin < 1:
        raise ValueError("token 估算余量必须是至少为 1 的有限数值")
    ascii_chars = sum(character.isascii() for character in text)
    cjk_chars = len(_CJK.findall(text))
    other_bytes = len(text.encode("utf-8")) - ascii_chars - 3 * cjk_chars
    return (
        math.ceil((ascii_chars / 3 + cjk_chars * 1.5 + other_bytes) * margin),
        f"estimate:multilingual:{margin:g}x",
    )


def estimate_request_tokens(
    messages: Sequence[BaseMessage],
    tools: Sequence[Any] = (),
    *,
    model_name: str = "",
    tokenizer_encoding: str = "",
) -> tuple[int, str]:
    """计量完整请求，并返回模型编码或估算来源."""
    total, source, _ = _request_parts(messages, tools, model_name, tokenizer_encoding)
    return total, source


def _request_parts(
    messages: Sequence[BaseMessage],
    tools: Sequence[Any],
    model_name: str,
    tokenizer_encoding: str,
) -> tuple[int, str, dict[str, int]]:
    """计量模型可见内容、推理文本、调用参数和工具 schema，排除宿主 artifact."""
    system = []
    conversation = []
    for message in messages:
        item: dict[str, Any] = {"role": message.type, "content": message.content}
        if message.name:
            item["name"] = message.name
        calls = getattr(message, "tool_calls", None)
        if calls:
            item["tool_calls"] = calls
        if getattr(message, "tool_call_id", None):
            item["tool_call_id"] = message.tool_call_id
        for key in ("reasoning", "reasoning_content", "function_call"):
            if key in message.additional_kwargs:
                item[key] = message.additional_kwargs[key]
        (system if message.type == "system" else conversation).append(item)
    sections = {
        "system": system,
        "conversation": conversation,
        "tools": [convert_to_openai_tool(tool) for tool in tools],
    }
    parts = {}
    _, source = estimate_text_tokens(
        "", model_name, tokenizer_encoding=tokenizer_encoding
    )
    for key, value in sections.items():
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        parts[key] = estimate_text_tokens(
            body if value else "", model_name, tokenizer_encoding=tokenizer_encoding
        )[0]
    parts["framing"] = 16 + 16 * len(messages) + 32 * len(tools)
    return sum(parts.values()), source, parts


def request_metrics(
    messages: Sequence[BaseMessage],
    tools: Sequence[Any] = (),
    *,
    summary_model: bool = False,
) -> dict[str, Any]:
    """构造可持久化指标，费用 usage 与工作输入分开."""
    if summary_model:
        window = config.SUMMARY_MODEL_CONTEXT_TOKENS
        output = config.SUMMARY_MODEL_OUTPUT_TOKENS
        safety = config.SUMMARY_MODEL_SAFETY_TOKENS
        name = config.SUMMARY_LLM or config.MAIN_LLM_MODEL
        encoding = config.SUMMARY_TOKENIZER_ENCODING
        limit = window - output - safety
    else:
        window = config.MODEL_MAX_CONTEXT_TOKENS
        output = config.MAIN_LLM_MAX_OUTPUT_TOKENS
        safety = config.MODEL_CONTEXT_SAFETY_TOKENS
        name = config.MAIN_LLM_MODEL
        encoding = config.MODEL_TOKENIZER_ENCODING
        limit = min(config.MODEL_CONTEXT_BUDGET_TOKENS, window - output - safety)
    if min(window, output, limit) <= 0 or safety < 0:
        raise ValueError("模型窗口、输出预留和安全余量配置无效")
    tokens, source, parts = _request_parts(messages, tools, name, encoding)
    return {
        "model": name,
        "window_tokens": window,
        "input_budget_tokens": limit,
        "input_tokens": tokens,
        "output_reserved_tokens": output,
        "safety_tokens": safety,
        "measurement_source": source,
        "input_breakdown_tokens": parts,
        "tool_count": len(tools),
    }


def enforce_request_budget(
    messages: Sequence[BaseMessage],
    tools: Sequence[Any] = (),
    *,
    summary_model: bool = False,
) -> dict[str, Any]:
    """校验完整模型请求，超限拒绝调用."""
    metrics = request_metrics(messages, tools, summary_model=summary_model)
    if metrics["input_tokens"] > metrics["input_budget_tokens"]:
        raise ContextBudgetExceeded(
            "本次资料超出模型可用输入容量，尚未完成处理。请缩小数据范围或调整模型容量配置。"
        )
    return metrics


def compact_tool_schemas(
    tools: Sequence[Any],
    *,
    description_chars: int | None = None,
    parameter_chars: int | None = None,
) -> list[dict[str, Any]]:
    """只精简描述与示例，保留参数类型、required、enum 及所有校验约束."""
    description_limit = description_chars or config.MODEL_TOOL_DESCRIPTION_CHARS
    parameter_limit = parameter_chars or config.MODEL_TOOL_PARAMETER_DESCRIPTION_CHARS

    def compact(value: Any, *, function: bool = False) -> Any:
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in {"examples", "example"}:
                continue
            if key == "description" and isinstance(item, str):
                limit = (
                    max(description_limit, 600)
                    if function and value.get("name") == "compute_artifact"
                    else description_limit
                    if function
                    else parameter_limit
                )
                result[key] = (
                    item
                    if len(item) <= limit
                    else item[: max(0, limit - 12)] + "（说明已精简）"
                )
            else:
                result[key] = compact(item, function=key == "function")
        return result

    return [compact(convert_to_openai_tool(tool)) for tool in tools]


def fit_tool_schemas(
    messages: Sequence[BaseMessage], tools: Sequence[Any]
) -> list[dict[str, Any]]:
    """按实际可用预算缩短说明，参数约束始终完整；仍超限由调用方拒绝."""
    schemas: list[dict[str, Any]] = []
    percent = config.MODEL_TOOL_SCHEMA_BUDGET_PERCENT
    if not 1 <= percent <= 100:
        raise ValueError("工具定义目标预算百分比必须在 1 至 100 之间")
    for divisor in (1, 2, 4, 8):
        schemas = compact_tool_schemas(
            tools,
            description_chars=max(48, config.MODEL_TOOL_DESCRIPTION_CHARS // divisor),
            parameter_chars=max(
                24, config.MODEL_TOOL_PARAMETER_DESCRIPTION_CHARS // divisor
            ),
        )
        metrics = request_metrics(messages, schemas)
        # 工具定义可精简时优先给结果／后续 ReAct 留空间，避免首轮刚好卡线。
        if (
            metrics["input_tokens"] <= metrics["input_budget_tokens"] * 0.85
            and metrics["input_breakdown_tokens"]["tools"]
            <= metrics["input_budget_tokens"] * percent / 100
        ):
            return schemas
    return schemas
