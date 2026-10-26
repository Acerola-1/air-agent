"""容量描述、缺失登记与主子不同窗口回归."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from common.config import config
from common.model_budget import request_metrics
from common.model_capacity import (
    ModelCapacityError,
    capacity_for_model,
    capacity_from_window,
)


class FakeModel:
    def __init__(self, name: str = "other-model", *, profile=None) -> None:
        self.model_name = name
        self.openai_api_base = "http://127.0.0.1:1/v1"
        self.profile = profile


def test_ratio_and_trigger_defaults(monkeypatch):
    monkeypatch.setattr(config, "MODEL_MAX_CONTEXT_TOKENS", 192000)
    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_TRIGGER_RATIO", 0.8)
    metrics = request_metrics([HumanMessage(content="a")])
    assert metrics["summary_trigger_tokens"] == 153600
    assert metrics["input_budget_tokens"] == 179712
    assert metrics["capacity_fingerprint"]


def test_ratio_invalid_is_explicit(monkeypatch):
    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_TRIGGER_RATIO", 1.5)
    with pytest.raises(ModelCapacityError, match="比例"):
        request_metrics([HumanMessage(content="a")])


def test_provider_limit_can_be_tighter(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 100000)
    metrics = request_metrics([HumanMessage(content="a")])
    assert metrics["input_budget_tokens"] == 100000
    assert metrics["provider_input_limit_tokens"] == 100000


def test_unknown_model_requires_explicit_capacity():
    with pytest.raises(ModelCapacityError, match="缺少受保护的容量配置"):
        capacity_for_model(FakeModel(), role="meteo")


def test_trusted_profile_supplies_subagent_window():
    resolved = capacity_for_model(
        FakeModel(profile={"max_input_tokens": 128000}), role="meteo"
    )
    assert resolved.window_tokens == 128000
    assert resolved.trigger_tokens == 102400


def test_parent_and_child_windows_do_not_share_trigger():
    parent = capacity_from_window(
        1_000_000,
        role="main",
        output_reserved_tokens=8192,
        safety_tokens=4096,
    )
    child = capacity_from_window(
        192000,
        role="meteo",
        output_reserved_tokens=8192,
        safety_tokens=4096,
    )
    assert parent.trigger_tokens == 800000
    assert child.trigger_tokens == 153600


def test_guarded_summarization_binds_explicit_window_trigger(monkeypatch):
    from intelligent_tracing import guards

    captured = {}

    class DummyMiddleware:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeModel:
        profile = None

    monkeypatch.setattr(guards, "GuardedSummarizationMiddleware", DummyMiddleware)
    resolved = capacity_from_window(
        192000,
        role="meteo",
        output_reserved_tokens=8192,
        safety_tokens=4096,
    )
    guards.guarded_summarization(FakeModel(), object(), capacity=resolved, role="meteo")
    assert captured["trigger"] == ("tokens", 153600)
