# ruff: noqa: D101, D102, D103, D107
"""Guard local formal-runtime startup with host sampling and owned teardown.

The guard is deliberately separate from the load generator supervisor: it
watches host memory while services are being brought up, refuses to start when
the host is already unstable, and blocks every remaining startup step as soon
as a stop line or a sampling failure appears.

Ownership is explicit.  A component is "owned" only when it was not running
before the guarded startup command and was started while that command ran.
Pre-existing services are never stopped; teardown uses graceful stop only and
never touches data volumes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from scripts.product_chat_perf.local_resources import ROOT, command, host_sample

MiB = 1048576
GiB = 1024 * MiB

# Stop application processes before their datastores.  Only owned entries are
# stopped; the lists define a safe order, not a startup plan.
APP_CONTAINERS = (
    "air-agent-langgraph-api",
    "lightrag-air",
    "lightrag-water",
    "lightrag-coastal",
    "air-agent-langfuse-web",
    "air-agent-langfuse-worker",
)
DATA_CONTAINERS = (
    "air-agent-postgres",
    "air-agent-langfuse-postgres",
    "lightrag-postgres",
    "iam-pg17",
    "air-agent-redis",
    "iam-redis7",
    "air-agent-langfuse-redis",
    "air-agent-langfuse-clickhouse",
    "air-agent-langfuse-minio",
    "lightrag-qdrant",
    "lightrag-neo4j",
)


class GuardRefused(RuntimeError):
    """The host is not stable enough to begin startup."""


class StartupAborted(RuntimeError):
    """A stop line or sampling failure closed the startup gate."""


@dataclass(frozen=True)
class Thresholds:
    """Frozen screening values for this guard.

    Preflight values detect *sustained* growth on an idle host; runtime values
    mirror the preflight standard's stop lines.  All are sampled protections,
    not operating-system hard limits.
    """

    preflight_seconds: float = 30.0
    preflight_interval: float = 2.0
    preflight_min_samples: int = 10
    preflight_swap_growth_bytes: int = 64 * MiB
    preflight_compressor_growth_bytes: int = 128 * MiB
    runtime_interval: float = 2.0
    runtime_swap_bytes: int = 256 * MiB
    runtime_compressor_bytes: int = 512 * MiB
    runtime_service_bytes: int = 8 * GiB
    command_timeout_seconds: float = 900.0
    terminate_grace_seconds: float = 5.0
    teardown_wait_seconds: float = 15.0


class HostSampler(Protocol):
    """Read one host-memory sample or raise when collection is unavailable."""

    def __call__(self) -> dict[str, Any]: ...


class ServiceSampler(Protocol):
    """Read one service-occupancy sample in bytes."""

    def __call__(self) -> int: ...


class World(Protocol):
    """The components the guard may inspect and (only if owned) stop."""

    def snapshot(self) -> dict[str, Any]:
        """Return current container states and host-service PID records."""

    def stop_container(self, name: str) -> str:
        """Stop one owned container gracefully and report the result."""

    def stop_host_service(self, name: str, pid: int) -> str:
        """Stop one owned host service after identity verification."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _host_ok(sample: dict[str, Any]) -> bool:
    return all(
        isinstance(sample.get(key), int)
        for key in ("swap_used_bytes", "compressed_resident_bytes")
    )


def evaluate_preflight(
    samples: list[dict[str, Any]], thresholds: Thresholds
) -> str | None:
    """Return a refusal reason when the idle host is not stable."""
    if len(samples) < thresholds.preflight_min_samples:
        return "preflight_too_few_samples"
    if not all(_host_ok(sample) for sample in samples):
        return "resource_sampling_unavailable"
    swap_growth = samples[-1]["swap_used_bytes"] - samples[0]["swap_used_bytes"]
    compressor_growth = (
        samples[-1]["compressed_resident_bytes"]
        - samples[0]["compressed_resident_bytes"]
    )
    if swap_growth >= thresholds.preflight_swap_growth_bytes:
        return "host_swap_growing_before_start"
    if compressor_growth >= thresholds.preflight_compressor_growth_bytes:
        return "host_compressor_growing_before_start"
    return None


def running_guest_usage(profile: dict[str, Any]) -> int:
    """Lower-bound guest bytes for whichever profile containers are running.

    Unlike the baseline collector this tolerates a partial startup, so it can
    stop a start in progress before the graph container exists.
    """
    names = list(profile["service_containers"])
    listing = json.loads(command(["container", "ls", "--all", "--format", "json"]))
    running = {row["id"] for row in listing if row["status"]["state"] == "running"}
    selected = [name for name in names if name in running]
    if not selected:
        return 0
    stats = json.loads(
        command(["container", "stats", "--no-stream", "--format", "json", *selected])
    )
    total = 0
    for row in stats:
        value = row.get("memoryUsageBytes")
        if not isinstance(value, int):
            raise ValueError("容器资源采集不完整")
        total += value
    return total


def _container_states() -> dict[str, str]:
    listing = json.loads(command(["container", "ls", "--all", "--format", "json"]))
    return {row["id"]: row["status"]["state"] for row in listing}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_matches(service: dict[str, Any], pid: int) -> bool:
    """Verify command marker and cwd before ever signalling a host process."""
    try:
        args = command(["ps", "-p", str(pid), "-o", "args="])
        cwd = command(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    except (OSError, subprocess.SubprocessError):
        return False
    expected_cwd = str((ROOT / service["expected_cwd"]).resolve())
    return service["command_marker"] in args and f"n{expected_cwd}" in cwd.splitlines()


class LocalWorld:
    """Real containers and host processes declared by the service profile."""

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.host_services = {item["name"]: item for item in profile["host_services"]}

    def snapshot(self) -> dict[str, Any]:
        host: dict[str, Any] = {}
        for name, service in self.host_services.items():
            path = ROOT / service["pid_file"]
            pid: int | None = None
            mtime: float | None = None
            try:
                pid = int(path.read_text().strip())
                mtime = path.stat().st_mtime
            except (OSError, ValueError):
                pid = None
            host[name] = {
                "pid": pid,
                "alive": bool(pid and _pid_alive(pid)),
                "pid_file_mtime": mtime,
            }
        return {"containers": _container_states(), "host_services": host}

    def stop_container(self, name: str) -> str:
        if name not in set(self.profile["service_containers"]):
            return "refused_not_in_profile"
        try:
            result = subprocess.run(
                ["container", "stop", name],
                capture_output=True,
                text=True,
                timeout=90,
            )
        except (OSError, subprocess.SubprocessError):
            return "failed"
        return "stopped" if result.returncode == 0 else "failed"

    def stop_host_service(self, name: str, pid: int) -> str:
        service = self.host_services.get(name)
        if service is None:
            return "refused_unknown_service"
        if not _pid_matches(service, pid):
            return "refused_identity_mismatch"
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return "failed"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                return "stopped"
            time.sleep(0.1)
        # Never escalate to SIGKILL for a service; report and leave it alone.
        return "still_running"


def classify_ownership(
    before: dict[str, Any],
    after: dict[str, Any],
    profile: dict[str, Any],
    *,
    started_after: float,
) -> dict[str, list[str]]:
    """Separate components this run started from ones already running."""
    allowed = set(profile["service_containers"])
    started_containers: list[str] = []
    preexisting_containers: list[str] = []
    for name, state in after["containers"].items():
        if state != "running":
            continue
        was_running = before["containers"].get(name) == "running"
        if was_running:
            preexisting_containers.append(name)
        elif name in allowed:
            started_containers.append(name)

    started_host: list[str] = []
    preexisting_host: list[str] = []
    for name, state in after["host_services"].items():
        if not state.get("alive"):
            continue
        previous = before["host_services"].get(name, {})
        pid_changed = state.get("pid") != previous.get("pid")
        file_is_new = (state.get("pid_file_mtime") or 0) >= started_after
        if pid_changed and file_is_new:
            started_host.append(name)
        else:
            preexisting_host.append(name)
    return {
        "started_containers": sorted(started_containers),
        "preexisting_containers": sorted(preexisting_containers),
        "started_host_services": sorted(started_host),
        "preexisting_host_services": sorted(preexisting_host),
    }


class StartupGuard:
    """Preflight, monitor, gate and ownership-aware teardown."""

    def __init__(
        self,
        out: Path,
        *,
        thresholds: Thresholds | None = None,
        sampler: HostSampler = host_sample,
        service_sampler: ServiceSampler | None = None,
        world: World | None = None,
        profile: dict[str, Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if out.exists():
            raise ValueError("输出目录已存在，拒绝覆盖")
        self.out = out
        self.thresholds = thresholds or Thresholds()
        self.sampler = sampler
        self.service_sampler = service_sampler
        self.world = world
        self.profile = profile
        self.sleep = sleep
        self.clock = clock
        self.abort_reason: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._samples: list[dict[str, Any]] = []
        self._monitor: threading.Thread | None = None
        self._baseline: dict[str, Any] | None = None
        self._started_at = 0.0

    # -- lifecycle ---------------------------------------------------------
    @property
    def aborted(self) -> bool:
        return self.abort_reason is not None

    def ensure_start_allowed(self, step: str) -> None:
        """Block every remaining startup step once the gate is closed."""
        if self.abort_reason is not None:
            raise StartupAborted(f"{step}: 启动已被禁止（{self.abort_reason}）")

    def _abort(self, reason: str) -> None:
        with self._lock:
            if self.abort_reason is None:
                self.abort_reason = reason
        self._stop.set()

    def _sample_host(self) -> dict[str, Any]:
        sample = self.sampler()
        if not _host_ok(sample):
            raise ValueError("宿主资源采集不完整")
        return dict(sample)

    def preflight(self) -> dict[str, Any]:
        """Sample an idle host and refuse startup when it is not stable."""
        count = max(
            self.thresholds.preflight_min_samples,
            (
                math.ceil(
                    self.thresholds.preflight_seconds
                    / self.thresholds.preflight_interval
                )
                + 1
            )
            if self.thresholds.preflight_seconds > 0
            else 0,
        )
        samples: list[dict[str, Any]] = []
        for index in range(count):
            try:
                samples.append(self._sample_host())
            except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                self._write_preflight(samples, "resource_sampling_unavailable")
                raise GuardRefused("resource_sampling_unavailable") from None
            if index + 1 < count:
                self.sleep(self.thresholds.preflight_interval)
        reason = evaluate_preflight(samples, self.thresholds)
        if reason is None and self.service_sampler is not None:
            try:
                used = int(self.service_sampler())
            except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                reason = "service_sampling_unavailable"
            else:
                if used >= self.thresholds.runtime_service_bytes:
                    reason = "service_group_already_at_budget"
        self._write_preflight(samples, reason)
        if reason:
            raise GuardRefused(reason)
        self._baseline = samples[-1]
        return {
            "status": "STABLE",
            "samples": len(samples),
            "baseline": samples[-1],
            "limits": asdict(self.thresholds),
        }

    def _write_preflight(
        self, samples: list[dict[str, Any]], reason: str | None
    ) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "preflight.jsonl").open("x") as stream:
            for sample in samples:
                stream.write(json.dumps(sample) + "\n")
        _write_json(
            self.out / "preflight.json",
            {
                "recorded_at": _now(),
                "status": "REFUSED" if reason else "STABLE",
                "reason": reason,
                "samples": len(samples),
                "swap_growth_bytes": (
                    samples[-1]["swap_used_bytes"] - samples[0]["swap_used_bytes"]
                    if samples
                    else None
                ),
                "compressor_growth_bytes": (
                    samples[-1]["compressed_resident_bytes"]
                    - samples[0]["compressed_resident_bytes"]
                    if samples
                    else None
                ),
                "limits": asdict(self.thresholds),
            },
        )

    def _monitor_loop(self) -> None:
        assert self._baseline is not None
        base = self._baseline
        peak_swap = 0
        peak_comp = 0
        with (self.out / "samples.jsonl").open("x") as stream:
            while not self._stop.is_set():
                try:
                    sample = self._sample_host()
                    service_bytes: int | None = None
                    if self.service_sampler is not None:
                        service_bytes = int(self.service_sampler())
                except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                    self._abort("resource_sampling_unavailable")
                    break
                swap_delta = max(0, sample["swap_used_bytes"] - base["swap_used_bytes"])
                comp_delta = max(
                    0,
                    sample["compressed_resident_bytes"]
                    - base["compressed_resident_bytes"],
                )
                peak_swap = max(peak_swap, swap_delta)
                peak_comp = max(peak_comp, comp_delta)
                row = {
                    "recorded_at": _now(),
                    "swap_delta_bytes": swap_delta,
                    "compressor_delta_bytes": comp_delta,
                    "service_bytes": service_bytes,
                    "host": sample,
                }
                with self._lock:
                    self._samples.append(row)
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                if swap_delta >= self.thresholds.runtime_swap_bytes:
                    self._abort("host_new_swap_limit")
                    break
                if comp_delta >= self.thresholds.runtime_compressor_bytes:
                    self._abort("host_new_compressor_limit")
                    break
                if (
                    service_bytes is not None
                    and service_bytes >= self.thresholds.runtime_service_bytes
                ):
                    self._abort("service_group_monitored_budget")
                    break
                self._stop.wait(self.thresholds.runtime_interval)
        self._peaks = {"swap": peak_swap, "compressor": peak_comp}

    def _start_monitor(self) -> None:
        self._started_at = time.time()
        self._monitor = threading.Thread(
            target=self._monitor_loop, name="startup-guard-monitor", daemon=True
        )
        self._monitor.start()

    def _stop_monitor(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=10)

    # -- teardown ----------------------------------------------------------
    def _stop_order(self, ownership: dict[str, list[str]]) -> list[tuple[str, str]]:
        started_containers = set(ownership["started_containers"])
        order: list[tuple[str, str]] = [
            ("host", name) for name in ownership["started_host_services"]
        ]
        for name in APP_CONTAINERS:
            if name in started_containers:
                order.append(("container", name))
        for name in DATA_CONTAINERS:
            if name in started_containers:
                order.append(("container", name))
        return order

    def teardown(
        self, ownership: dict[str, list[str]], after: dict[str, Any]
    ) -> dict[str, Any]:
        """Gracefully stop only owned components; never touch pre-existing ones."""
        stopped: list[dict[str, str]] = []
        still_running: list[dict[str, str]] = []
        refused: list[dict[str, str]] = []
        for kind, name in self._stop_order(ownership):
            if kind == "host":
                pid = (after["host_services"].get(name) or {}).get("pid")
                if not isinstance(pid, int):
                    refused.append({"kind": kind, "name": name, "result": "no_pid"})
                    continue
                result = self.world.stop_host_service(name, pid)
            else:
                result = self.world.stop_container(name)
            record = {"kind": kind, "name": name, "result": result}
            if result == "stopped":
                stopped.append(record)
            elif result == "still_running":
                still_running.append(record)
            else:
                refused.append(record)
        payload = {
            "stopped": stopped,
            "still_running": still_running,
            "refused": refused,
            "skipped_preexisting": [
                {"kind": "container", "name": name}
                for name in ownership["preexisting_containers"]
            ]
            + [
                {"kind": "host", "name": name}
                for name in ownership["preexisting_host_services"]
            ],
            "volumes_deleted": False,
            "data_deleted": False,
            "forced": False,
        }
        _write_json(self.out / "teardown.json", payload)
        return payload

    # -- guarded run -------------------------------------------------------
    def run(
        self,
        action: Callable[[StartupGuard], int],
        *,
        teardown_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Run a startup action between preflight and owned teardown."""
        try:
            preflight = self.preflight()
        except GuardRefused as exc:
            result = {
                "status": "REFUSED_PREFLIGHT",
                "abort_reason": str(exc),
                "recorded_at": _now(),
                "command_started": False,
                "command_exit_code": None,
            }
            _write_json(self.out / "guard.json", result)
            return result
        if self.world is None or self.profile is None:
            raise ValueError("运行启动需要 world 与 profile")
        before = self.world.snapshot()
        self._start_monitor()
        code: int | None = None
        error: str | None = None
        try:
            code = action(self)
        except StartupAborted:
            code = 130
        except Exception as exc:  # noqa: BLE001 - record, then teardown
            error = type(exc).__name__
            code = 1
        finally:
            self._stop_monitor()
        after = self.world.snapshot()
        ownership = classify_ownership(
            before, after, self.profile, started_after=self._started_at
        )
        teardown: dict[str, Any] | None = None
        if self.abort_reason is not None or (code != 0 and teardown_on_failure):
            teardown = self.teardown(ownership, after)
        peaks = getattr(self, "_peaks", {"swap": 0, "compressor": 0})
        if self.abort_reason is not None:
            status = f"ABORTED_{self.abort_reason.upper()}"
        elif code == 0:
            status = "COMPLETED"
        else:
            status = "FAILED_COMMAND"
        result = {
            "status": status,
            "abort_reason": self.abort_reason,
            "recorded_at": _now(),
            "command_started": True,
            "command_exit_code": code,
            "action_error": error,
            "preflight": preflight,
            "runtime": {
                "samples": len(self._samples),
                "peak_swap_delta_bytes": peaks["swap"],
                "peak_compressor_delta_bytes": peaks["compressor"],
                "limits": asdict(self.thresholds),
            },
            "ownership": ownership,
            "teardown": teardown,
        }
        _write_json(self.out / "guard.json", result)
        return result


# -- simulation -------------------------------------------------------------
class _ScriptedSampler:
    def __init__(self, samples: list[dict[str, Any]], error_at: int | None) -> None:
        self.samples = samples
        self.error_at = error_at
        self.index = 0

    def __call__(self) -> dict[str, Any]:
        if self.error_at is not None and self.index == self.error_at:
            raise OSError("scripted sampling failure")
        value = self.samples[min(self.index, len(self.samples) - 1)]
        self.index += 1
        return dict(value)


class _ScenarioWorld:
    def __init__(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        self.before = before
        self.after = after
        self.started = False
        self.stops: list[tuple[str, str]] = []

    def snapshot(self) -> dict[str, Any]:
        return self.after if self.started else self.before

    def mark_started(self) -> None:
        self.started = True

    def stop_container(self, name: str) -> str:
        self.stops.append(("container", name))
        return "stopped"

    def stop_host_service(self, name: str, pid: int) -> str:
        self.stops.append(("host", name))
        return "stopped"


def _scenario_thresholds(raw: dict[str, Any] | None) -> Thresholds:
    base = asdict(Thresholds())
    for key, value in (raw or {}).items():
        if key in base and isinstance(value, (int, float)):
            base[key] = value
    return Thresholds(**base)


def run_scenario(scenario: dict[str, Any], out: Path) -> dict[str, Any]:
    """Run a deterministic scenario without starting services or loading memory."""
    thresholds = _scenario_thresholds(scenario.get("thresholds"))
    preflight = scenario.get("preflight_samples", [])
    runtime = scenario.get("runtime_samples", [])
    sampler = _ScriptedSampler(preflight + runtime, scenario.get("sample_error_at"))
    world = _ScenarioWorld(
        scenario.get("before", {"containers": {}, "host_services": {}}),
        scenario.get("after", {"containers": {}, "host_services": {}}),
    )
    action_spec = scenario.get("action", {})
    steps = action_spec.get("steps", ["step-1"])
    hold = float(action_spec.get("hold_seconds", 0.05))
    exit_code = int(action_spec.get("exit_code", 0))
    executed: list[str] = []

    guard = StartupGuard(
        out,
        thresholds=thresholds,
        sampler=sampler,
        world=world,
        profile=scenario.get(
            "profile",
            {
                "service_containers": list(world.after["containers"]),
                "host_services": [],
            },
        ),
        sleep=lambda _seconds: None,
    )

    def action(active: StartupGuard) -> int:
        for step in steps:
            active.ensure_start_allowed(step)
            executed.append(step)
            world.mark_started()
            deadline = time.monotonic() + hold
            while time.monotonic() < deadline:
                if active.aborted:
                    return 130
                time.sleep(0.005)
        return exit_code

    result = guard.run(
        action, teardown_on_failure=bool(action_spec.get("teardown_on_failure", True))
    )
    result["scenario"] = scenario.get("name")
    result["simulated_steps"] = executed
    result["simulated_stops"] = [{"kind": k, "name": n} for k, n in world.stops]
    _write_json(out / "guard.json", result)
    return result


# -- CLI --------------------------------------------------------------------
def _load_profile(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _service_sampler(profile: dict[str, Any]) -> Callable[[], int]:
    def sample() -> int:
        return running_guest_usage(profile)

    return sample


def _run_step(guard: StartupGuard, argv: list[str], timeout: float) -> int:
    """Run one startup command; terminate only that command's process group."""
    log = open(guard.out / "command.log", "a")  # noqa: SIM115 - closed below
    try:
        process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if guard.aborted:
                _terminate_group(process, guard.thresholds.terminate_grace_seconds)
                return 130
            if time.monotonic() >= deadline:
                _terminate_group(process, guard.thresholds.terminate_grace_seconds)
                return 124
            time.sleep(0.2)
        return process.returncode
    finally:
        log.close()


def _terminate_group(process: subprocess.Popen, grace: float) -> None:
    """Stop the startup command itself, never a service it already detached."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _plan_action(guard: StartupGuard, steps: list[dict[str, Any]]) -> int:
    for step in steps:
        guard.ensure_start_allowed(step["name"])
        code = _run_step(
            guard,
            list(step["argv"]),
            guard.thresholds.command_timeout_seconds,
        )
        if code != 0:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    pre = sub.add_parser("preflight", help="只采样宿主，不启动任何服务")
    pre.add_argument("--out", type=Path, required=True)
    pre.add_argument("--seconds", type=float, default=30.0)
    pre.add_argument("--profile", type=Path)

    plan = sub.add_parser("plan", help="按计划启动，超线后停止后续步骤")
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--profile", type=Path, required=True)
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--seconds", type=float, default=30.0)
    plan.add_argument("--keep-on-failure", action="store_true")

    sim = sub.add_parser("simulate", help="轻量脚本场景，不启动服务")
    sim.add_argument("--out", type=Path, required=True)
    sim.add_argument("--scenario", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.action == "preflight":
        thresholds = Thresholds(preflight_seconds=args.seconds)
        service_sampler = None
        profile = None
        if args.profile:
            profile = _load_profile(args.profile)
            service_sampler = _service_sampler(profile)
        guard = StartupGuard(
            args.out, thresholds=thresholds, service_sampler=service_sampler
        )
        try:
            result = guard.preflight()
            print(json.dumps(result, ensure_ascii=False))  # noqa: T201
            return 0
        except GuardRefused as exc:
            print(json.dumps({"status": "REFUSED", "reason": str(exc)}))  # noqa: T201
            return 1
    if args.action == "simulate":
        scenario = json.loads(args.scenario.read_text())
        result = run_scenario(scenario, args.out)
        print(json.dumps(result, ensure_ascii=False))  # noqa: T201
        return 0 if result["status"] == "COMPLETED" else 1
    profile = _load_profile(args.profile)
    steps = json.loads(args.plan.read_text()).get("steps", [])
    guard = StartupGuard(
        args.out,
        thresholds=Thresholds(preflight_seconds=args.seconds),
        world=LocalWorld(profile),
        service_sampler=_service_sampler(profile),
        profile=profile,
    )
    result = guard.run(
        lambda active: _plan_action(active, steps),
        teardown_on_failure=not args.keep_on_failure,
    )
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
