"""Read-only local idle baseline; no service startup or model requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.product_chat_perf.local_resources import ROOT, host_sample, service_sample


def collect(profile_path: Path, out: Path, *, seconds: float = 180) -> dict:
    """Require three healthy idle minutes and headroom before bounded load."""
    if seconds < 180 or out.exists():
        raise ValueError("基线至少 180 秒且输出必须为新目录")
    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes)
    out.mkdir(parents=True)
    result = {
        "status": "BLOCKED",
        "recorded_at": datetime.now(UTC).isoformat(),
        "profile_sha256": hashlib.sha256(
            json.dumps(profile, sort_keys=True).encode()
        ).hexdigest(),
        "required_seconds": seconds,
        "peak_accounted_service_bytes": None,
        "reason": None,
        "hard_group_limit_verified": False,
    }
    started = time.monotonic()
    peak = 0
    try:
        base = host_sample()
        # Canonical status checks health, not merely LISTEN or PID existence.
        for phase in ("start", "end"):
            if phase == "start":
                service_sample(profile)  # Reject stopped containers before HTTP checks.
            checked = subprocess.run(
                [sys.executable, "deployment/run_separated_local.py", "status"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            if checked.returncode:
                raise ValueError("正式入口健康检查未全部通过")
            if phase == "end":
                break
            measured_start = time.monotonic()
            with (out / "samples.jsonl").open("x") as stream:
                while True:
                    host = host_sample()
                    service = service_sample(profile)
                    peak = max(peak, service["accounted_service_bytes"])
                    sample = {"host": host, "service": service}
                    stream.write(json.dumps(sample) + "\n")
                    stream.flush()
                    if peak >= profile["idle_admission_max_bytes"]:
                        raise ValueError("空闲服务占用不足以保留升压余量")
                    if (
                        host["swap_used_bytes"] - base["swap_used_bytes"]
                        >= 256 * 1048576
                    ):
                        raise ValueError("基线期间宿主新增交换达到停止线")
                    if (
                        host["compressed_resident_bytes"]
                        - base["compressed_resident_bytes"]
                        >= 512 * 1048576
                    ):
                        raise ValueError("基线期间宿主压缩内存增长达到停止线")
                    if time.monotonic() - measured_start >= seconds:
                        break
                    time.sleep(2)
        result["status"] = "VERIFIED_FOR_LOCAL_BOUNDED_LOAD"
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        result["reason"] = (
            str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        )
    result["elapsed_seconds"] = time.monotonic() - started
    result["peak_accounted_service_bytes"] = peak or None
    result["recorded_at"] = datetime.now(UTC).isoformat()
    (out / "baseline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    """Save a new successful or blocked baseline receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    result = collect(args.profile, args.out, seconds=args.seconds)
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201
    raise SystemExit(0 if result["status"] == "VERIFIED_FOR_LOCAL_BOUNDED_LOAD" else 1)


if __name__ == "__main__":
    main()
