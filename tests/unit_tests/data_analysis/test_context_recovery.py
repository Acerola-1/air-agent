"""超窗分类与一次模型级恢复回归."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from common.model_budget import ContextBudgetExceeded
from common.model_errors import is_context_overflow
from data_analysis.context.preparation import (
    ContextRecoveryFailed,
    PreparedRequest,
)
from data_analysis.context.recovery import call_with_context_recovery


class ProviderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


def request(marker: str = "input", *, compacted: bool = False) -> PreparedRequest:
    return PreparedRequest(
        messages=[HumanMessage(content=marker)],
        schemas=[],
        metrics={"input_tokens": 1 if compacted else 5, "input_budget_tokens": 10},
        compacted=compacted,
        within_capacity=True,
    )


def test_overflow_classifier_ignores_unrelated_errors():
    assert is_context_overflow(ProviderError(400, "maximum context length exceeded"))
    assert is_context_overflow(ProviderError(400, "context_length_exceeded"))
    assert not is_context_overflow(ProviderError(400, "invalid parameter"))
    assert not is_context_overflow(ProviderError(401, "maximum context length"))
    assert not is_context_overflow(ProviderError(429, "rate limited"))
    assert not is_context_overflow(TimeoutError("timeout"))


@pytest.mark.anyio
async def test_overflow_compacts_once_and_retries_only_model(monkeypatch):
    attempts = {"invoke": 0, "prepare": 0, "guard": 0}
    recovered = request("compacted", compacted=True)

    async def fake_prepare(*args, **kwargs):
        attempts["prepare"] += 1
        assert kwargs.get("force_compact") is True
        return recovered

    monkeypatch.setattr(
        "data_analysis.context.recovery.prepare_model_request", fake_prepare
    )

    async def guard(config):
        attempts["guard"] += 1
        return None

    monkeypatch.setattr("common.identity.runtime.guard_runtime_call", guard)

    async def invoke(prepared):
        attempts["invoke"] += 1
        if attempts["invoke"] == 1:
            raise ProviderError(400, "maximum context length exceeded")
        return ("ok", prepared.messages[0].content)

    result, used = await call_with_context_recovery(
        invoke,
        request(),
        state={"messages": []},
        run_config={},
        prepare_kwargs={"system_messages": []},
    )
    assert result == ("ok", "compacted")
    assert used is recovered
    assert attempts == {"invoke": 2, "prepare": 1, "guard": 2}


@pytest.mark.anyio
async def test_non_overflow_error_is_not_retried(monkeypatch):
    attempts = {"invoke": 0}

    async def invoke(prepared):
        attempts["invoke"] += 1
        raise ProviderError(401, "unauthorized")

    async def fake_prepare(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("must not compact for auth errors")

    monkeypatch.setattr(
        "data_analysis.context.recovery.prepare_model_request", fake_prepare
    )
    with pytest.raises(ProviderError):
        await call_with_context_recovery(
            invoke,
            request(),
            state={"messages": []},
            run_config={},
            prepare_kwargs={},
        )
    assert attempts["invoke"] == 1


@pytest.mark.anyio
async def test_second_overflow_stops_without_loop(monkeypatch):
    attempts = {"invoke": 0}
    recovered = request("compacted", compacted=True)

    async def fake_prepare(*args, **kwargs):
        return recovered

    async def guard(config):
        return None

    monkeypatch.setattr(
        "data_analysis.context.recovery.prepare_model_request", fake_prepare
    )
    monkeypatch.setattr("common.identity.runtime.guard_runtime_call", guard)

    async def invoke(prepared):
        attempts["invoke"] += 1
        raise ProviderError(400, "maximum context length exceeded")

    with pytest.raises(ContextRecoveryFailed) as failed:
        await call_with_context_recovery(
            invoke,
            request(),
            state={"messages": []},
            run_config={},
            prepare_kwargs={},
        )
    assert attempts["invoke"] == 2
    assert failed.value.prepared is recovered
    assert isinstance(failed.value.__cause__, ProviderError)


@pytest.mark.anyio
async def test_no_compaction_available_is_bounded(monkeypatch):
    async def fake_prepare(*args, **kwargs):
        return request("same", compacted=False)

    async def guard(config):
        return None

    monkeypatch.setattr(
        "data_analysis.context.recovery.prepare_model_request", fake_prepare
    )
    monkeypatch.setattr("common.identity.runtime.guard_runtime_call", guard)

    async def invoke(prepared):
        raise ProviderError(400, "prompt is too long")

    with pytest.raises(ContextBudgetExceeded):
        await call_with_context_recovery(
            invoke,
            request(),
            state={"messages": []},
            run_config={},
            prepare_kwargs={},
        )
