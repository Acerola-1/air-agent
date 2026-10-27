"""Local load setup must be finite, supervised and separate from formal traffic."""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from scripts import max_concurrency_round as runner
from scripts.product_chat_perf import local_baseline
from scripts.product_chat_perf.local_supervisor import exceeded, supervise


def make_runner(tmp_path, monkeypatch, *, level=2, delay=0.02):
    def send(client, sample, **kwargs):
        time.sleep(delay)
        return {
            "sample_id": sample.sample_id,
            "primary_result": "stream_completed",
            "answer_nonempty": True,
            "answer_associated": True,
            "client_first_answer_ms": 5,
            "client_complete_ms": 10,
            "submission_key": sample.sample_id,
        }

    monkeypatch.setattr(runner, "send_sample", send)
    return runner.LevelRunner(
        "local",
        level,
        "calibrate",
        {},
        {"cookie": "synthetic-only", "tenant_id": "test", "context_version": 1},
        {
            "slo": {
                "completion_success_rate_min": 0.99,
                "legacy_first_body_p95_ms_max": 45000,
                "complete_p95_ms_max": 60000,
            }
        },
        runner.Budget(tmp_path, 1000),
        runner.StopConditions(monitor_infra=False),
    )


def test_question_bank_is_finite_and_tiny():
    samples = runner._samples()
    assert len(samples) == 10
    assert {s.case_id for s in samples} == set(runner.CASE_IDS)


def test_dispatch_measurement_excludes_response_and_scopes_sample(
    tmp_path, monkeypatch
):
    r = make_runner(tmp_path, monkeypatch, delay=0.12)
    r._send_one(client=object())
    row = r.rows[0]
    assert row["dispatch_delay_ms"] < 100
    assert row["ended_offset_ms"] - row["planned_offset_ms"] >= 100
    assert row["sample_id"].startswith("local:calibrate:")


def test_sustained_phase_requires_time_and_sample_floors(tmp_path, monkeypatch):
    r = make_runner(tmp_path, monkeypatch)
    result = r.run(0, 0, 1, 10, 0, tmp_path / "fast.jsonl")
    assert result["measure"]["seconds"] >= 1
    assert result["measure"]["sends"] >= 10
    assert result["Q1"]["sent"] >= 5 and result["Q2"]["sent"] >= 5
    measured = [row for row in r.rows if row["phase"] == "measure"]
    assert len(measured) == result["measure"]["sends"]
    assert result["overall"]["sent"] == len(measured)


def test_slow_stage_does_not_stop_at_time_with_too_few_sends(tmp_path, monkeypatch):
    r = make_runner(tmp_path, monkeypatch, level=1, delay=0.15)
    result = r.run(0, 0, 0.1, 8, 0, tmp_path / "slow.jsonl")
    assert result["measure"]["sends"] >= 8
    assert result["Q1"]["sent"] >= 4 and result["Q2"]["sent"] >= 4


def test_global_timer_and_real_load_default_closed(tmp_path, monkeypatch):
    budget = runner.Budget(tmp_path, 100, max_seconds=0)
    assert not budget.reserve(1)
    assert budget.state["sent_total"] == 0

    def unexpected(*args):
        pytest.fail("缺显式开关时不得读会话或发送请求")

    monkeypatch.setattr(runner, "_load", unexpected)
    with pytest.raises(SystemExit, match="禁止真实负载"):
        runner.require_local_ready(argparse.Namespace(enable_real_load=False))


def test_missing_baseline_blocks_paid_requests(monkeypatch):
    monkeypatch.setattr(runner, "_load", lambda _: (None, None, None, {}))
    with pytest.raises(SystemExit, match="资源基线未核验"):
        runner.require_local_ready(
            argparse.Namespace(enable_real_load=True, run_id="local")
        )


def test_legacy_login_requires_explicit_conditions_before_sso():
    with pytest.raises(SystemExit, match="显式提供本轮 --conditions"):
        runner.cmd_login(argparse.Namespace(conditions=None))


def test_watchdog_uses_rss_and_new_swap_not_old_swap():
    assert exceeded(10, 0, rss_limit=100, swap_limit=50) is None
    assert exceeded(100, 0, rss_limit=100, swap_limit=50) == "load_generator_rss_limit"
    assert exceeded(10, 50, rss_limit=100, swap_limit=50) == "host_new_swap_limit"


def test_independent_watchdog_stops_only_its_growing_child(tmp_path):
    child = "import time; a=[]\nfor i in range(100):\n a.append(bytearray(4*1024*1024)); time.sleep(.1)"
    result = supervise(
        [sys.executable, "-c", child],
        tmp_path / "guarded",
        rss_limit_mib=48,
        max_seconds=10,
        interval=0.1,
    )
    assert result["stop_reason"] == "load_generator_rss_limit"
    assert 48 * 1048576 <= result["peak_observed_rss_bytes"] < 128 * 1048576
    assert result["child_exit_code"] != 0


def test_independent_watchdog_allows_small_child_and_refuses_overwrite(tmp_path):
    out = tmp_path / "small"
    result = supervise(
        [sys.executable, "-c", "import time; time.sleep(.3)"], out, max_seconds=5
    )
    assert result["stop_reason"] is None and result["child_exit_code"] == 0
    with pytest.raises(ValueError, match="拒绝覆盖"):
        supervise([sys.executable, "-c", "pass"], out)


def test_baseline_preserves_blocked_receipt_when_services_missing(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    monkeypatch.setattr(local_baseline, "host_sample", lambda: {})

    def missing(_):
        raise ValueError("服务尚未运行")

    monkeypatch.setattr(local_baseline, "service_sample", missing)
    result = local_baseline.collect(profile, tmp_path / "out")
    assert result["status"] == "BLOCKED"
    assert (
        json.loads((tmp_path / "out/baseline.json").read_text())["reason"]
        == "服务尚未运行"
    )


@pytest.mark.parametrize(
    "service_bytes,expected", [(5, "VERIFIED_FOR_LOCAL_BOUNDED_LOAD"), (6, "BLOCKED")]
)
def test_baseline_requires_idle_headroom_with_healthy_control(
    tmp_path, monkeypatch, service_bytes, expected
):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"idle_admission_max_bytes": 6}))
    monkeypatch.setattr(
        local_baseline,
        "host_sample",
        lambda: {"swap_used_bytes": 0, "compressed_resident_bytes": 0},
    )
    monkeypatch.setattr(
        local_baseline,
        "service_sample",
        lambda _: {"accounted_service_bytes": service_bytes},
    )
    monkeypatch.setattr(
        local_baseline.subprocess,
        "run",
        lambda *a, **k: argparse.Namespace(returncode=0),
    )
    clock = iter([0, 1, 182, 183])
    monkeypatch.setattr(local_baseline.time, "monotonic", lambda: next(clock))
    result = local_baseline.collect(profile, tmp_path / "out")
    assert result["status"] == expected


def test_baseline_refuses_short_measurement(tmp_path):
    with pytest.raises(ValueError, match="至少 180 秒"):
        local_baseline.collect(tmp_path / "unused", tmp_path / "out", seconds=1)


@pytest.mark.parametrize(
    "age,profile,expected",
    [(0, "same", None), (1801, "same", "已过期"), (0, "different", "不一致")],
)
def test_real_load_gate_requires_fresh_matching_supervision(
    tmp_path, monkeypatch, age, profile, expected
):
    evidence = tmp_path / "baseline.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "VERIFIED_FOR_LOCAL_BOUNDED_LOAD",
                "recorded_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
                "profile_sha256": "same",
            }
        )
    )
    cond = {
        "local_resource_readiness": {
            "baseline_verified": True,
            "evidence_path": str(evidence),
        }
    }
    monkeypatch.setattr(runner, "_load", lambda _: (None, None, None, cond))
    monkeypatch.setenv("AIR_AGENT_PERF_SERVICE_GUARD", "enabled")
    monkeypatch.setenv("AIR_AGENT_PERF_SUPERVISOR_PID", str(os.getppid()))
    monkeypatch.setenv("AIR_AGENT_PERF_PROFILE_SHA256", profile)
    args = argparse.Namespace(enable_real_load=True, run_id="mock")
    if expected:
        with pytest.raises(SystemExit, match=expected):
            runner.require_local_ready(args)
    else:
        runner.require_local_ready(args)  # Checks only; never dispatches a request.
