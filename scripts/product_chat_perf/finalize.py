# ruff: noqa: D101, D102, D103, D107, T201
"""Finalize a product-chat baseline bundle with deadline-aware results.

The live runner records raw stream completion.  This step applies the frozen
per-question deadlines, rebuilds checks from restricted answer text, and
rewrites summary/report without changing the raw rows.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.product_chat_perf.run_baseline import (
    Q1_TERMS,
    _call_stats,
    _resource_stats,
    _tool_stats,
)
from scripts.unified_campaign.records import percentile_cont

SCHEMA = "basic-qa-perf.v1"
ROOT = Path(__file__).resolve().parents[2]

_TRANSLATE = str.maketrans(
    {
        "₂": "2",
        "₃": "3",
        "₅": "5",
        "₀": "0",
        "₁": "1",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
    }
)

CASE_GROUPS: dict[str, list[list[str]]] = {
    "BQA-011": [["洛阳"], ["AQI"], ["良", "等级", "污染"]],
    "BQA-012": [["洛阳"], ["PM2.5"], ["PM10"], ["CO"]],
    "BQA-013": [["洛阳"], ["郑州"], ["AQI"]],
    "BQA-014": [["洛阳"], ["AQI"], ["15时", "15:00", "15 时"]],
    "BQA-015": [["洛阳"], ["PM2.5"], ["排名", "第"]],
    "BQA-016": [["洛阳"], ["国控"], ["站"]],
    "BQA-017": [["洛阳"], ["站"], ["AQI"]],
    "BQA-018": [["O3"], ["洛阳"], ["郑州"]],
    "BQA-019": [["洛阳"], ["PM2.5"], ["累计"], ["剩余"]],
    "BQA-020": [["洛阳"], ["PM2.5"], ["峰值"], ["小时"]],
    "BQA-021": [["洛阳"], ["综合指数"], ["PM2.5"]],
    "BQA-022": [["洛阳"], ["优良"], ["CO", "臭氧"]],
    "BQA-023": [["洛阳"], ["占比", "贡献"], ["PM2.5"]],
    "BQA-024": [["洛阳"], ["郑州"], ["贡献", "占比"]],
    "BQA-025": [["洛阳"], ["PM2.5"], ["峰值"], ["均值"]],
    "BQA-026": [["郑州"], ["PM2.5"], ["趋势"]],
    "BQA-027": [["洛阳"], ["郑州"], ["综合指数"]],
    "BQA-028": [["洛阳"], ["郑州"], ["PM2.5"], ["小时"]],
    "BQA-029": [["洛阳"], ["国控"], ["省控"]],
    "BQA-030": [["郑州"], ["PM2.5"], ["重污染"]],
    "BQA-031": [["郑州"], ["AQI"], ["首要污染物"]],
    "BQA-032": [["洛阳"], ["PM10"], ["排名"]],
    "BQA-033": [["洛阳"], ["优良"], ["增加"]],
    "BQA-034": [["洛阳"], ["郑州"], ["综合指数"]],
    "BQA-035": [["洛阳"], ["PM2.5"], ["均值"], ["峰值"]],
    "BQA-036": [["郑州"], ["PM2.5"], ["峰值"]],
    "BQA-037": [["洛阳"], ["O3"], ["占比", "贡献"]],
    "BQA-038": [["洛阳"], ["PM2.5"], ["剩余"]],
    "BQA-039": [["洛阳"], ["国控"], ["AQI"]],
    "BQA-040": [["洛阳"], ["郑州"], ["AQI"]],
}


def _normalize(text: str) -> str:
    return text.translate(_TRANSLATE).replace(" ", "")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _quality(
    category: str, case_id: str, answer: str, tools: int
) -> tuple[bool, list[str]]:
    text = _normalize(answer or "")
    if not text.strip():
        return False, ["empty_answer"]
    reasons: list[str] = []
    refusal_markers = (
        "暂时不可用",
        "无法查询",
        "查询失败",
        "服务不可用",
        "抱歉，当前",
        "未取得可核验",
        "授权不允许",
        "403",
    )
    if any(_normalize(marker) in text for marker in refusal_markers):
        # A long answer may quote a limitation after giving substantive data;
        # only short or leading refusals are counted as failed answers.
        if len(text) < 300 or any(
            text.startswith(_normalize(marker)) for marker in refusal_markers
        ):
            reasons.append("failure_marker")
    if category == "Q1":
        for group in Q1_TERMS.get(case_id, []):
            if not any(_normalize(term) in text for term in group):
                reasons.append("missing:" + "/".join(group))
    else:
        for group in CASE_GROUPS.get(case_id, []):
            if category in {"Q3", "Q4"} and set(group) <= {"洛阳", "郑州"}:
                continue  # city is often implicit in a continued/analysis answer
            if not any(_normalize(term) in text for term in group):
                reasons.append("missing:" + "/".join(group))
        if category in {"Q2", "Q3"} and tools == 0:
            reasons.append("no_tool_call")
        if not any(ch.isdigit() for ch in text):
            reasons.append("no_number")
    return not reasons, reasons


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timely = [
        row
        for row in rows
        if row.get("timely_success") and row.get("client_complete_ms") is not None
    ]
    late = [
        row
        for row in rows
        if row.get("late_attainment") and row.get("client_complete_ms") is not None
    ]
    complete = [float(row["client_complete_ms"]) for row in timely]
    first = [
        float(row["client_first_answer_ms"])
        for row in timely
        if row.get("client_first_answer_ms") is not None
    ]
    headline = Counter(row.get("headline_result") for row in rows)
    return {
        "planned": len(rows),
        "sent": len([row for row in rows if row.get("sent")]),
        "completed_success": len(timely),
        "late_attainment": len(late),
        "late_cases": [row.get("case_id") for row in late],
        "headline_outcomes": dict(headline),
        "failures": {
            key: value for key, value in headline.items() if key != "completed_success"
        },
        "first_answer_p50_ms": percentile_cont(first, 0.50),
        "first_answer_p95_ms": percentile_cont(first, 0.95),
        "complete_p50_ms": percentile_cont(complete, 0.50),
        "complete_p95_ms": percentile_cont(complete, 0.95),
        "complete_min_ms": min(complete) if complete else None,
        "complete_max_ms": max(complete) if complete else None,
        "first_answer_samples": len(first),
    }


def _report(
    summary: dict[str, Any], manifest: dict[str, Any], checks: list[dict[str, Any]]
) -> str:
    overall = summary["overall"]
    slowest = summary["slowest"]
    runtime = manifest.get("runtime", {})
    strategy = manifest.get("strategy", {})
    lines = [
        f"# basic QA 真实聊天性能基线报告：{summary['run_id']}",
        "",
        "规范：[basic QA 并发性能回归规范](../../../testing/basic-qa-performance-regression.md)。",
        "",
        "## 1. 结论与范围",
        "",
        "| 项目 | 本轮结果 |",
        "| --- | --- |",
        f"| run_id / 规范版本 / schema | {summary['run_id']} / v1.2 / basic-qa-perf.v1 |",
        "| 档位 | E / S（单用户、单并发、40 题各一次；非 R/F） |",
        f"| 声明范围内结论 | {summary['conclusion']} |",
        f"| 测试时间、时区 | {strategy.get('started_at_shanghai', '')} — {strategy.get('ended_at_shanghai', '')}（Asia/Shanghai；记录 UTC） |",
        f"| 计划/发送/按时完成成功 | {summary['n_planned_measured']} / {summary['n_sent_measured']} / {overall['completed_success']} |",
        f"| 晚到但完成（不计按时成功） | {overall['late_attainment']}：{json.dumps(overall['late_cases'], ensure_ascii=False)} |",
        f"| 质量/结构检查通过 | {summary['quality_pass']}/{summary['quality_pass_denominator']} |",
        f"| 真实模型 | {runtime.get('model_actual', '未采集')}（配置请求 {runtime.get('model_requested', '未采集')}；经本机 OpenAI 兼容网关） |",
        f"| MCP | {runtime.get('mcp_source', '未采集')} |",
        "| 浏览器 | 未执行；接口首正文不能替代浏览器首屏 |",
        "",
        "一句话结论：普通用户在本轮单用户、单并发、真实模型和真实共享 MCP 下，"
        f"按时完成 {overall['completed_success']}/{summary['n_sent_measured']} 题；"
        f"按时成功样本的接口首正文 P50/P95 为 {overall['first_answer_p50_ms']}/{overall['first_answer_p95_ms']} ms，"
        f"完整回答 P50/P95 为 {overall['complete_p50_ms']}/{overall['complete_p95_ms']} ms。"
        "小样本 P95 只是探索值，不是稳定 SLA 或容量承诺。",
        "",
        "## 2. 请求守恒与延迟",
        "",
        "| 分类 | 计划/发送 | 按时成功 | 晚到 | 首正文 P50/P95 (ms) | 完整 P50/P95 (ms) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for category in ("Q1", "Q2", "Q3", "Q4"):
        item = summary["by_category"][category]
        lines.append(
            f"| {category} | {item['planned']}/{item['sent']} | {item['completed_success']} | "
            f"{item['late_attainment']} | {item['first_answer_p50_ms']}/{item['first_answer_p95_ms']} | "
            f"{item['complete_p50_ms']}/{item['complete_p95_ms']} |"
        )
    lines += [
        "",
        "超时/失败分布："
        + json.dumps(
            {
                k: v
                for k, v in overall["headline_outcomes"].items()
                if k != "completed_success"
            },
            ensure_ascii=False,
        )
        + "。晚到成功保留实际完成耗时，但不回填按时成功；截止值不冒充完成耗时。",
        "",
        "历史预置：另发送 "
        f"{summary['n_sent_history']} 条 Q4 历史用户轮，主结果 "
        f"{json.dumps(summary['history_outcomes'], ensure_ascii=False)}；"
        "不计入 40 题分母，但保留在原始请求中。",
        "",
        "最慢的按时成功/晚到题目：",
        "",
        "| 题号 | 分类 | 首正文 ms | 完成 ms | 截止 s | 结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in slowest:
        lines.append(
            f"| {row['case_id']} | {row['category']} | {row['client_first_answer_ms']} | "
            f"{row['client_complete_ms']} | {row['deadline_seconds']} | {row['headline_result']} |"
        )
    lines += [
        "",
        "| 分类 | 质量通过/发送 | 未通过原因（摘要） |",
        "| --- | --- | --- |",
    ]
    quality_reasons = summary.get("quality_reasons_by_category", {})
    for category in ("Q1", "Q2", "Q3", "Q4"):
        item = summary["by_category"][category]
        reasons = quality_reasons.get(category, {})
        lines.append(
            f"| {category} | {item.get('quality_pass', 0)}/{item['sent']} | "
            f"{json.dumps(reasons, ensure_ascii=False)} |"
        )
    lines += [
        "",
        "## 3. 模型、工具与用量",
        "",
        f"- 模型观测：{json.dumps(summary['model_calls'], ensure_ascii=False)}",
        f"- 工具尝试：{json.dumps(summary['tool_calls'], ensure_ascii=False)}",
        f"- Token：{json.dumps(summary['token_coverage'], ensure_ascii=False)}",
        f"- 资源：{json.dumps(summary['resources'], ensure_ascii=False)}",
        "",
        "## 4. 质量、失败与边界",
        "",
        f"- 检查项：{summary['quality_checks']['pass']}/{summary['quality_checks']['total']} PASS；"
        f"质量通过 {summary['quality_pass']}/{summary['quality_pass_denominator']}。",
        "- 合成 gold 数值与真实 MCP 数据不可比；本轮质量规则检查流完成、台账关联、"
        "必要实体/日期/指标词、工具实际调用和数字存在性，不把真实数据值判成合成 gold 错误。",
        "- Q1 的降级答复需单独看：概念题被路由到知识检索链路，当前隔离栈未提供知识库服务/授权，"
        "返回统一降级文本；这是依赖缺失与产品降级行为，不是模型知识能力结论。",
        "- 未执行：浏览器渲染、R/F 并发、开放到达、长稳、故障注入、跨用户/跨企业隔离、"
        "价格与成本（无价格表，未知不记 0）。",
        "- 回答正文仅保存在受限证据目录；性能 bundle 不含题目/回答正文。",
        "",
        "## 5. 证据与复跑",
        "",
        "证据文件：`manifest.json`、`summary.json`、`requests.jsonl`、`calls.jsonl`、"
        "`resources.jsonl`、`checks.jsonl`、`browser.jsonl`、`evidence-index.json`、"
        "`precheck.jsonl`、`aborted-runs/`。",
        "",
        "复跑命令：",
        "",
        "```bash",
        ".venv/bin/python -m scripts.product_chat_perf.run_baseline \\",
        "  --endpoints <隔离端点.json> \\",
        "  --performance-out docs/reviews/basic-qa-performance/<run_id> \\",
        "  --evidence-out .local/<受控证据目录> --enable-real-models",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run_dir = args.run_dir
    rows = _read_jsonl(run_dir / "requests.jsonl")
    if not rows:
        rows = _read_jsonl(run_dir / "requests.partial.jsonl")
    if not rows:
        raise SystemExit("没有请求记录")
    answers = {
        str(item.get("sample_id")): str(item.get("answer") or "")
        for item in _read_jsonl(args.evidence_dir / "answers.jsonl")
    }
    calls = _read_jsonl(run_dir / "calls.jsonl")
    resources = _read_jsonl(run_dir / "resources.jsonl")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    cases = {
        item["case_id"]: item
        for item in json.loads(
            (ROOT / "docs/testing/data/basic-qa-reference-cases.v1.json").read_text()
        )["cases"]
    }
    tool_counts = Counter(
        str(row.get("sample_id"))
        for row in calls
        if row.get("kind") == "tool" and row.get("status") == "succeeded"
    )
    for row in rows:
        if row.get("cohort") != "measured":
            continue
        deadline = int(row.get("deadline_seconds") or 0) * 1000
        complete = row.get("client_complete_ms")
        timely = (
            row.get("primary_result") == "stream_completed"
            and complete is not None
            and complete <= deadline
        )
        late = (
            row.get("primary_result") == "stream_completed"
            and complete is not None
            and complete > deadline
        )
        row["deadline_ms"] = deadline
        row["timely_success"] = timely
        row["late_attainment"] = late
        row["deadline_exceeded_ms"] = (
            max(0, int(complete or 0) - deadline) if complete else None
        )
        row["headline_result"] = (
            "completed_success"
            if timely
            else ("client_timeout" if late else row.get("primary_result"))
        )
        answer = answers.get(str(row.get("sample_id")), "")
        ok, reasons = _quality(
            str(row.get("category")),
            str(row.get("case_id")),
            answer,
            tool_counts.get(str(row.get("sample_id")), 0),
        )
        row["quality_pass"] = bool(ok and timely)
        row["quality_reasons"] = reasons
        if late:
            row["quality_reasons"] = list(reasons) + ["late_completion"]
        row["answer_chars"] = len(answer)
        row["answer_sha256"] = hashlib_sha(answer)
    measured = [row for row in rows if row.get("cohort") == "measured"]
    history = [row for row in rows if row.get("cohort") == "history"]
    by_category = {
        category: _stats([r for r in measured if r.get("category") == category])
        for category in ("Q1", "Q2", "Q3", "Q4")
    }
    quality_pass = len([row for row in measured if row.get("quality_pass")])
    quality_reasons_by_category: dict[str, dict[str, int]] = {}
    for category in ("Q1", "Q2", "Q3", "Q4"):
        counter: dict[str, int] = {}
        for row in measured:
            if row.get("category") != category or row.get("quality_pass"):
                continue
            for reason in row.get("quality_reasons") or []:
                counter[reason] = counter.get(reason, 0) + 1
        quality_reasons_by_category[category] = counter
    for category in ("Q1", "Q2", "Q3", "Q4"):
        by_category[category]["quality_pass"] = len(
            [
                row
                for row in measured
                if row.get("category") == category and row.get("quality_pass")
            ]
        )
    slowest = sorted(
        [row for row in measured if row.get("client_complete_ms") is not None],
        key=lambda row: int(row["client_complete_ms"]),
        reverse=True,
    )[:8]
    summary = {
        "schema_version": SCHEMA,
        "run_id": run_dir.name,
        "recorded_at": datetime.now(UTC).isoformat(),
        "layer": "E",
        "profile": "S",
        "n_planned_measured": 40,
        "n_sent_measured": len(measured),
        "n_sent_history": len(history),
        "outcomes": dict(Counter(row.get("headline_result") for row in measured)),
        "history_outcomes": dict(Counter(row.get("primary_result") for row in history)),
        "overall": _stats(measured),
        "by_category": by_category,
        "quality_pass": quality_pass,
        "quality_pass_denominator": len(measured),
        "quality_reasons_by_category": quality_reasons_by_category,
        "quality_checks": {
            "total": len(measured),
            "pass": quality_pass,
        },
        "slowest": [
            {
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "client_first_answer_ms": row.get("client_first_answer_ms"),
                "client_complete_ms": row.get("client_complete_ms"),
                "deadline_seconds": row.get("deadline_seconds"),
                "headline_result": row.get("headline_result"),
            }
            for row in slowest
        ],
        "model_calls": _call_stats(
            [row for row in calls if row.get("kind") == "model"]
        ),
        "tool_calls": _tool_stats([row for row in calls if row.get("kind") == "tool"]),
        "token_coverage": _tokens(calls),
        "resources": _resource_stats(resources),
        "p95_exploratory": True,
        "conclusion": "BASELINE_CANDIDATE（含已知依赖缺失与晚到样本）",
    }
    manifest["conclusion"] = summary["conclusion"]
    manifest["summary_status"] = {
        "headline": "BASELINE_CANDIDATE",
        "known_blocked": "Q1 knowledge dependency unavailable in isolated stack",
        "browser": "not executed",
        "cost": "not collected",
    }
    _write_jsonl(run_dir / "requests.jsonl", rows)
    checks = _checks(rows, cases, tool_counts, answers)
    _write_jsonl(run_dir / "checks.jsonl", checks)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (run_dir / "report.md").write_text(_report(summary, manifest, checks))
    print(
        json.dumps(
            {
                "run_id": summary["run_id"],
                "sent": len(measured),
                "history": len(history),
                "quality": quality_pass,
                "timely": summary["overall"]["completed_success"],
                "late": summary["overall"]["late_attainment"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def hashlib_sha(text: str) -> str | None:
    if not text:
        return None
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def _tokens(calls: list[dict[str, Any]]) -> dict[str, Any]:
    models = [row for row in calls if row.get("kind") == "model"]
    usage = [row for row in models if row.get("usage_status") == "provided"]
    return {
        "model_observations": len(models),
        "usage_provided": len(usage),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in usage),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in usage),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in usage),
        "missing_reason": None
        if len(usage) == len(models)
        else "部分尝试未返回 usage 或未采集",
        "price": None,
        "cost": None,
        "cost_note": "未提供价格表，未知价格不记 0",
    }


def _checks(rows, cases, tool_counts, answers) -> list[dict[str, Any]]:
    checks = []
    for row in rows:
        if row.get("cohort") != "measured":
            continue
        sample_id = str(row.get("sample_id"))
        checks.append(
            {
                "schema_version": SCHEMA,
                "check_id": f"{sample_id}:stream",
                "sample_id": sample_id,
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "check_kind": "stream",
                "expected": "stream completed within deadline with non-empty answer",
                "actual": {
                    "headline_result": row.get("headline_result"),
                    "client_complete_ms": row.get("client_complete_ms"),
                    "deadline_ms": row.get("deadline_ms"),
                },
                "status": "PASS"
                if row.get("timely_success") and row.get("answer_nonempty")
                else "FAIL",
                "rule_version": "e2e-baseline-v1",
            }
        )
        checks.append(
            {
                "schema_version": SCHEMA,
                "check_id": f"{sample_id}:association",
                "sample_id": sample_id,
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "check_kind": "association",
                "expected": "ledger request/attempt/submission and platform run associated",
                "actual": {
                    "answer_associated": row.get("answer_associated"),
                    "association_status": row.get("association_status"),
                },
                "status": "PASS" if row.get("answer_associated") else "FAIL",
                "rule_version": "e2e-baseline-v1",
            }
        )
        checks.append(
            {
                "schema_version": SCHEMA,
                "check_id": f"{sample_id}:answer",
                "sample_id": sample_id,
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "check_kind": "answer",
                "expected": "structural/semantic checks against real data",
                "actual": {
                    "pass": row.get("quality_pass"),
                    "reasons": row.get("quality_reasons"),
                    "answer_chars": row.get("answer_chars"),
                    "answer_sha256": row.get("answer_sha256"),
                },
                "status": "PASS" if row.get("quality_pass") else "FAIL",
                "evidence_ref": "restricted:answers.jsonl",
                "rule_version": "e2e-baseline-v1",
            }
        )
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
