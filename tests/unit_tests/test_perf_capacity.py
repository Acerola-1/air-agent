"""Capacity-tool calibration: no external models or shared runtime resources."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from scripts.product_chat_perf.capacity import capacity_verdict, summarize_stage
from scripts.product_chat_perf.gateway import send_gateway_sample
from scripts.product_chat_perf.plan import PlanError, public_plan, validate_plan
from scripts.product_chat_perf.quality import assess_answer
from scripts.product_chat_perf.scheduling import Budget, error_window_tripped, run_stage
from scripts.unified_campaign.sender import _StreamState


def stage(**changes):
    value = {
        "stage_id": "u-hy3-c2",
        "layer": "U",
        "profile": "F",
        "phase": "explore",
        "model": "hy3",
        "arrival": "closed",
        "concurrency": 2,
        "duration_seconds": 0.07,
        "min_samples": 4,
        "request_deadline_seconds": 1,
        "token_ceiling_per_request": 8000,
        "gateway": {
            "base_url": "http://127.0.0.1:28763/v1",
            "api_key_env": "PERF_TEST_MODEL_KEY",
        },
        "workloads": [
            {
                "workload_id": "short-text",
                "messages": [{"role": "user", "content": "请解释同比与环比。"}],
                "max_tokens": 64,
            }
        ],
        "thresholds": {
            "first_answer_p95_ms": 1000,
            "complete_p95_ms": 2000,
            "min_success_rate": 0.99,
            "min_quality_rate": 0.99,
        },
    }
    value.update(changes)
    return value


def plan(**changes):
    value = {
        "schema_version": "model-capacity-plan.v2",
        "run_id": "owned-calibration",
        "objective": "exploration",
        "budgets": {"max_seconds": 10, "max_requests": 50, "max_tokens": 400000},
        "retention": "retain_for_review",
        "allow_real_models": True,
        "allow_load": True,
        "stages": [stage()],
    }
    value.update(changes)
    return value


def test_plan_validation_is_network_free_and_refuses_wrong_runner():
    assert validate_plan(plan())["network_requests"] == 0
    with pytest.raises(PlanError, match="不支持"):
        validate_plan(plan(), runner="run_baseline")


@pytest.mark.parametrize(
    "patch", [{"budgets": {}}, {"objective": "capacity"}, {"retention": None}]
)
def test_missing_capacity_conditions_fail_before_io(patch):
    with pytest.raises(PlanError):
        validate_plan(plan(**patch))


def test_real_and_load_authorization_require_two_explicit_settings():
    with pytest.raises(PlanError, match="双重"):
        validate_plan(plan(), require_execution=True)
    assert validate_plan(
        plan(), enable_real_models=True, enable_load=True, require_execution=True
    )["validated"]


def test_complete_capacity_plan_needs_repeats_stability_and_sample_floors():
    stages = [
        stage(
            stage_id=phase,
            phase=phase,
            duration_seconds=3600 if phase == "stability" else 900,
            min_samples_per_workload=200,
            arrival="open"
            if phase in {"open", "stability", "burst", "recovery"}
            else "closed",
            arrival_rate_per_second=1,
        )
        for phase in ["explore", "confirm", "open", "stability", "burst", "recovery"]
    ]
    with pytest.raises(PlanError, match="三轮"):
        validate_plan(plan(objective="capacity", stages=stages))
    stages += [
        stage(
            stage_id=f"confirm-{index}",
            phase="confirm",
            duration_seconds=600,
            min_samples_per_workload=200,
        )
        for index in [2, 3]
    ]
    assert validate_plan(plan(objective="capacity", stages=stages))["validated"]
    stages[0]["duration_seconds"] = 1
    with pytest.raises(PlanError, match="门槛"):
        validate_plan(plan(objective="capacity", stages=stages))


def test_product_dependency_failure_cannot_be_a_complete_e_baseline():
    product = stage(
        layer="E",
        graph_profile="basic-qa",
        history_policy="generated",
        max_samples=100,
        observation_source={
            "run_id": "owned-calibration",
            "container": "owned-calibration-api",
            "database": "postgresql://127.0.0.1:28732/owned_calibration",
        },
        target={
            "bff_base_url": "http://127.0.0.1:28748",
            "admin_base_url": "http://127.0.0.1:28746",
            "ownership": {"run_id": "owned-calibration", "formal": False},
        },
    )
    with pytest.raises(PlanError, match="知识/MCP"):
        validate_plan(plan(stages=[product]))
    assert validate_plan(plan())["validated"]  # U does not require knowledge.


def test_credentials_and_private_inputs_stay_out_of_public_manifest():
    source = plan()
    source["stages"][0]["gateway"]["api_key"] = "never-persist-this"
    with pytest.raises(PlanError, match="凭据"):
        validate_plan(source)
    source = plan()
    source["stages"][0]["workloads"][0]["messages"][0]["content"] = "private question"
    assert "private question" not in json.dumps(public_plan(source))
    assert "sha256" in json.dumps(public_plan(source))


def test_stop_window_uses_instants_not_elapsed_latencies():
    spread = [
        {
            "primary_result": "client_timeout",
            "client_complete_ms": 1000,
            "ended_at_offset_ms": index * 10000,
        }
        for index in range(20)
    ]
    assert not error_window_tripped(spread)
    concentrated = [
        {
            "primary_result": "client_timeout" if index < 3 else "completed_success",
            "client_complete_ms": index * 10000 + 1000,
            "ended_at_offset_ms": index * 100,
        }
        for index in range(20)
    ]
    assert error_window_tripped(concentrated)
    assert not error_window_tripped(
        [{**row, "ended_at_offset_ms": None} for row in concentrated]
    )


def test_closed_loop_replenishes_and_waits_for_both_floors():
    counts = [0, 0]
    lock = threading.Lock()

    def send(_index):
        with lock:
            counts[0] += 1
            counts[1] = max(counts)
        time.sleep(0.015)
        with lock:
            counts[0] -= 1
        return {
            "primary_result": "completed_success",
            "sent": True,
            "client_first_answer_ms": 1,
            "client_complete_ms": 15,
            "quality_status": "PASS",
            "total_tokens": 10,
        }

    result = run_stage(stage(), send, budget=Budget(10, 50, 400000))
    assert result["sent"] > 4
    assert result["measure_seconds"] >= 0.07
    assert counts[1] == result["peak_inflight"] == 2
    assert result["rows"][-1]["ended_at_offset_ms"] > 0


def test_open_arrivals_do_not_wait_for_slow_completions():
    def send(_index):
        time.sleep(0.04)
        return {"primary_result": "completed_success", "sent": True, "total_tokens": 1}

    result = run_stage(
        stage(arrival="open", arrival_rate_per_second=150, concurrency=1),
        send,
        budget=Budget(10, 50, 400000),
    )
    assert result["planned"] >= 10
    assert result["not_sent"] > 0
    assert result["planned"] == result["sent"] + result["not_sent"]
    assert any(row["completion_window"] == "drain" for row in result["rows"])


def test_budget_is_reserved_before_send_and_unknown_usage_is_not_zero():
    calls = []
    result = run_stage(
        stage(),
        lambda index: (
            calls.append(index) or {"primary_result": "completed_success", "sent": True}
        ),
        budget=Budget(10, 3, 24000),
    )
    assert len(calls) == 3
    assert result["stop_reason"] == "request_budget"
    assert result["budget"]["missing_usage_requests"] == 3
    assert result["budget"]["reserved_or_actual_tokens"] == 24000


def test_drain_completions_not_in_measurement_throughput():
    def send(_index):
        time.sleep(0.06)
        return {
            "primary_result": "completed_success",
            "sent": True,
            "client_first_answer_ms": 1,
            "client_complete_ms": 60,
            "quality_status": "PASS",
            "total_tokens": 1,
        }

    value = stage(duration_seconds=0.015, min_samples=2)
    result = run_stage(value, send, budget=Budget(10, 20, 400000))
    summary = summarize_stage(value, result)
    assert summary["technical_success"] == 2
    assert summary["completed_in_measurement_window"] == 0
    assert summary["successful_completions_per_minute"] == 0


def test_transport_success_with_wrong_business_scope_does_not_pass_e():
    value = stage(layer="E")
    result = {
        "rows": [
            {
                "primary_result": "stream_completed",
                "client_first_answer_ms": 1,
                "client_complete_ms": 2,
                "business_valid": False,
                "quality_status": "FAIL",
                "completion_window": "measurement",
            }
        ],
        "planned": 1,
        "sent": 1,
        "not_sent": 0,
        "peak_inflight": 1,
        "measure_seconds": 1,
        "total_seconds_including_drain": 1,
        "stop_reason": None,
        "budget": {},
    }
    summary = summarize_stage(value, result)
    assert summary["technical_success_rate"] == 1
    assert summary["success_rate"] == 0
    assert summary["verdict"] == "FAIL"


def test_unexecuted_stages_and_exploration_do_not_claim_capacity():
    value = stage()
    summary = {
        "stage_id": value["stage_id"],
        "layer": "U",
        "model": "hy3",
        "arrival": "closed",
        "phase": "explore",
        "concurrency": 2,
        "verdict": "PASS",
    }
    result = capacity_verdict(plan(), [summary])
    assert result["by_model_and_layer"][0]["capacity_status"] == "EXPLORATORY"
    assert not result["by_model_and_layer"][0]["maximum_capacity_claim"]
    assert (
        capacity_verdict(plan(), [])["by_model_and_layer"][0]["capacity_status"]
        == "BLOCKED"
    )


def test_typed_units_have_separate_meaningful_result_and_gap_clocks():
    now = [0.0]
    state = _StreamState(0, lambda: now[0])
    state.consume(
        "custom",
        json.dumps(
            {
                "type": "final_output_start",
                "public_unit_version": 1,
                "output_protocol_version": 1,
            }
        ),
    )

    def delta(at, seq, text, **extra):
        now[0] = at
        state.consume(
            "custom",
            json.dumps(
                {"type": "final_output_delta", "message": text, "seq": seq, **extra}
            ),
        )

    delta(0.1, 1, "正在查询。")
    assert state.first_answer_ms is None
    unit = {
        "public_unit_version": 1,
        "unit_kind": "explanation",
        "unit_id": "u-1",
        "fragment_kind": "context",
        "fragment_id": "f-1",
    }
    delta(0.2, 2, "同比采用相同月份作为基期，环比采用紧邻的上一期。", **unit)
    delta(0.8, 3, "工具正在执行", unit_kind="status")
    delta(
        1.2,
        4,
        "洛阳市 AQI 为 82。",
        **{**unit, "unit_kind": "finding", "unit_id": "u-2"},
    )
    assert state.first_answer_ms == 200
    assert state.first_verified_result_ms == 1200
    assert state.max_answer_gap_ms == 1000
    assert len(state.public_units) == 2
    assert "message" not in state.public_units[0]
    # Exact replay is not a new visible unit or a new result clock.
    delta(
        2.0,
        4,
        "洛阳市 AQI 为 82。",
        **{**unit, "unit_kind": "finding", "unit_id": "u-2"},
    )
    assert len(state.public_units) == 2
    state.finish_observation(3500)
    assert state.max_answer_gap_ms == 2300


def test_untyped_legacy_delta_is_not_backfilled_as_verified_result():
    state = _StreamState(0, lambda: 1.0)
    state.consume(
        "custom",
        json.dumps({"type": "final_output_start", "output_protocol_version": 1}),
    )
    state.consume(
        "custom",
        json.dumps({"type": "final_output_delta", "message": "旧正文", "seq": 1}),
    )
    assert state.first_answer_ms == 1000
    assert state.first_delta_source == "legacy_untyped_delta"
    assert state.first_verified_result_ms is None


def test_q4_reuse_can_pass_but_changed_scope_and_real_fixture_mismatch_do_not():
    case = {"case_id": "BQA-033", "category": "Q4"}
    oracle = {
        "case_id": "BQA-033",
        "evidence_ref": "restricted:actual-result",
        "requires_new_query": False,
        "required_terms": ["2026年4月", "增加9天"],
    }
    result = assess_answer(
        case,
        "洛阳市2026年4月优良天数增加9天。",
        source="real",
        tool_count=0,
        oracle=oracle,
        history_verified=True,
    )
    assert result["status"] == "PASS"
    assert (
        assess_answer(
            case,
            "洛阳市2026年5月增加9天。",
            source="real",
            tool_count=0,
            oracle=oracle,
            history_verified=True,
        )["status"]
        == "FAIL"
    )
    assert (
        assess_answer(
            case, "15时峰值45。", source="real", tool_count=1, history_verified=True
        )["status"]
        == "BLOCKED"
    )
    assert (
        assess_answer(
            case,
            "洛阳市2026年4月优良天数增加9天。",
            source="real",
            tool_count=0,
            oracle=oracle,
            history_verified=False,
        )["status"]
        == "FAIL"
    )


def test_gateway_preserves_actual_model_and_usage_without_raw_content():
    chunks = [
        {
            "model": "gateway-hy3",
            "choices": [{"delta": {"reasoning_content": "private reasoning"}}],
        },
        {
            "model": "gateway-hy3",
            "choices": [
                {"delta": {"content": "同比与环比区别"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        },
    ]
    payload = (
        "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        + "data: [DONE]\n\n"
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=payload, headers={"content-type": "text/event-stream"}
        )
    )
    with httpx.Client(transport=transport) as client:
        row = send_gateway_sample(
            client,
            base_url="http://local/v1",
            api_key="secret",
            model="hy3",
            workload=stage()["workloads"][0],
            deadline_seconds=2,
            token_ceiling=8000,
        )
    assert row["primary_result"] == "completed_success"
    assert row["model_actual"] == "gateway-hy3"
    assert row["total_tokens"] == 14
    assert row["first_piece_kind"] == "reasoning"
    assert "private reasoning" not in json.dumps(row)
    assert "secret" not in json.dumps(row)


def test_gateway_refuses_insufficient_token_reservation_before_network():
    sends = []
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sends.append(request) or httpx.Response(200)
            )
        ) as client,
        pytest.raises(ValueError, match="拒绝发送"),
    ):
        send_gateway_sample(
            client,
            base_url="http://local/v1",
            api_key="secret",
            model="hy3",
            workload=stage()["workloads"][0],
            deadline_seconds=2,
            token_ceiling=1,
        )
    assert not sends


def test_resource_sampler_uses_observed_cores_and_real_intervals(monkeypatch):
    from scripts.product_chat_perf import run_baseline

    now = [1.0]
    ticks = [1000000]
    monkeypatch.setattr(run_baseline.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        run_baseline,
        "_container_stats",
        lambda names: {
            "owned": {
                "cpuUsageUsec": ticks[0],
                "cpuCount": 2,
                "memoryUsageBytes": 123,
                "memoryLimitBytes": 512,
            }
        },
    )
    sampler = run_baseline.ResourceSampler(["owned"])
    sampler.sample()
    now[0], ticks[0] = 3.0, 3000000
    sampler.sample()
    row = sampler.rows[-1]
    assert row["cpu_percent"] == 50
    assert row["sample_interval_seconds"] == 2
    assert row["memory_limit_bytes"] == 512


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -1])
def test_nonfinite_or_negative_plan_budgets_cannot_start(bad):
    with pytest.raises(PlanError):
        validate_plan(
            plan(budgets={"max_seconds": bad, "max_requests": 10, "max_tokens": 80000})
        )


def test_untrusted_usage_cannot_refund_reserved_tokens():
    budget = Budget(10, 10, 100)
    assert budget.reserve(30)
    budget.settle(30, -500)
    assert budget.snapshot()["reserved_or_actual_tokens"] == 30
    assert budget.snapshot()["missing_usage_requests"] == 1


@pytest.mark.parametrize(
    "case_id,terms,valid,invalid",
    [
        (
            "BQA-032",
            ["洛阳", "PM10", "排名第3"],
            "洛阳市 PM10 排名第3。",
            "洛阳市 PM2.5 排名第3。",
        ),
        (
            "BQA-033",
            ["2026年4月", "增加9天"],
            "2026年4月优良天数增加9天。",
            "2026年5月优良天数增加9天。",
        ),
        (
            "BQA-034",
            ["洛阳", "郑州", "综合指数"],
            "洛阳与郑州综合指数比较可复用本轮历史。",
            "洛阳与郑州 AQI 比较可复用本轮历史。",
        ),
        (
            "BQA-036",
            ["郑州", "PM2.5", "8时"],
            "郑州市 PM2.5 峰值为8时。",
            "郑州市 PM2.5 峰值为15时。",
        ),
    ],
)
def test_real_q4_oracles_use_actual_scope_and_allow_verified_history(
    case_id, terms, valid, invalid
):
    case = {"case_id": case_id, "category": "Q4"}
    oracle = {
        "case_id": case_id,
        "evidence_ref": "restricted:owned-source",
        "requires_new_query": False,
        "required_terms": terms,
    }

    def check(text):
        return assess_answer(
            case,
            text,
            source="real",
            tool_count=0,
            oracle=oracle,
            history_verified=True,
        )

    assert check(valid)["status"] == "PASS"
    assert check(invalid)["status"] == "FAIL"


class ControlledGateway:
    """One task-owned streaming endpoint, never an external model."""

    def __enter__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                frames = [
                    {
                        "model": "controlled-hy3",
                        "choices": [{"delta": {"reasoning_content": "private"}}],
                    },
                    {
                        "model": "controlled-hy3",
                        "choices": [
                            {
                                "delta": {"content": "同比与环比的口径不同。"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                ]
                for frame in frames:
                    self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(0.02)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_complete_controlled_plan_to_raw_bundle_and_recomputed_summary(
    monkeypatch, tmp_path
):
    from scripts.product_chat_perf.capacity import execute

    monkeypatch.setenv("PERF_TEST_MODEL_KEY", "synthetic-only")
    with ControlledGateway() as base:
        value = plan(
            stages=[
                stage(gateway={"base_url": base, "api_key_env": "PERF_TEST_MODEL_KEY"})
            ]
        )
        validate_plan(
            value, enable_real_models=True, enable_load=True, require_execution=True
        )
        out, evidence = tmp_path / "public", tmp_path / "private"
        summary = execute(value, out, evidence)
    rows = [
        json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()
    ]
    calls = [
        json.loads(line) for line in (out / "calls.jsonl").read_text().splitlines()
    ]
    measurement = json.loads((out / "stage-measurements.json").read_text())[0]
    recomputed = summarize_stage(
        value["stages"][0], {**measurement, "rows": rows, "arrivals": []}
    )
    assert recomputed == summary["stages"][0]
    assert len(rows) == len(calls) == summary["stages"][0]["sent"] >= 4
    assert summary["stages"][0]["technical_success"] == len(rows)
    assert summary["budget"]["known_actual_tokens"] == len(rows) * 15
    assert summary["by_model_and_layer"][0]["capacity_status"] == "EXPLORATORY"
    assert (out / "report.md").exists() and (evidence / "run-plan.json").exists()
    assert "synthetic-only" not in "".join(
        path.read_text() for path in out.iterdir() if path.is_file()
    )


def test_physical_meter_reserves_each_call_and_stops_before_next_upstream(monkeypatch):
    from scripts.product_chat_perf.meter import create_meter

    monkeypatch.setenv("METER_CLIENT_KEY", "owned-client")
    monkeypatch.setenv("METER_UPSTREAM_KEY", "owned-upstream")
    budget = Budget(10, 1, 10000)
    with ControlledGateway() as upstream:
        server = create_meter(
            {
                "run_id": "owned-calibration",
                "upstream_base_url": upstream,
                "allowed_models": ["hy3"],
                "client_key_env": "METER_CLIENT_KEY",
                "upstream_key_env": "METER_UPSTREAM_KEY",
                "max_output_tokens": 64,
                "request_deadline_seconds": 2,
            },
            budget,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            body = {
                "model": "hy3",
                "messages": [{"role": "user", "content": "同比"}],
                "stream": True,
                "max_tokens": 64,
            }
            headers = {"authorization": "Bearer owned-client"}
            with httpx.Client(timeout=3) as client:
                assert (
                    client.post(base + "/v1/chat/completions", json=body).status_code
                    == 401
                )
                first = client.post(
                    base + "/v1/chat/completions", json=body, headers=headers
                )
                assert first.status_code == 200 and "[DONE]" in first.text
                assert (
                    client.post(
                        base + "/v1/chat/completions", json=body, headers=headers
                    ).status_code
                    == 429
                )
                snapshot = client.get(base + "/budget", headers=headers).json()
                assert snapshot["budget"]["requests"] == 1
                assert snapshot["budget"]["known_actual_tokens"] == 15
                assert snapshot["peak_inflight"] == 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_nonstreaming_multiple_tools_preserve_actual_model_and_missing_usage():
    payload = {
        "model": "controlled-deepseek",
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": name, "arguments": "{}"}}
                        for name in ("alpha", "beta")
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    workload = {
        **stage()["workloads"][0],
        "mode": "tool",
        "stream": False,
        "tools": [
            {
                "type": "function",
                "function": {"name": name, "parameters": {"type": "object"}},
            }
            for name in ("alpha", "beta")
        ],
    }
    with httpx.Client(transport=transport) as client:
        row = send_gateway_sample(
            client,
            base_url="http://owned/v1",
            api_key="synthetic",
            model="hy3",
            workload=workload,
            deadline_seconds=2,
            token_ceiling=8000,
        )
    assert row["primary_result"] == "completed_success"
    assert row["tool_call_count"] == 2 and row["tool_calls_valid"]
    assert row["model_actual"] == "controlled-deepseek"
    assert row["total_tokens"] is None and row["usage_status"] == "not_provided"
    assert row["model_ttft_ms"] is None
