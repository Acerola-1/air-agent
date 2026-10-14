"""接管回归：验证真实回调形状、费用及有界采集。"""

import asyncio
import dataclasses
import pathlib
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, LLMResult

from common.langfuse_tracing import privacy_mask
from common.observability.collector import ObservabilityCollector, WorkItem
from common.observability.context import ObservationContext
from common.observability.model_adapter import (
    ModelObservabilityHandler,
    normalize_usage,
)
from common.observability.pricing import compute_cost
from common.observability.store import ObservabilityBackendUnavailable
from common.observability.tool_adapter import classify_tool_output


def context():
    def u():
        return str(uuid.uuid4())

    return ObservationContext(
        u(), u(), u(), u(), u(), None, "basic-qa", "initial", None, None
    )


def test_standard_result_extracts_actual_usage():
    message = AIMessage(
        content="fixture",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    usage, status = normalize_usage(
        LLMResult(generations=[[ChatGeneration(message=message)]])
    )
    assert status == "measured" and usage["total_tokens"] == 120


def test_stream_final_deduplicates_and_cleans_state(monkeypatch):
    import common.observability.model_adapter as adapter

    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    queued = []
    monkeypatch.setattr(
        adapter, "get_collector", lambda: SimpleNamespace(enqueue=queued.append)
    )
    writes = []

    async def persist(ctx, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(adapter, "record_model_observation", persist)
    h = ModelObservabilityHandler(context())
    run = str(uuid.uuid4())
    h.on_chat_model_start({"kwargs": {"model": "fixture"}}, [], run_id=run, metadata={})
    h.on_llm_new_token("", run_id=run)
    assert h._state_for(run).ttft_ms is None
    msg = AIMessageChunk(
        content="ok",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    h.on_llm_new_token("ok", chunk=ChatGenerationChunk(message=msg), run_id=run)
    res = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(content="ok", usage_metadata=msg.usage_metadata)
                )
            ]
        ]
    )
    h.on_llm_end(res, run_id=run)
    h.on_llm_end(res, run_id=run)
    for work in queued:
        asyncio.run(work.fn())
    assert len(writes) == 2 and writes[0]["status"] == "started"
    assert writes[0]["observation_id"] == writes[1]["observation_id"]
    writes = writes[1:]
    assert (
        len(writes) == 1
        and writes[0]["usage"]["total_tokens"] == 120
        and writes[0]["ended_at"] is not None
    )
    assert not h._by_run


def test_negative_and_impossible_usage_do_not_become_measured():
    for raw in (
        {"input_tokens": -1, "output_tokens": 20},
        {"input_tokens": 1, "cache_read_tokens": 2},
        {"input_tokens": 2, "output_tokens": 3, "total_tokens": 9},
    ):
        assert normalize_usage(raw)[1] == "invalid_usage"


@pytest.mark.parametrize(
    "payload,status",
    [
        ('{"code":403}', "permission_denied"),
        ('{"success":true,"data":[]}', "empty_result"),
        ("{}", "empty_result"),
        ('{"success":false,"code":400}', "validation_rejected"),
    ],
)
def test_tool_business_result(payload, status):
    assert classify_tool_output(payload) == status


def test_mask_removes_nested_credentials_and_body():
    marker = "SAFE_TEST_PRIVATE_MARKER"
    safe = privacy_mask(
        data={
            "input": marker,
            "metadata": {"secret": marker},
            "tenant_id": str(uuid.uuid4()),
        }
    )
    assert marker not in str(safe) and "tenant_id" in safe
    assert privacy_mask(data=marker) is None


def test_price_precision_and_reasoning_subsets():
    p = SimpleNamespace(
        price_ref="fixture",
        currency="CNY",
        unit="1m_tokens",
        input_per_unit=Decimal("2"),
        output_per_unit=Decimal("8"),
        cache_read_per_unit=Decimal(".2"),
        cache_write_per_unit=None,
        reasoning_mode="independent",
        reasoning_per_unit=Decimal("10"),
    )
    r = compute_cost(
        p,
        observation_id="fixture",
        usage={
            "input_tokens": 1,
            "cache_read_tokens": 1,
            "output_tokens": 200,
            "reasoning_tokens": 50,
        },
        measurement_status="measured",
    )
    assert r.amount == Decimal(".0017002")
    p.reasoning_mode = "included_in_output"
    r = compute_cost(
        p,
        observation_id="fixture",
        usage={"input_tokens": 1, "cache_read_tokens": 1, "output_tokens": 0},
        measurement_status="measured",
    )
    assert r.amount == Decimal(".0000002")


def test_price_engine_copies_match():
    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "src/common/observability/pricing.py").read_bytes() == (
        root / "admin/api/app/services/observability_pricing.py"
    ).read_bytes()


def test_collector_retries_three_times_and_flush_is_bounded(monkeypatch):
    asyncio.run(_collector_scenario(monkeypatch))


async def _collector_scenario(monkeypatch):
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 4:
            raise ObservabilityBackendUnavailable("fixture")

    c = ObservabilityCollector()
    c.enqueue(WorkItem("fixture", context(), flaky))
    before = time.monotonic()
    stats = c.flush(timeout=0.01)
    assert time.monotonic() - before < 0.1 and stats["pending"] > 0
    await asyncio.to_thread(c.flush, 2)
    assert len(calls) == 4 and c.stats()["written"] == 1 and c.stats()["pending"] == 0
    await c.close()


def test_usage_sources_and_aliases_conflict_fail_closed():
    message = AIMessage(
        content="controlled",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        response_metadata={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
            }
        },
    )
    assert normalize_usage(message)[1] == "invalid_usage"
    assert (
        normalize_usage({"input_tokens": 10, "prompt_tokens": 11})[1] == "invalid_usage"
    )
    assert (
        normalize_usage({"input_tokens": 10, "prompt_tokens": -1})[1] == "invalid_usage"
    )
    result = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="controlled",
                        usage_metadata={
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    )
                )
            ]
        ],
        llm_output={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
            }
        },
    )
    assert normalize_usage(result)[1] == "invalid_usage"


def test_attach_callback_scope_replaces_old_submission_without_mutating_parent(
    monkeypatch,
):
    from langchain_core.callbacks import CallbackManager

    from common.observability.model_adapter import attach_observation_callbacks

    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    a = context()
    b = dataclasses.replace(a, submission_id=str(uuid.uuid4()))
    original = ModelObservabilityHandler(a)
    manager = CallbackManager([original], inheritable_handlers=[original])
    updated = attach_observation_callbacks({"callbacks": manager}, b)["callbacks"]
    assert manager.handlers == [original]
    assert (
        len(updated.handlers) == 1
        and updated.handlers[0]._ctx.submission_id == b.submission_id
    )


def test_nonchat_llm_uses_standard_start_callback(monkeypatch):
    from langchain_core.language_models.fake import FakeListLLM

    import common.observability.model_adapter as adapter

    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    queued = []
    monkeypatch.setattr(
        adapter, "get_collector", lambda: SimpleNamespace(enqueue=queued.append)
    )
    handler = ModelObservabilityHandler(context())
    assert (
        FakeListLLM(responses=["controlled"]).invoke(
            "fixture",
            config={"callbacks": [handler], "metadata": {"obs_role": "normalizer"}},
        )
        == "controlled"
    )
    assert len(queued) == 2 and not handler._by_run
