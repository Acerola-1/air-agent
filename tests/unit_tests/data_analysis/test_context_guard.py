"""历史投影、容量边界与旧配置迁移回归."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from common.config import config
from common.model_budget import (
    ContextBudgetExceeded,
    enforce_request_budget,
    estimate_text_tokens,
    fit_tool_schemas,
    request_metrics,
)
from common.model_capacity import ModelCapacityError, capacity_from_window
from data_analysis.context.projection import prepare_model_context


def history():
    return [
        HumanMessage(content="旧事实", id="h1"),
        AIMessage(content="旧答案", id="a1"),
        HumanMessage(content="新事实", id="h2"),
        AIMessage(content="新答案", id="a2"),
        HumanMessage(content="本轮", id="h3"),
    ]


def capacity(
    window: int = 192000,
    *,
    output: int = 8192,
    safety: int = 4096,
    provider: int | None = None,
):
    return capacity_from_window(
        window,
        role="main",
        output_reserved_tokens=output,
        safety_tokens=safety,
        provider_input_limit_tokens=provider,
    )


def test_projection_requires_valid_watermark():
    messages = history()
    assert prepare_model_context(messages, summary="旧摘要") == messages
    assert (
        prepare_model_context(
            messages, summary="旧摘要", summary_covered_message_id="missing"
        )
        == messages
    )
    assert (
        prepare_model_context(
            messages, summary="旧摘要", summary_covered_message_id="h1"
        )
        == messages
    )
    result = prepare_model_context(
        messages, summary="旧摘要", summary_covered_message_id="a1"
    )
    assert isinstance(result[0], SystemMessage)
    assert [m.id for m in result[1:]] == ["h2", "a2", "h3"]
    assert len(messages) == 5


def test_watermark_does_not_move_implicitly():
    messages = history() + [
        AIMessage(content="第三答", id="a3"),
        HumanMessage(content="第四问", id="h4"),
    ]
    result = prepare_model_context(
        messages, summary="仅第一轮", summary_covered_message_id="a1", keep_turns=1
    )
    assert any(m.id == "h2" for m in result)
    assert any(m.id == "h3" for m in result)


def test_current_tool_overflow_is_rejected():
    messages = [
        HumanMessage(content="统计"),
        AIMessage(content="", tool_calls=[{"id": "c", "name": "data", "args": {}}]),
        ToolMessage(content="x" * 10000, tool_call_id="c"),
    ]
    with pytest.raises(ContextBudgetExceeded):
        enforce_request_budget(
            prepare_model_context(messages), capacity=capacity(provider=1000)
        )
    assert len(messages[-1].content) == 10000


def test_system_args_schema_and_reasoning_are_counted():
    simple = request_metrics([HumanMessage(content="好")])["input_tokens"]
    complex_messages = [
        SystemMessage(content="规则" * 300),
        HumanMessage(content="好"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "think" * 100},
            tool_calls=[{"id": "x", "name": "big", "args": {"text": "a" * 2000}}],
        ),
    ]
    tool = {
        "type": "function",
        "function": {
            "name": "big",
            "description": "definition" * 300,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    result = request_metrics(complex_messages, [tool])
    assert result["input_tokens"] > simple + 2000
    assert result["measurement_source"].startswith("estimate:multilingual:")
    parts = result["input_breakdown_tokens"]
    assert parts["system"] > 0 and parts["conversation"] > 0 and parts["tools"] > 0
    assert sum(parts.values()) == result["input_tokens"]


def test_window_output_and_provider_limit_are_separate():
    main = capacity(10000, output=2000, safety=1000)
    assert main.input_budget_tokens == 7000
    assert main.trigger_tokens == 8000
    limited = capacity(10000, output=2000, safety=1000, provider=5000)
    assert limited.input_budget_tokens == 5000
    summary = capacity_from_window(
        8000,
        role="summary",
        output_reserved_tokens=1000,
        safety_tokens=500,
    )
    metrics = request_metrics(
        [HumanMessage(content="a")], summary_model=True, capacity=summary
    )
    assert metrics["input_budget_tokens"] == 6500


def test_130k_allowed_under_192k_window():
    """旧 120K 应用上限不再拒绝 130K 输入."""
    metrics = capacity(192000)
    assert metrics.input_budget_tokens == 179712
    assert metrics.trigger_tokens == 153600
    text = "x" * (130000 * 3)
    result = request_metrics([HumanMessage(content=text)], capacity=metrics)
    assert result["input_tokens"] > 120000
    assert enforce_request_budget([HumanMessage(content=text)], capacity=metrics)


def test_million_window_has_no_old_fixed_limit(monkeypatch):
    million = capacity(1_000_000)
    assert million.trigger_tokens == 800_000
    assert million.input_budget_tokens == 987_712
    monkeypatch.setattr(config, "MODEL_MAX_CONTEXT_TOKENS", 1_000_000)
    assert (
        request_metrics([HumanMessage(content="a")])["summary_trigger_tokens"]
        == 800_000
    )


def test_invalid_ratio_rejected():
    with pytest.raises(ModelCapacityError):
        capacity_from_window(
            1000,
            role="main",
            output_reserved_tokens=10,
            safety_tokens=10,
            trigger_ratio=1.0,
        )


def test_artifact_raw_payload_is_not_counted_as_model_input():
    plain = [ToolMessage(content="目录", tool_call_id="c")]
    archived = [
        ToolMessage(
            content="目录",
            tool_call_id="c",
            artifact={
                "storage_type": "database",
                "storage_status": "stored",
                "artifact_id": "art_1",
                "raw": "x" * 1_000_000,
            },
        )
    ]
    assert (
        request_metrics(archived)["input_tokens"]
        <= request_metrics(plain)["input_tokens"] + 5
    )


def test_abandoned_tool_protocol_closes_without_changing_history():
    messages = [
        HumanMessage(content="旧问"),
        AIMessage(
            content="", tool_calls=[{"id": "pending", "name": "data", "args": {}}]
        ),
        HumanMessage(content="新问"),
    ]
    projected = prepare_model_context(messages)
    assert (
        isinstance(projected[2], ToolMessage) and projected[2].tool_call_id == "pending"
    )
    assert len(messages) == 3


def test_schema_compaction_preserves_validation_constraints():
    from common.model_budget import compact_tool_schemas

    tool = {
        "type": "function",
        "function": {
            "name": "real_tool",
            "description": "说明" * 10000,
            "parameters": {
                "type": "object",
                "required": ["count"],
                "properties": {
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "enum": [1, 2, 3],
                        "description": "参数说明" * 1000,
                    }
                },
            },
        },
    }
    schema = compact_tool_schemas([tool])[0]
    assert schema["function"]["name"] == "real_tool"
    assert schema["function"]["parameters"]["required"] == ["count"]
    count = schema["function"]["parameters"]["properties"]["count"]
    assert (
        count["minimum"] == 1 and count["maximum"] == 20 and count["enum"] == [1, 2, 3]
    )
    assert len(schema["function"]["description"]) <= 256
    assert len(tool["function"]["description"]) == 20000


def test_unknown_alias_uses_multilingual_estimate_not_byte_count():
    text = "请问湖州市今天的 PM2.5 是多少？" + (
        '{"description":"查询城市逐小时浓度"}' * 100
    )
    tokens, source = estimate_text_tokens(text, "unknown-test-alias")
    assert source == "estimate:multilingual:1.25x"
    assert 0 < tokens < len(text.encode("utf-8")) * 0.8
    assert (
        estimate_text_tokens("中文" * 100, "unknown-test-alias")[0]
        > estimate_text_tokens("ab" * 100, "unknown-test-alias")[0]
    )


def test_explicit_tokenizer_and_estimate_margin(monkeypatch):
    from common import model_budget

    class Encoding:
        name = "fixture"

        def encode(self, text, **kwargs):
            return list(text)

    monkeypatch.setattr(
        model_budget,
        "_encoding",
        lambda model, encoding: Encoding() if encoding == "fixture" else None,
    )
    monkeypatch.setattr(config, "MODEL_TOKENIZER_ENCODING", "fixture")
    assert (
        request_metrics([HumanMessage(content="中文")])["measurement_source"]
        == "tokenizer:fixture"
    )
    monkeypatch.setattr(config, "MODEL_TOKEN_ESTIMATE_MARGIN", 2)
    assert estimate_text_tokens("中文", "unknown-test-alias")[0] == 6
    monkeypatch.setattr(config, "MODEL_TOKEN_ESTIMATE_MARGIN", 0)
    with pytest.raises(ValueError, match="估算余量"):
        estimate_text_tokens("中文", "unknown-test-alias")


def test_expert_query_with_many_tools_leaves_room_for_results(monkeypatch):
    monkeypatch.setattr(config, "MAIN_LLM_MODEL", "unknown-test-alias")
    monkeypatch.setattr(config, "MODEL_TOOL_SCHEMA_BUDGET_PERCENT", 40)
    messages = [
        SystemMessage(content="查询城市空气质量，保留单位日期。" * 1100),
        HumanMessage(content="你好"),
        AIMessage(content="你好，请问需要查询什么？"),
        HumanMessage(content="请问湖州市今天的 PM2.5 是多少？"),
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"query_{i}",
                "description": "查询城市空气质量" * 100,
                "parameters": {
                    "type": "object",
                    "required": ["city"],
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名称及适用范围" * 100,
                        }
                    },
                },
            },
        }
        for i in range(114)
    ]
    schemas = fit_tool_schemas(messages, tools)
    metrics = enforce_request_budget(messages, schemas)
    assert metrics["input_tokens"] < 102000
    assert metrics["input_breakdown_tokens"]["tools"] < 48000
    assert metrics["input_breakdown_tokens"]["conversation"] < 500
    assert all(t["function"]["parameters"]["required"] == ["city"] for t in schemas)
    enforce_request_budget(
        [
            *messages,
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "data", "name": "query_0", "args": {"city": "湖州市"}}
                ],
            ),
            ToolMessage(
                content="湖州市 PM2.5：20 微克/立方米。" * 100, tool_call_id="data"
            ),
        ],
        schemas,
    )


def test_legacy_keys_are_reported_but_never_used(monkeypatch):
    from common.config.settings import deprecated_context_setting_keys

    monkeypatch.setenv("MODEL_CONTEXT_BUDGET_TOKENS", "120000")
    monkeypatch.setenv("CONVERSATION_SUMMARY_THRESHOLD_TOKENS", "30000")
    assert deprecated_context_setting_keys() == (
        "MODEL_CONTEXT_BUDGET_TOKENS",
        "CONVERSATION_SUMMARY_THRESHOLD_TOKENS",
    )
    # 旧值残留不改变百万窗口的水位与边界。
    million = capacity(1_000_000)
    assert million.trigger_tokens == 800_000
    assert million.input_budget_tokens == 987_712
