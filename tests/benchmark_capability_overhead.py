"""实测本地保护开销（至少 35 样本 × 2 轮 + 5 次预热）、全路径受理开销及目录故障上界.

验收项 A24 / 任务 7.2 / 审计项 R10:
- 本地保护开销至少 35 样本 × 2 轮 (P50/P95/Mean/Delta P95)
- 全路径受理与调用保护真实开销（含 HTTP、身份上下文、材料校验、v2 摘要比对、原子持久化与事实发射）
- 实测故障上界与错误率（无常量填充）
- 输出结果至 docs/reviews/evidence/flash-05-repair-01/overhead-benchmark.json
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import dotenv

dotenv.load_dotenv()

os.environ.setdefault("MCP_SERVER_URL", "http://127.0.0.1:8000/sse")
os.environ.setdefault("MCP_SERVER_NAME", "ipp")
os.environ.setdefault(
    "DATABASE_URI", "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"
)

from common.capabilities import (
    discover_authorized_mcp_catalog,
    filter_capability_mcp_tools,
    list_graph_skills,
    validate_skill_selection,
)


def measure_times(fn, count: int, warmup: int = 5) -> tuple[float, float, float, list[float]]:
    for _ in range(warmup):
        fn()
    durations: list[float] = []
    for _ in range(count):
        t0 = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - t0) * 1000.0)  # ms
    durations.sort()
    p50 = statistics.median(durations)
    p95_idx = int(0.95 * len(durations))
    p95 = durations[p95_idx]
    mean = statistics.mean(durations)
    return p50, p95, mean, durations


async def test_directory_fault_bounds():
    """实测单个服务超时和总目录超时的降级与限时上界."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_factory = MagicMock()

    async def slow_tools(identity, server_key):
        if server_key == "slow_server":
            await asyncio.sleep(1.0)
            return []
        return [MagicMock(name="fast_tool", display_name="快速工具", description="快速工具描述")]

    mock_factory.tools_for = AsyncMock(side_effect=slow_tools)
    mock_factory._urls = {"fast_server": "http://mock/fast", "slow_server": "http://mock/slow"}

    mock_identity = MagicMock()
    mock_identity.tenant_id = "t1"
    mock_identity.membership_id = "m1"

    bindings = [
        {"server_key": "fast_server", "protocol_version": "v1-fixture", "status": "active"},
        {"server_key": "slow_server", "protocol_version": "v1-fixture", "status": "active"},
    ]

    t0 = time.perf_counter()
    with (
        patch("common.mcp_scoped.get_factory", return_value=mock_factory),
        patch("common.capabilities.fetch_active_mcp_bindings", AsyncMock(return_value=bindings)),
        patch("common.mcp_authorization.fixture_mode_enabled", return_value=True),
    ):
        services, tools = await discover_authorized_mcp_catalog(
            mock_identity,
            timeout_per_server=0.2,
            total_timeout=0.5,
        )
    elapsed = time.perf_counter() - t0

    slow_svc = next((s for s in services if s.server_key == "slow_server"), None)
    fast_svc = next((s for s in services if s.server_key == "fast_server"), None)

    return {
        "configured_single_timeout_sec": 0.2,
        "configured_total_timeout_sec": 0.5,
        "actual_elapsed_sec": round(elapsed, 4),
        "slow_server_degraded_status": slow_svc.status if slow_svc else None,
        "fast_server_status": fast_svc.status if fast_svc else None,
        "harvested_tools_count": len(tools),
        "upper_bound_maintained": elapsed <= 0.6,
    }


def run_full_benchmark():
    # 1. Local helpers benchmark
    # Round 1
    p50_auto_1, p95_auto_1, mean_auto_1, _ = measure_times(
        lambda: list_graph_skills("basic-qa"), 35, 5
    )
    p50_exp_1, p95_exp_1, mean_exp_1, _ = measure_times(
        lambda: validate_skill_selection(
            "basic-qa", "skill:basic-qa:air-quality-basic-query"
        ),
        35,
        5,
    )

    # Round 2
    p50_auto_2, p95_auto_2, mean_auto_2, _ = measure_times(
        lambda: list_graph_skills("basic-qa"), 35, 5
    )
    p50_exp_2, p95_exp_2, mean_exp_2, _ = measure_times(
        lambda: validate_skill_selection(
            "basic-qa", "skill:basic-qa:air-quality-basic-query"
        ),
        35,
        5,
    )

    class DummyTool:
        def __init__(self, name: str):
            self.name = name

    all_tools = [DummyTool(f"tool_{i}") for i in range(20)]
    cfg = {
        "configurable": {
            "capability_mcp_mode": "allowlist",
            "capability_tool_ids": ["tool_1", "tool_5", "tool_10"],
        }
    }
    p50_filter_1, p95_filter_1, mean_filter_1, _ = measure_times(
        lambda: filter_capability_mcp_tools(all_tools, cfg), 35, 5
    )
    p50_filter_2, p95_filter_2, mean_filter_2, _ = measure_times(
        lambda: filter_capability_mcp_tools(all_tools, cfg), 35, 5
    )

    delta_p95_r1 = p95_exp_1 - p95_auto_1
    delta_p95_r2 = p95_exp_2 - p95_auto_2

    # 2. 实测目录故障上界与限时
    fault_bounds_result = asyncio.run(test_directory_fault_bounds())

    # 3. 运行全路径受理真实开销测试（调用 admin/api 中的基准）
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [
        str(repo_root / "admin" / "api" / ".venv" / "bin" / "python"),
        str(repo_root / "admin" / "api" / "tests" / "benchmark_admission_overhead.py"),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{repo_root / 'src'}:{repo_root / 'tests'}:"
        f"{repo_root / 'admin' / 'api'}:{repo_root / 'admin' / 'api' / 'tests'}"
    )
    env["DATABASE_URI"] = "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"
    env["ADMIN_RUNTIME_DATABASE_URI"] = "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"
    env["RUNTIME_DATABASE_URI"] = "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"
    proc = subprocess.run(cmd, cwd=str(repo_root), env=env, capture_output=True, text=True, check=True)
    admission_output = json.loads(proc.stdout)

    result = {
        "check": "capability selection local protection & admission full-path overhead",
        "benchmark_environment": {
            "os": "macOS",
            "python_agent_runtime": sys.version.split()[0],
            "database": "PostgreSQL 17-alpine (aa-sel05-pg on 15434)",
            "sample_size_per_round": 35,
            "rounds": 2,
            "warmup_runs": 5,
        },
        "threshold": "本地及受理保护新增 P95 ≤ 50ms",
        "external_wait_seconds": 0.0,
        "local_helper_overhead": {
            "description": "本地内存/文件层 helper 开销（list_graph_skills, validate_skill_selection, filter_capability_mcp_tools）",
            "rounds": [
                {
                    "round": 1,
                    "auto_baseline_ms": {
                        "samples": 35,
                        "p50": round(p50_auto_1, 4),
                        "p95": round(p95_auto_1, 4),
                        "mean": round(mean_auto_1, 4),
                    },
                    "explicit_protected_ms": {
                        "samples": 35,
                        "p50": round(p50_exp_1, 4),
                        "p95": round(p95_exp_1, 4),
                        "mean": round(mean_exp_1, 4),
                    },
                    "tool_filtering_ms": {
                        "samples": 35,
                        "p50": round(p50_filter_1, 4),
                        "p95": round(p95_filter_1, 4),
                        "mean": round(mean_filter_1, 4),
                    },
                    "delta_p95_ms": round(delta_p95_r1, 4),
                },
                {
                    "round": 2,
                    "auto_baseline_ms": {
                        "samples": 35,
                        "p50": round(p50_auto_2, 4),
                        "p95": round(p95_auto_2, 4),
                        "mean": round(mean_auto_2, 4),
                    },
                    "explicit_protected_ms": {
                        "samples": 35,
                        "p50": round(p50_exp_2, 4),
                        "p95": round(p95_exp_2, 4),
                        "mean": round(mean_exp_2, 4),
                    },
                    "tool_filtering_ms": {
                        "samples": 35,
                        "p50": round(p50_filter_2, 4),
                        "p95": round(p95_filter_2, 4),
                        "mean": round(mean_filter_2, 4),
                    },
                    "delta_p95_ms": round(delta_p95_r2, 4),
                },
            ],
            "max_delta_p95_ms": round(max(delta_p95_r1, delta_p95_r2), 4),
            "passed": max(delta_p95_r1, delta_p95_r2) <= 50.0,
        },
        "admission_full_path_benchmark": admission_output["admission_full_path_benchmark"],
        "directory_fault_bounds_measured": fault_bounds_result,
        "real_collected_reliability": admission_output["real_collected_reliability"],
        "overall_verdict": {
            "local_helper_passed": max(delta_p95_r1, delta_p95_r2) <= 50.0,
            "admission_full_path_passed": admission_output["admission_full_path_benchmark"]["passed"],
            "fault_bounds_passed": fault_bounds_result["upper_bound_maintained"],
            "fault_intercept_rate_pct": admission_output["real_collected_reliability"]["actual_fault_intercept_rate_pct"],
            "overall_passed": (
                max(delta_p95_r1, delta_p95_r2) <= 50.0
                and admission_output["admission_full_path_benchmark"]["passed"]
                and fault_bounds_result["upper_bound_maintained"]
            ),
        },
    }

    out_dir = Path("docs/reviews/evidence/flash-05-repair-02")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "overhead-benchmark.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Written benchmark to {out_file}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_full_benchmark()
