# ruff: noqa: D101, D102, D103, D107, T201
"""Merge sequential Q1-Q3 rows, parallel Q4 history, and Q4 measured rows."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.product_chat_perf.run_baseline import (
    ResourceSampler,
    _collect_calls,
    _git_state,
)

SCHEMA = "basic-qa-perf.v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--q1q3", type=Path, required=True)
    parser.add_argument("--q4-dir", type=Path, required=True)
    parser.add_argument("--q1q3-answers", type=Path, required=True)
    parser.add_argument("--q4-answers", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--containers", nargs="+", required=True)
    parser.add_argument("--answers-out", type=Path, required=True)
    args = parser.parse_args(argv)
    q1q3 = read_jsonl(args.q1q3)
    q4_measured = read_jsonl(args.q4_dir / "q4-measured.jsonl")
    q4_history = read_jsonl(args.q4_dir / "q4-history.jsonl")
    q4_meta = json.loads((args.q4_dir / "q4-meta.json").read_text())
    rows = q1q3 + q4_measured + q4_history
    rows.sort(key=lambda row: str(row.get("planned_at") or ""))
    answers = read_jsonl(args.q1q3_answers) + read_jsonl(args.q4_answers)
    seen = set()
    unique_answers = []
    for item in answers:
        key = item.get("sample_id")
        if key in seen:
            continue
        seen.add(key)
        unique_answers.append(item)
    attempt_map = {
        str(row.get("attempt_id")): str(row.get("sample_id"))
        for row in rows
        if row.get("attempt_id")
    }
    started = min(str(row.get("planned_at")) for row in rows if row.get("planned_at"))
    ended = q4_meta["ended_at"]
    calls = _collect_calls(started, attempt_map)
    # Post-measurement idle snapshot only: the sequential runner was aborted
    # before it could write its Q1-Q3 resource interval.
    sampler = ResourceSampler(args.containers, interval=3.0)
    sampler.sample()
    sampler.start()
    time.sleep(8)
    sampler.stop()
    resources = sampler.rows
    for row in resources:
        row["collection_scope"] = "post-measurement idle; Q1-Q3 interval not sampled"
    args.out_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(args.out_dir / "requests.jsonl", rows)
    write_jsonl(args.out_dir / "calls.jsonl", calls)
    write_jsonl(args.out_dir / "resources.jsonl", resources)
    write_jsonl(args.out_dir / "history.jsonl", q4_history)
    args.answers_out.parent.mkdir(parents=True, exist_ok=True)
    args.answers_out.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in unique_answers)
    )
    manifest = {
        "schema_version": SCHEMA,
        "run_id": args.out_dir.name,
        "campaign_id": "product-chat-perf-" + args.out_dir.name,
        "recorded_at": datetime.now(UTC).isoformat(),
        "layer": "E",
        "profile": "S",
        "stage": "end-to-end-baseline",
        "git": _git_state(),
        "dataset": {
            "version": "bqa-reference-202605-v1",
            "measured_questions": 40,
            "history_user_turns": 50,
            "selection_mode": "automatic",
            "model_mode": "fast",
        },
        "runtime": {
            "bff": "http://127.0.0.1:25448",
            "admin_api": "http://127.0.0.1:25446",
            "tenant_id": "6c5504f0-2e31-4373-91e0-ec7b95f40971",
            "context_version": 2,
            "containers": args.containers,
            "resource_limits": "每个容器 4 CPU / 1 GiB（apple container 运行参数）",
            "langgraph_image": "air-agent/langgraph-api:tracing-merge-20261020",
            "model_requested": "hy3",
            "model_actual": "hy3",
            "provider": "openai-compatible gateway (127.0.0.1:7863 via bridge)",
            "mcp_source": "ipp shared_readonly real MCP (https://www.mapairs.com/product/datacenter/api2/mcp)",
        },
        "strategy": {
            "concurrency_measured": 1,
            "rounds": 1,
            "planned_measured": 40,
            "planned_history": 50,
            "deadline_seconds": {"Q1": 60, "Q2": 120, "Q3": 180, "Q4": 180},
            "budget_seconds": 5400,
            "started_at": started,
            "ended_at": ended,
            "started_at_shanghai": "",
            "ended_at_shanghai": "",
            "history_preload": {
                "concurrency": 10,
                "turns": 50,
                "note": "setup only; not part of measured load and not a capacity claim",
            },
            "segments": [
                "Q1-Q3 measured sequential (30) in run 20261021-201100-e2e3",
                "Q4 history preloaded 10-way parallel (50 user turns)",
                "Q4 measured sequential (10) after 10s settle",
            ],
            "thresholds": "首轮基线未冻结绝对 SLO；P95 为探索值",
        },
        "measurement": {
            "client_first_answer_ms": "BFF 提交前至首个可见 AI 正文增量；排除历史消息、工具、推理",
            "client_complete_ms": "BFF 提交前至流完整结束；超过冻结截止记晚到",
            "browser": "未执行；接口值不得当作浏览器首屏",
            "server_observation": "agent_model_observations / agent_tool_call_attempts",
            "resources": "仅测量后空闲 8 秒快照；Q1-Q3 测量窗未采集",
            "history_preload": "历史轮为准备步骤，不计入 40 题分母；并发仅用于缩短准备时间",
        },
        "known_limitations": [
            "顺序执行器在 Q4 历史阶段被中止；Q1-Q3 资源采样缺失",
            "Q4 历史使用 10 路并发预置，不能与单并发测量窗比较",
            "无价格表，成本未采集",
            "未执行浏览器、R/F、隔离与故障注入",
            "合成 gold 与真实 MCP 数据不可比，质量检查为结构/语义规则",
        ],
        "allow_real_models": True,
        "allow_load": False,
        "conclusion": "BASELINE_CANDIDATE（含已知依赖缺失与晚到样本）",
        "browser": "未执行",
        "targets": ["http://127.0.0.1:25448", "http://127.0.0.1:25446"],
    }
    # local Shanghai display
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    manifest["strategy"]["started_at_shanghai"] = (
        datetime.fromisoformat(started).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    )
    manifest["strategy"]["ended_at_shanghai"] = (
        datetime.fromisoformat(ended).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    )
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"rows": len(rows), "answers": len(unique_answers), "calls": len(calls)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
