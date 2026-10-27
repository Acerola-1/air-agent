"""Startup guard: preflight refusal, live stop, ownership and safe teardown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from scripts.product_chat_perf.startup_guard import (
    GiB,
    GuardRefused,
    StartupAborted,
    StartupGuard,
    Thresholds,
    _run_step,
    classify_ownership,
    evaluate_preflight,
    run_scenario,
)

BASE_SWAP = 1_000_000_000
BASE_COMP = 2_000_000_000
MiB = 1024 * 1024
FUTURE = 4_102_444_800.0


def host(swap: int = BASE_SWAP, compressor: int = BASE_COMP) -> dict:
    return {
        "swap_used_bytes": swap,
        "compressed_resident_bytes": compressor,
        "free_pages_bytes": 50 * MiB,
    }


def stable(count: int = 3) -> list[dict]:
    return [host() for _ in range(count)]


def scenario(**overrides) -> dict:
    base = {
        "name": "test",
        "thresholds": {
            "preflight_seconds": 0,
            "preflight_min_samples": 3,
            "preflight_interval": 0.001,
            "runtime_interval": 0.005,
            "runtime_swap_bytes": 256 * MiB,
            "runtime_compressor_bytes": 512 * MiB,
            "runtime_service_bytes": 8 * GiB,
        },
        "preflight_samples": stable(),
        "runtime_samples": [host()],
        "before": {
            "containers": {
                "air-agent-postgres": "running",
                "air-agent-langgraph-api": "stopped",
            },
            "host_services": {},
        },
        "after": {
            "containers": {
                "air-agent-postgres": "running",
                "air-agent-langgraph-api": "running",
            },
            "host_services": {},
        },
        "action": {"steps": ["start-app"], "hold_seconds": 0.05, "exit_code": 0},
    }
    base.update(overrides)
    return base


def test_preflight_refuses_growing_swap(tmp_path):
    result = run_scenario(
        scenario(
            preflight_samples=[
                host(swap=BASE_SWAP),
                host(swap=BASE_SWAP + 10 * MiB),
                host(swap=BASE_SWAP + 100 * MiB),
            ]
        ),
        tmp_path / "swap",
    )
    assert result["status"] == "REFUSED_PREFLIGHT"
    assert result["abort_reason"] == "host_swap_growing_before_start"
    assert result["command_started"] is False
    assert result["simulated_steps"] == []
    assert result["simulated_stops"] == []
    assert (
        json.loads((tmp_path / "swap/preflight.json").read_text())["status"]
        == "REFUSED"
    )


def test_preflight_refuses_growing_compressor(tmp_path):
    result = run_scenario(
        scenario(
            preflight_samples=[
                host(compressor=BASE_COMP),
                host(compressor=BASE_COMP + 100 * MiB),
                host(compressor=BASE_COMP + 200 * MiB),
            ]
        ),
        tmp_path / "comp",
    )
    assert result["status"] == "REFUSED_PREFLIGHT"
    assert result["abort_reason"] == "host_compressor_growing_before_start"


def test_preflight_sampling_failure_blocks_command(tmp_path):
    result = run_scenario(scenario(sample_error_at=1), tmp_path / "error")
    assert result["status"] == "REFUSED_PREFLIGHT"
    assert result["abort_reason"] == "resource_sampling_unavailable"
    assert result["command_started"] is False


def test_runtime_swap_stops_starting_and_tears_down_owned_only(tmp_path):
    result = run_scenario(
        scenario(
            runtime_samples=[host(), host(swap=BASE_SWAP + 300 * MiB)],
            action={"steps": ["start-db", "start-app"], "hold_seconds": 0.3},
        ),
        tmp_path / "runtime",
    )
    assert result["status"] == "ABORTED_HOST_NEW_SWAP_LIMIT"
    assert result["command_exit_code"] == 130
    assert result["simulated_steps"] == ["start-db"]
    assert result["ownership"]["started_containers"] == ["air-agent-langgraph-api"]
    assert result["ownership"]["preexisting_containers"] == ["air-agent-postgres"]
    assert result["simulated_stops"] == [
        {"kind": "container", "name": "air-agent-langgraph-api"}
    ]
    assert {"kind": "container", "name": "air-agent-postgres"} in result["teardown"][
        "skipped_preexisting"
    ]
    assert result["teardown"]["forced"] is False


def test_runtime_compressor_stop_line(tmp_path):
    result = run_scenario(
        scenario(
            runtime_samples=[
                host(),
                host(compressor=BASE_COMP + 600 * MiB),
            ],
            action={"steps": ["start-app"], "hold_seconds": 0.3},
        ),
        tmp_path / "compressor",
    )
    assert result["status"] == "ABORTED_HOST_NEW_COMPRESSOR_LIMIT"
    assert result["command_exit_code"] == 130


def test_runtime_sampling_failure_closes_gate(tmp_path):
    result = run_scenario(
        scenario(
            runtime_samples=[host(), host()],
            sample_error_at=4,
            action={"steps": ["start-app"], "hold_seconds": 0.3},
        ),
        tmp_path / "sampling",
    )
    assert result["status"] == "ABORTED_RESOURCE_SAMPLING_UNAVAILABLE"
    assert result["command_exit_code"] == 130
    assert result["simulated_stops"] == [
        {"kind": "container", "name": "air-agent-langgraph-api"}
    ]


def test_normal_start_completes_without_teardown(tmp_path):
    result = run_scenario(
        scenario(action={"steps": ["start-db", "start-app"], "hold_seconds": 0.02}),
        tmp_path / "normal",
    )
    assert result["status"] == "COMPLETED"
    assert result["command_exit_code"] == 0
    assert result["simulated_steps"] == ["start-db", "start-app"]
    assert result["teardown"] is None
    assert result["simulated_stops"] == []


def test_failed_command_tears_down_only_new_components(tmp_path):
    result = run_scenario(
        scenario(
            before={
                "containers": {
                    "air-agent-postgres": "running",
                    "air-agent-redis": "running",
                    "air-agent-langgraph-api": "stopped",
                },
                "host_services": {
                    "iam-java": {"pid": 100, "alive": True, "pid_file_mtime": 0.0}
                },
            },
            after={
                "containers": {
                    "air-agent-postgres": "running",
                    "air-agent-redis": "running",
                    "air-agent-langgraph-api": "running",
                },
                "host_services": {
                    "iam-java": {"pid": 200, "alive": True, "pid_file_mtime": FUTURE}
                },
            },
            action={"steps": ["start-app"], "hold_seconds": 0.02, "exit_code": 1},
        ),
        tmp_path / "failed",
    )
    assert result["status"] == "FAILED_COMMAND"
    assert result["ownership"]["started_host_services"] == ["iam-java"]
    assert result["ownership"]["preexisting_containers"] == [
        "air-agent-postgres",
        "air-agent-redis",
    ]
    assert result["simulated_stops"] == [
        {"kind": "host", "name": "iam-java"},
        {"kind": "container", "name": "air-agent-langgraph-api"},
    ]
    skipped = {
        (item["kind"], item["name"])
        for item in result["teardown"]["skipped_preexisting"]
    }
    assert ("container", "air-agent-postgres") in skipped
    assert ("container", "air-agent-redis") in skipped


def test_preflight_refuses_service_group_already_at_budget(tmp_path):
    guard = StartupGuard(
        tmp_path / "budget",
        thresholds=Thresholds(preflight_seconds=0, preflight_min_samples=2),
        sampler=lambda: host(),
        service_sampler=lambda: 9 * GiB,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(GuardRefused, match="service_group_already_at_budget"):
        guard.preflight()


def test_preflight_refuses_service_sampling_failure(tmp_path):
    def broken() -> int:
        raise ValueError("stats unavailable")

    guard = StartupGuard(
        tmp_path / "service-error",
        thresholds=Thresholds(preflight_seconds=0, preflight_min_samples=2),
        sampler=lambda: host(),
        service_sampler=broken,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(GuardRefused, match="service_sampling_unavailable"):
        guard.preflight()


def test_gate_rejects_any_remaining_step_after_abort():
    guard = StartupGuard.__new__(StartupGuard)
    guard.abort_reason = "host_new_swap_limit"
    with pytest.raises(StartupAborted):
        guard.ensure_start_allowed("start-langgraph")


def test_preflight_growth_thresholds_are_inclusive():
    thresholds = Thresholds(
        preflight_min_samples=2,
        preflight_swap_growth_bytes=10,
        preflight_compressor_growth_bytes=10,
    )
    assert evaluate_preflight([host(), host()], thresholds) is None
    assert evaluate_preflight([host(), host(swap=BASE_SWAP + 9)], thresholds) is None
    assert (
        evaluate_preflight([host(), host(swap=BASE_SWAP + 10)], thresholds)
        == "host_swap_growing_before_start"
    )
    assert (
        evaluate_preflight([host(), {"swap_used_bytes": 1}], thresholds)
        == "resource_sampling_unavailable"
    )


def test_classification_requires_new_pid_file_for_host_services():
    profile = {
        "service_containers": ["air-agent-postgres"],
        "host_services": [],
    }
    before = {
        "containers": {"air-agent-postgres": "running"},
        "host_services": {
            "iam-java": {"pid": 100, "alive": True, "pid_file_mtime": 0.0}
        },
    }
    after = {
        "containers": {"air-agent-postgres": "running"},
        "host_services": {
            "iam-java": {"pid": 200, "alive": True, "pid_file_mtime": 1.0}
        },
    }
    owned = classify_ownership(before, after, profile, started_after=100.0)
    assert owned["started_host_services"] == []
    assert owned["preexisting_host_services"] == ["iam-java"]


def test_guard_refuses_to_overwrite_existing_output(tmp_path):
    out = tmp_path / "exists"
    out.mkdir()
    with pytest.raises(ValueError, match="拒绝覆盖"):
        StartupGuard(out)


def test_run_step_terminates_only_the_startup_command(tmp_path):
    out = tmp_path / "step"
    guard = StartupGuard(
        out,
        thresholds=Thresholds(preflight_seconds=0, preflight_min_samples=1),
        sampler=lambda: host(),
        sleep=lambda _seconds: None,
    )
    out.mkdir()
    pidfile = tmp_path / "child.pid"
    script = (
        f"import os,time;open(r'{pidfile}','w').write(str(os.getpid()));time.sleep(30)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    timer = threading.Timer(0.4, lambda: guard._abort("test_abort"))
    timer.start()
    try:
        code = _run_step(guard, [sys.executable, "-c", script], 10)
        time.sleep(0.2)
        pid = int(pidfile.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert unrelated.poll() is None
    finally:
        timer.cancel()
        unrelated.terminate()
        unrelated.wait(timeout=5)
    assert code == 130
