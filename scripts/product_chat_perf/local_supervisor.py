"""Start local calibration only after an independent RSS/swap watchdog is ready.

This is a sampled stop guard, not a macOS aggregate hard memory limit. It owns
only the subprocess group it creates and never stops shared containers/apps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from scripts.product_chat_perf.local_resources import (
    host_sample,
    process_rss_bytes,
    service_sample,
)


def exceeded(
    rss: int, swap_delta: int, *, rss_limit: int, swap_limit: int
) -> str | None:
    """Evaluate current RSS and new swap; do not reject harmless historical swap."""
    if rss >= rss_limit:
        return "load_generator_rss_limit"
    if swap_delta >= swap_limit:
        return "host_new_swap_limit"
    return None


def stop_owned(process: subprocess.Popen) -> None:
    """Stop only our live process group, preserving every shared service."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def supervise(
    argv: list[str],
    out: Path,
    *,
    rss_limit_mib: int = 512,
    max_seconds: float = 600,
    swap_limit_mib: int = 256,
    interval: float = 0.25,
    service_profile: dict | None = None,
) -> dict:
    """Monitor a fresh child from process creation, including import/initialization."""
    if not argv or rss_limit_mib <= 0 or max_seconds <= 0 or not 0.05 <= interval <= 1:
        raise ValueError("监督参数非法")
    if out.exists():
        raise ValueError("输出已存在，拒绝覆盖")
    baseline = host_sample()  # Required before child starts, not after warmup.
    service_state = None
    if service_profile is not None:
        service_state = service_sample(service_profile)
        if (
            service_state["accounted_service_bytes"]
            >= service_profile["service_stop_bytes"]
        ):
            raise ValueError("服务基础占用已达本地停止预算，不能启动负载")
    out.mkdir(parents=True)
    start = time.monotonic()
    peak = 0
    reason = None
    code = None
    process = None
    env = {**os.environ, "AIR_AGENT_PERF_SUPERVISOR_PID": str(os.getpid())}
    if service_profile is not None:
        env["AIR_AGENT_PERF_SERVICE_GUARD"] = "enabled"
        env["AIR_AGENT_PERF_PROFILE_SHA256"] = hashlib.sha256(
            json.dumps(service_profile, sort_keys=True).encode()
        ).hexdigest()
    try:
        with (
            (out / "child.log").open("x") as log,
            (out / "samples.jsonl").open("x") as samples,
        ):
            process = subprocess.Popen(
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            last_host = baseline
            next_host = start
            while process.poll() is None:
                now = time.monotonic()
                try:
                    rss = process_rss_bytes(process.pid)
                    if now >= next_host:
                        last_host = host_sample()
                        if service_profile is not None:
                            service_state = service_sample(service_profile)
                        next_host = now + 1
                except (OSError, subprocess.SubprocessError, ValueError):
                    if process.poll() is not None:
                        break
                    reason = "resource_sampling_unavailable"
                    break
                peak = max(peak, rss)
                delta = max(
                    0, last_host["swap_used_bytes"] - baseline["swap_used_bytes"]
                )
                sample = {
                    "elapsed_seconds": now - start,
                    "rss_bytes": rss,
                    "new_host_swap_bytes": delta,
                    "service": service_state,
                }
                samples.write(json.dumps(sample) + "\n")
                samples.flush()
                reason = exceeded(
                    rss,
                    delta,
                    rss_limit=rss_limit_mib * 1048576,
                    swap_limit=swap_limit_mib * 1048576,
                )
                if reason:
                    break
                if (
                    service_state
                    and service_state["accounted_service_bytes"]
                    >= service_profile["service_stop_bytes"]
                ):
                    reason = "service_group_monitored_budget"
                    break
                if (
                    last_host["compressed_resident_bytes"]
                    - baseline["compressed_resident_bytes"]
                    >= 512 * 1048576
                ):
                    reason = "host_new_compressed_memory_limit"
                    break
                if now - start >= max_seconds:
                    reason = "wall_clock_limit"
                    break
                if (out / "child.log").stat().st_size > 64 * 1048576:
                    reason = "output_size_limit"
                    break
                time.sleep(interval)
    finally:
        if process is not None:
            stop_owned(process)
            code = process.returncode
    result = {
        "child_exit_code": code,
        "stop_reason": reason,
        "peak_observed_rss_bytes": peak,
        "rss_stop_limit_bytes": rss_limit_mib * 1048576,
        "new_swap_stop_limit_bytes": swap_limit_mib * 1048576,
        "max_seconds": max_seconds,
        "baseline_swap_bytes": baseline["swap_used_bytes"],
        "elapsed_seconds": time.monotonic() - start,
        "protection": "sampled_watchdog_not_hard_rss_quota",
        "sample_interval_seconds": interval,
    }
    (out / "supervisor.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    """Launch only explicitly supplied commands under the local stop guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rss-limit-mib", type=int, default=512)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--new-swap-limit-mib", type=int, default=256)
    parser.add_argument("--service-profile", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    profile = (
        json.loads(args.service_profile.read_text()) if args.service_profile else None
    )
    result = supervise(
        argv,
        args.out,
        rss_limit_mib=args.rss_limit_mib,
        max_seconds=args.seconds,
        swap_limit_mib=args.new_swap_limit_mib,
        service_profile=profile,
    )
    print(json.dumps(result))  # noqa: T201
    raise SystemExit(
        0 if result["stop_reason"] is None and result["child_exit_code"] == 0 else 1
    )


if __name__ == "__main__":
    main()
