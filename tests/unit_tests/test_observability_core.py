"""观测域单元测试：上下文提取、usage 归一化、队列有界性、终态保护.

真实 PG 集成验证在 tests/integration_tests（需要 aa_observe04 专用库）；
本文件只覆盖不依赖数据库的纯逻辑与隔离替身。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from common.observability.collector import (
    ObservabilityCollector,
    WorkItem,
    reset_collector_for_tests,
)
from common.observability.context import (
    observation_context_from_config,
)
from common.observability.model_adapter import normalize_usage
from common.observability.tool_adapter import classify_exception, classify_tool_output


def _config(request_id: str = "r1", attempt_id: str = "a1") -> dict[str, Any]:
    return {
        "configurable": {
            "langgraph_auth_user": {
                "identity": "22222222-2222-4222-8222-222222222222",
                "tenant_id": "11111111-1111-4111-8111-111111111111",
                "membership_id": "33333333-3333-8333-8333-333333333333",
                "session_id": "44444444-4444-4444-8444-444444444444",
            },
            "ledger_request_id": request_id,
            "ledger_attempt_id": attempt_id,
            "ledger_graph_id": "basic-qa",
            "ledger_submission_id": "55555555-5555-5555-8555-555555555555",
            "thread_id": "thread-x",
            "run_id": "run-x",
        },
        "metadata": {},
    }


class TestObservationContext:
    def test_extracts_trusted_context(self) -> None:
        ctx = observation_context_from_config(_config())
        assert ctx is not None
        assert ctx.tenant_id == "11111111-1111-4111-8111-111111111111"
        assert ctx.attempt_id == "a1"
        assert ctx.graph_id == "basic-qa"
        assert ctx.platform_run_id == "run-x"

    def test_rejects_missing_identity(self) -> None:
        config = _config()
        config["configurable"].pop("langgraph_auth_user")
        assert observation_context_from_config(config) is None

    def test_rejects_missing_ledger_ids(self) -> None:
        config = _config()
        config["configurable"].pop("ledger_attempt_id")
        assert observation_context_from_config(config) is None

    def test_client_cannot_override_identity_via_metadata(self) -> None:
        config = _config()
        config["metadata"] = {
            "ledger_request_id": "forged",
            "ledger_attempt_id": "forged",
        }
        ctx = observation_context_from_config(config)
        # configurable 优先，且身份永远来自 langgraph_auth_user
        assert ctx is not None
        assert ctx.request_id == "r1"


class TestNormalizeUsage:
    def test_usage_metadata_primary(self) -> None:
        usage, status = normalize_usage(
            {
                "input_tokens": 1000,
                "output_tokens": 200,
                "total_tokens": 1200,
                "input_token_details": {"cache_read": 400},
                "output_token_details": {"reasoning": 50},
            }
        )
        assert status == "measured"
        assert usage["input_tokens"] == 1000
        assert usage["cache_read_tokens"] == 400
        assert usage["reasoning_tokens"] == 50

    def test_response_metadata_fallback(self) -> None:
        class Resp:
            response_metadata = {
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 20}
            }

        usage, status = normalize_usage(Resp())
        assert status == "measured"
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 20

    def test_missing_usage_stays_missing(self) -> None:
        usage, status = normalize_usage({"input_tokens": "bad"})
        assert status == "invalid_usage"
        assert usage == {}

    def test_none_usage_missing(self) -> None:
        usage, status = normalize_usage(None)
        assert status == "usage_missing"

    def test_bool_rejected(self) -> None:
        usage, status = normalize_usage({"input_tokens": True, "output_tokens": 1})
        # bool 不是合法计数：只收非 bool 整数
        assert "input_tokens" not in usage
        assert usage["output_tokens"] == 1
        assert status == "invalid_usage"

    def test_negative_rejected(self) -> None:
        usage, status = normalize_usage({"input_tokens": -5, "output_tokens": 1})
        assert "input_tokens" not in usage
        assert usage["output_tokens"] == 1
        assert status == "invalid_usage"


class TestToolClassification:
    def test_empty_string_is_empty_result(self) -> None:
        assert classify_tool_output("") == "empty_result"
        assert classify_tool_output(None) == "empty_result"

    def test_http200_business_failure_not_succeeded(self) -> None:
        body = '{"code": 403, "success": false, "message": "denied"}'
        assert classify_tool_output(body) == "permission_denied"
        body5 = '{"code": 502, "success": false}'
        assert classify_tool_output(body5) == "technical_error"

    def test_plain_success(self) -> None:
        assert classify_tool_output('{"rows": [1,2]}') == "succeeded"
        assert classify_tool_output("纯文本结果") == "succeeded"

    def test_exception_categories(self) -> None:
        assert classify_exception(TimeoutError("x")) == ("timed_out", "timeout")
        status, category = classify_exception(RuntimeError("authorization rejected"))
        assert status == "permission_denied"
        status2, _ = classify_exception(RuntimeError("什么未知错误"))
        assert status2 == "technical_error"


class TestBoundedCollector:
    @pytest.mark.anyio
    async def test_queue_full_drops_and_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
        collector = ObservabilityCollector()
        # 打满队列（不启动 worker：直接绕过 enqueue 的自动启动路径）
        enqueued = 0
        for _ in range(1000):
            item = WorkItem(kind="t", ctx=None, fn=_noop)  # type: ignore[arg-type]
            try:
                collector._queue.put_nowait(item)
                enqueued += 1
            except asyncio.QueueFull:
                break
        assert enqueued == 1000
        ok = collector.enqueue(WorkItem(kind="t", ctx=None, fn=_noop))  # type: ignore[arg-type]
        assert ok is False
        stats = collector.stats()
        assert stats["dropped"] == 1
        assert stats["last_drop_reason"] == "queue_full"
        await collector.close()

    @pytest.mark.anyio
    async def test_disabled_writes_drop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "false")
        collector = ObservabilityCollector()
        ctx = observation_context_from_config(_config())
        assert ctx is not None
        ok = collector.enqueue(WorkItem(kind="t", ctx=ctx, fn=_noop))
        assert ok is False
        assert collector.stats()["dropped"] == 1

    @pytest.mark.anyio
    async def test_successful_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
        calls: list[str] = []

        async def _write() -> None:
            calls.append("ok")

        collector = ObservabilityCollector()
        ctx = observation_context_from_config(_config())
        assert ctx is not None
        collector.enqueue(WorkItem(kind="t", ctx=ctx, fn=_write))
        stats = collector.flush(timeout=2.0)
        await collector.close()
        assert calls == ["ok"]
        assert stats["written"] == 1

    @pytest.mark.anyio
    async def test_retry_bounded_and_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
        from common.observability.store import ObservabilityBackendUnavailable

        attempts = {"n": 0}

        async def _flaky() -> None:
            attempts["n"] += 1
            raise ObservabilityBackendUnavailable("down")

        collector = ObservabilityCollector()
        ctx = observation_context_from_config(_config())
        assert ctx is not None
        collector.enqueue(WorkItem(kind="t", ctx=ctx, fn=_flaky))
        stats = collector.flush(timeout=5.0)
        await collector.close()
        # 重试 ≤3 次后失败可见
        assert attempts["n"] <= 4
        assert stats["failed"] == 1


async def _noop() -> None:
    return None


class TestObservationContextGuards:
    def test_guc_contains_operations_flag(self) -> None:
        ctx = observation_context_from_config(_config())
        assert ctx is not None
        guc = ctx.as_guc()
        assert guc["air_agent.agent_operations"] == "off"
        assert guc["air_agent.tenant_id"] == ctx.tenant_id


def teardown_function() -> None:
    reset_collector_for_tests()
