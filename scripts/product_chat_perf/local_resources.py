"""Read the actual local Mac resource conditions without starting workloads."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def command(args: list[str]) -> str:
    """Read one bounded CLI response without shell expansion."""
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=10
    ).stdout


def process_rss_bytes(pid: int) -> int:
    """Read resident bytes; never mistake virtual address reservation for RAM."""
    value = command(["ps", "-p", str(pid), "-o", "rss="]).strip()
    return int(value) * 1024


def host_sample() -> dict:
    """Record host RAM and swap; existing swap alone is not new pressure."""
    vm = command(["vm_stat"])
    size = int(re.search(r"page size of (\d+) bytes", vm)[1])
    pages = {
        k.strip(): int(v) for k, v in re.findall(r"^([^:\n]+):\s+(\d+)\.", vm, re.M)
    }
    swap = command(["sysctl", "vm.swapusage"])
    match = re.search(r"used\s*=\s*([\d.]+)([MG])", swap)
    if not match:
        raise ValueError("无法读取宿主交换用量")
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "physical_memory_bytes": int(command(["sysctl", "-n", "hw.memsize"])),
        "physical_cpu_count": int(command(["sysctl", "-n", "hw.physicalcpu"])),
        "logical_cpu_count": int(command(["sysctl", "-n", "hw.logicalcpu"])),
        "swap_used_bytes": round(
            float(match[1]) * (1024 ** (2 if match[2] == "M" else 3))
        ),
        "free_pages_bytes": pages.get("Pages free", 0) * size,
        "compressed_resident_bytes": pages.get("Pages occupied by compressor", 0)
        * size,
        "swapins_pages": pages.get("Swapins"),
        "swapouts_pages": pages.get("Swapouts"),
        "memory_pressure_query": command(["memory_pressure", "-Q"]).strip(),
        "note": "Free pages are not available RAM; pressure free-percentage is not a physical free-byte measurement. No RSS/guest/VM totals are added together.",
    }


def service_sample(profile: dict) -> dict:
    """Estimate guest working memory plus verified host process trees once each.

    Guest usage is not summed with the host VM RSS. VM/kernel overhead remains
    outside this estimate and needs reserved host headroom. This is not cgroup
    enforcement of an aggregate limit.
    """
    names = profile["service_containers"]
    configured = json.loads(command(["container", "ls", "--all", "--format", "json"]))
    running = {r["id"] for r in configured if r["status"]["state"] == "running"}
    missing = sorted(set(names) - running)
    if missing:
        raise ValueError("本地服务未全部运行，不能以零占用通过资源门禁")
    stats = json.loads(
        command(["container", "stats", "--no-stream", "--format", "json", *names])
    )
    by_name = {r["id"]: r for r in stats}
    if any(
        n not in by_name or not isinstance(by_name[n].get("memoryUsageBytes"), int)
        for n in names
    ):
        raise ValueError("容器资源采集不完整")
    guest = sum(by_name[n]["memoryUsageBytes"] for n in names)
    processes = {}
    for line in command(["ps", "-axo", "pid,ppid,rss"]).splitlines()[1:]:
        pid, parent, rss = map(int, line.split())
        processes[pid] = (parent, rss * 1024)
    counted = set()
    roots = {}
    for service in profile["host_services"]:
        pid = int((ROOT / service["pid_file"]).read_text())
        args = command(["ps", "-p", str(pid), "-o", "args="])
        cwd = command(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        expected = str((ROOT / service["expected_cwd"]).resolve())
        if (
            service["command_marker"] not in args
            or "n" + expected not in cwd.splitlines()
        ):
            raise ValueError("宿主服务 PID 已过期或身份不符，不计入已验证服务")
        roots[service["name"]] = pid
        descendants = {pid}
        while True:
            new = {p for p, (parent, _) in processes.items() if parent in descendants}
            if new <= descendants:
                break
            descendants |= new
        counted |= descendants
    host = sum(processes[p][1] for p in counted if p in processes)
    return {
        "guest_working_bytes": guest,
        "verified_host_tree_rss_bytes": host,
        "accounted_service_bytes": guest + host,
        "host_service_roots": roots,
        "counted_host_processes": len(counted),
        "accounting": "guest_usage_plus_host_app_tree_rss_excluding_host_VM_rss; overhead_reserved_separately; monitored_not_hard_quota",
    }


def inventory() -> dict:
    """Read actual container limits and states, explicitly retaining unavailable data."""
    result = {"host": host_sample(), "containers": [], "container_error": None}
    try:
        values = json.loads(command(["container", "ls", "--all", "--format", "json"]))
        for value in values:
            config = value["configuration"]
            result["containers"].append(
                {
                    "name": value["id"],
                    "state": value["status"]["state"],
                    "cpu_limit": config["resources"].get("cpus"),
                    "memory_limit_bytes": config["resources"].get("memoryInBytes"),
                    "image": config["image"]["reference"],
                }
            )
    except (subprocess.SubprocessError, ValueError, KeyError) as exc:
        result["container_error"] = type(exc).__name__
    limits = [
        r["memory_limit_bytes"] for r in result["containers"] if r["name"] != "buildkit"
    ]
    result["configured_container_limits_total_bytes"] = (
        sum(limits) if limits and all(isinstance(x, int) for x in limits) else None
    )
    result["running_container_count"] = (
        sum(r["state"] == "running" for r in result["containers"])
        if not result["container_error"]
        else None
    )
    result["service_idle_usage_bytes"] = None
    result["ten_gib_group_hard_limit_verified"] = False
    result["limit_note"] = (
        "Sum of per-container ceilings, not actual usage or host reservation; host processes and VM overhead are not included. Idle usage not measured with stopped services."
    )
    return result


def main() -> None:
    """Save a sanitized, read-only local inventory to a new file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = inventory()
    with args.out.open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    print(  # noqa: T201 - sanitized CLI summary
        json.dumps(
            {
                k: data[k]
                for k in [
                    "running_container_count",
                    "configured_container_limits_total_bytes",
                    "ten_gib_group_hard_limit_verified",
                ]
            }
        )
    )  # noqa: T201


if __name__ == "__main__":
    main()
