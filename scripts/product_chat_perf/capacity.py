"""Explicit U/P/E capacity plans, actual scheduling and reproducible v2 reports.

``validate`` is network-free. ``run`` requires explicit real-model/load flags;
the endpoint plan must independently enable them. Each stage produces raw
records and a verdict, and missing phases never become a capacity PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.product_chat_perf.gateway import send_gateway_sample
from scripts.product_chat_perf.plan import (
    BUNDLE_SCHEMA,
    PlanError,
    digest,
    load_plan,
    public_plan,
    validate_plan,
)
from scripts.product_chat_perf.quality import assess_answer
from scripts.product_chat_perf.run_baseline import (
    ResourceSampler,
    _collect_calls,
    _git_state,
)
from scripts.product_chat_perf.scheduling import Budget, run_stage
from scripts.unified_campaign.dataset import load_plan as load_cases
from scripts.unified_campaign.records import percentile_cont
from scripts.unified_campaign.sender import send_sample


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def summarize_stage(stage: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Derive a verdict from all sends and actual windows, not success alone."""
    rows = result["rows"]
    success = [
        row
        for row in rows
        if row.get("primary_result") in {"stream_completed", "completed_success"}
    ]
    quality = [row for row in success if row.get("quality_status") == "PASS"]
    first = [
        row["client_first_answer_ms"]
        for row in success
        if isinstance(row.get("client_first_answer_ms"), (float, int))
        and (stage["layer"] == "U" or row.get("public_unit_version") == 1)
    ]
    complete = [
        row["client_complete_ms"]
        for row in success
        if isinstance(row.get("client_complete_ms"), (float, int))
    ]
    results = [
        row["client_first_verified_result_ms"]
        for row in rows
        if isinstance(row.get("client_first_verified_result_ms"), (int, float))
    ]
    gaps = [
        row["max_answer_gap_ms"]
        for row in rows
        if isinstance(row.get("max_answer_gap_ms"), (int, float))
    ]
    thresholds = stage["thresholds"]
    first_p95 = percentile_cont(first, 0.95)
    complete_p95 = percentile_cont(complete, 0.95)
    n_sent = result["sent"]
    business_success = (
        success
        if stage["layer"] == "U"
        else [row for row in success if row.get("business_valid") is True]
    )
    success_rate = len(business_success) / n_sent if n_sent else None
    quality_rate = len(quality) / n_sent if n_sent else None
    per_workload = Counter(
        row.get("sampling_group") or row.get("workload_id") or row.get("case_id")
        for row in rows
    )
    minimum_per_workload = int(stage.get("min_samples_per_workload", 0))
    expected_workloads = {
        item.get("sampling_group") or item["workload_id"] for item in stage["workloads"]
    }
    reasons: list[str] = []
    if result["stop_reason"]:
        reasons.append(str(result["stop_reason"]))
    if (
        result["measure_seconds"] < stage["duration_seconds"]
        or n_sent < stage["min_samples"]
    ):
        reasons.append("duration_or_sample_floor_unmet")
    if any(
        per_workload.get(key, 0) < minimum_per_workload for key in expected_workloads
    ):
        reasons.append("workload_sample_floor_unmet")
    if result["not_sent"]:
        reasons.append("planned_arrivals_not_sent")
    if success_rate is None or success_rate < thresholds["min_success_rate"]:
        reasons.append("success_rate_below_target")
    if quality_rate is None or quality_rate < thresholds["min_quality_rate"]:
        reasons.append("quality_rate_below_target")
    if len(first) != len(success) or first_p95 is None:
        reasons.append("first_answer_measurement_missing")
    elif first_p95 > thresholds["first_answer_p95_ms"]:
        reasons.append("first_answer_p95_above_target")
    if complete_p95 is None or complete_p95 > thresholds["complete_p95_ms"]:
        reasons.append("complete_p95_above_target")
    blocked = any(row.get("quality_status") == "BLOCKED" for row in rows)
    verdict = (
        "BLOCKED"
        if blocked
        else "PARTIAL"
        if result["stop_reason"]
        in {"request_budget", "time_budget", "token_budget", "sample_pool_exhausted"}
        else "FAIL"
        if reasons
        else "PASS"
    )
    measurement_success = sum(
        row.get("completion_window") == "measurement" for row in success
    )
    return {
        "stage_id": stage["stage_id"],
        "layer": stage["layer"],
        "model": stage["model"],
        "graph_profile": stage.get("graph_profile"),
        "phase": stage["phase"],
        "arrival": stage["arrival"],
        "concurrency": stage["concurrency"],
        "arrival_rate_per_second": stage.get("arrival_rate_per_second"),
        "planned": result["planned"],
        "sent": n_sent,
        "not_sent": result["not_sent"],
        "outcomes": dict(Counter(row.get("primary_result") for row in rows)),
        "technical_success": len(success),
        "technical_success_rate": len(success) / n_sent if n_sent else None,
        "business_valid": len(business_success) if stage["layer"] != "U" else None,
        "quality_pass": len(quality),
        "success_rate": success_rate,
        "quality_rate": quality_rate,
        "first_answer_p50_ms": percentile_cont(first, 0.5),
        "first_answer_p95_ms": first_p95,
        "complete_p50_ms": percentile_cont(complete, 0.5),
        "complete_p95_ms": complete_p95,
        "first_answer_samples": len(first),
        "first_verified_result_samples": len(results),
        "first_verified_result_p50_ms": percentile_cont(results, 0.5),
        "first_verified_result_p95_ms": percentile_cont(results, 0.95),
        "max_answer_gap_p50_ms": percentile_cont(gaps, 0.5),
        "max_answer_gap_p95_ms": percentile_cont(gaps, 0.95),
        "answer_gap_samples": len(gaps),
        "answer_rule_versions": dict(
            Counter(
                row.get("answer_rule_version") or "upstream_not_product" for row in rows
            )
        ),
        "p95_exploratory": len(first) < 200,
        "completed_in_measurement_window": measurement_success,
        "successful_completions_per_minute": measurement_success
        / result["measure_seconds"]
        * 60
        if result["measure_seconds"]
        else None,
        "peak_inflight": result["peak_inflight"],
        "actual_measure_seconds": result["measure_seconds"],
        "total_seconds_including_drain": result["total_seconds_including_drain"],
        "verdict": verdict,
        "reasons": reasons,
        "budget": result["budget"],
    }


def capacity_verdict(
    plan: dict[str, Any], stages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep layers/models separate and never infer an untested maximum."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for stage in stages:
        key = (stage["layer"], stage["model"], stage.get("graph_profile") or "upstream")
        groups.setdefault(key, []).append(stage)
    output = []
    for (layer, model, graph), results in groups.items():
        passed = [row for row in results if row["verdict"] == "PASS"]
        closed = [row["concurrency"] for row in passed if row["arrival"] == "closed"]
        confirmed_counts = Counter(
            row["concurrency"]
            for row in passed
            if row["phase"] == "confirm" and row["arrival"] == "closed"
        )
        confirmed = [level for level, count in confirmed_counts.items() if count >= 3]
        stable = [
            row.get("arrival_rate_per_second")
            for row in passed
            if row["phase"] == "stability" and row["arrival"] == "open"
        ]
        failed = [row["concurrency"] for row in results if row["verdict"] == "FAIL"]
        expected = [
            stage
            for stage in plan["stages"]
            if (
                stage["layer"],
                stage["model"],
                stage.get("graph_profile") or "upstream",
            )
            == (layer, model, graph)
        ]
        covered = {row["stage_id"] for row in results}
        complete = (
            plan["objective"] == "capacity"
            and all(stage["stage_id"] in covered for stage in expected)
            and all(
                row["verdict"] == "PASS" or row["phase"] == "explore" for row in results
            )
        )
        # Exploration alone cannot establish the sustainable candidate. Keep
        # its best observed level separately from repeated confirmation.
        has_boundary = bool(confirmed and failed and min(failed) > max(confirmed))
        complete = complete and bool(confirmed) and bool(stable)
        output.append(
            {
                "layer": layer,
                "model": model,
                "graph_profile": graph,
                "highest_passed_closed_concurrency": max(closed) if closed else None,
                "highest_confirmed_closed_concurrency": max(confirmed)
                if confirmed
                else None,
                "first_failed_concurrency": min(failed) if failed else None,
                "sustainable_arrivals_per_second": max(stable)
                if stable and complete
                else None,
                "capacity_status": "VERIFIED_TESTED_BOUNDARY"
                if complete and has_boundary
                else "LOWER_BOUND"
                if complete
                else "EXPLORATORY"
                if plan["objective"] == "exploration"
                else "PARTIAL",
                "maximum_capacity_claim": False,
                "independent_acceptance_required": True,
                "unexecuted_stage_ids": [
                    stage["stage_id"]
                    for stage in expected
                    if stage["stage_id"] not in covered
                ],
            }
        )
    planned_keys = {
        (stage["layer"], stage["model"], stage.get("graph_profile") or "upstream")
        for stage in plan["stages"]
    }
    for layer, model, graph in planned_keys - set(groups):
        output.append(
            {
                "layer": layer,
                "model": model,
                "graph_profile": graph,
                "capacity_status": "BLOCKED",
                "highest_passed_closed_concurrency": None,
                "maximum_capacity_claim": False,
            }
        )
    return {
        "schema_version": BUNDLE_SCHEMA,
        "run_id": plan["run_id"],
        "objective": plan["objective"],
        "plan_sha256": digest(plan),
        "stages": stages,
        "by_model_and_layer": output,
        "all_planned_stages_executed": len(stages) == len(plan["stages"]),
        "cost": None,
        "limitations": [
            "当前网关/账号/时段与已冻结负载范围；不是供应商全局容量",
            "接口时间不包含浏览器渲染；浏览器证据另列",
            "未发现上界或未完成长稳时不能宣称最大可持续容量",
        ],
    }


def _meter_snapshot(
    stage: dict[str, Any], run_id: str, limits: dict[str, Any]
) -> dict[str, Any]:
    meter = stage["model_budget_meter"]
    key = os.environ.get(meter["api_key_env"], "")
    with httpx.Client(timeout=3) as client:
        response = client.get(
            meter["base_url"].rstrip("/") + "/budget",
            headers={"authorization": f"Bearer {key}"},
        )
        response.raise_for_status()
        value = response.json()
    if (
        value.get("run_id") != run_id
        or value.get("configuration_digest") != meter["configuration_digest"]
    ):
        raise PlanError("模型预算计量器身份或配置不匹配")
    configured = (value.get("budget") or {}).get("limits") or {}
    for key in ("max_seconds", "max_requests", "max_tokens"):
        cap = configured.get(key)
        if (
            isinstance(cap, bool)
            or not isinstance(cap, (int, float))
            or not 0 < cap <= limits[key]
        ):
            raise PlanError("物理计量器上限缺失或超过运行计划预算")
    return value


def _prepare_product(
    stage: dict[str, Any], budget: Budget, evidence: Path
) -> tuple[Any, list[dict[str, Any]]]:
    target = stage["target"]
    cookie = os.environ.get(target.get("cookie_env", "CAMPAIGN_COOKIE"), "")
    if not cookie:
        raise PlanError("缺少本任务测试会话")
    samples, _ = load_cases(stage["stage_id"])
    measured = {
        sample.case_id: sample
        for sample in samples
        if sample.cohort in {"measured", "representative"}
    }
    by_case = {
        sample.case_id: [
            item
            for item in samples
            if item.case_id == sample.case_id and item.cohort == "history"
        ]
        for sample in samples
    }
    tasks: list[tuple[Any, str | None, dict[str, Any]]] = []
    setup_rows: list[dict[str, Any]] = []
    maximum = int(stage.get("max_samples", stage["min_samples"]))
    if any(workload.get("case_id") not in measured for workload in stage["workloads"]):
        raise PlanError("未知固定题库 case_id，历史预置前拒绝发送")
    if stage["history_policy"] != "generated":
        raise PlanError(
            "fixed_replay 必须提供支持正常授权导入的适配器；当前入口不伪造历史注入"
        )
    with httpx.Client(timeout=None) as client:
        for index in range(maximum):
            workload = stage["workloads"][index % len(stage["workloads"])]
            sample = measured[workload["case_id"]]
            sample = replace(
                sample,
                sample_id=f"{stage['stage_id']}:{index}:{sample.case_id}:measured",
                deadline_seconds=int(stage["request_deadline_seconds"]),
            )
            thread_id = None
            for turn in by_case.get(sample.case_id, []):
                if not budget.reserve(int(stage["token_ceiling_per_request"])):
                    raise PlanError("历史预置阶段预算耗尽")
                turn = replace(
                    turn,
                    sample_id=f"{stage['stage_id']}:{index}:{sample.case_id}:history:{turn.history_index}",
                )
                row = send_sample(
                    client,
                    turn,
                    bff_base_url=target["bff_base_url"],
                    cookie=cookie,
                    tenant_id=target["tenant_id"],
                    context_version=int(target["context_version"]),
                    thread_id=thread_id,
                )
                row.update({"stage_id": stage["stage_id"], "cohort": "history"})
                setup_rows.append(row)
                # Logical sends use conservative allowances; physical model
                # usage is authoritatively enforced by the required relay.
                budget.settle(int(stage["token_ceiling_per_request"]), None)
                if row.get("primary_result") != "stream_completed" or not row.get(
                    "answer_associated"
                ):
                    _jsonl(
                        evidence / f"{stage['stage_id']}-history-failed.jsonl",
                        setup_rows,
                    )
                    raise PlanError(f"Q4 {sample.case_id} 必要历史未完成，测量受阻")
                thread_id = row["thread_id"]
            tasks.append((sample, thread_id, workload))

    def send(index: int) -> dict[str, Any]:
        if index >= len(tasks):
            return {
                "primary_result": "not_sent",
                "sent": False,
                "quality_status": "BLOCKED",
                "reason": "sample_pool_exhausted",
            }
        sample, thread_id, workload = tasks[index]
        with httpx.Client(timeout=None) as client:
            row = send_sample(
                client,
                sample,
                bff_base_url=target["bff_base_url"],
                cookie=cookie,
                tenant_id=target["tenant_id"],
                context_version=int(target["context_version"]),
                thread_id=thread_id,
                include_answer_text=True,
            )
        answer = row.pop("_answer_text", "") or ""
        with (evidence / "answers.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"sample_id": sample.sample_id, "answer": answer},
                    ensure_ascii=False,
                )
                + "\n"
            )
        row.update(
            {
                "workload_id": workload["workload_id"],
                "sampling_group": workload.get("sampling_group"),
                "quality_status": "PENDING",
                "business_valid": None,
                "history_verified": sample.category != "Q4" or bool(thread_id),
            }
        )
        return row

    return send, setup_rows


def assess_product_rows(
    stage: dict[str, Any],
    rows: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    evidence: Path,
) -> None:
    """Reconcile quality with actual scoped calls after the timed stage ends."""
    answers_path = evidence / "answers.jsonl"
    answers = (
        {
            row["sample_id"]: row["answer"]
            for row in (
                json.loads(line) for line in answers_path.read_text().splitlines()
            )
        }
        if answers_path.exists()
        else {}
    )
    observed_tools = Counter(
        row["sample_id"]
        for row in calls
        if row.get("kind") == "tool" and row.get("status") == "succeeded"
    )
    observed_attempts = {
        row["sample_id"] for row in calls if row.get("kind") == "model"
    }
    workloads = {workload["workload_id"]: workload for workload in stage["workloads"]}
    for row in rows:
        workload = workloads[row["workload_id"]]
        quality = assess_answer(
            {
                "case_id": row["case_id"],
                "category": row["category"],
                "quality_v2": workload.get("quality_v2"),
            },
            answers.get(row["sample_id"], ""),
            source=stage.get("data_source", "real"),
            tool_count=observed_tools[row["sample_id"]],
            oracle=workload.get("oracle"),
            history_verified=bool(row.get("history_verified")),
        )
        if row["sample_id"] not in observed_attempts:
            quality = {
                "status": "BLOCKED",
                "reasons": ["scoped_model_observations_missing"],
                "rule_version": "source-aware-v2",
            }
        row.update(
            {
                "quality_status": quality["status"],
                "quality_check": quality,
                "observed_successful_tool_attempts": observed_tools[row["sample_id"]],
                "business_valid": row.get("primary_result") == "stream_completed"
                and quality["status"] == "PASS",
            }
        )


def execute(plan: dict[str, Any], output: Path, evidence: Path) -> dict[str, Any]:
    """Execute an already validated plan, writing every cohort incrementally."""
    if output.exists() or evidence.exists():
        raise PlanError("输出目录已存在，拒绝覆盖")
    if (
        output.resolve() == evidence.resolve()
        or output.resolve() in evidence.resolve().parents
    ):
        raise PlanError("受控证据目录不得置于公开性能目录内")
    output.mkdir(parents=True)
    evidence.mkdir(parents=True, mode=0o700)
    private_plan = evidence / "run-plan.json"
    private_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    private_plan.chmod(0o600)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": BUNDLE_SCHEMA,
                "plan_sha256": digest(plan),
                "run_id": plan["run_id"],
                "recorded_at": datetime.now(UTC).isoformat(),
                "plan": public_plan(plan),
                "tool_source": _git_state(),
                "tool_source_sha256": {
                    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [
                        Path("pyproject.toml"),
                        Path("uv.lock"),
                        Path("scripts/unified_campaign/sender.py"),
                        *sorted(Path("scripts/product_chat_perf").glob("*.py")),
                    ]
                },
                "runtime_source": "must_be_verified_against_owned_deployment_separately",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    limits = plan["budgets"]
    budget = Budget(
        float(limits["max_seconds"]),
        int(limits["max_requests"]),
        int(limits["max_tokens"]),
    )
    summaries: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_calls: list[dict[str, Any]] = []
    all_resources: list[dict[str, Any]] = []
    stage_measurements: list[dict[str, Any]] = []
    stage_errors: list[dict[str, Any]] = []
    failed_levels: dict[tuple[str, str, str], int] = {}
    skipped_stages: list[dict[str, Any]] = []
    for stage in plan["stages"]:
        if budget.stopped_reason:
            break
        group = (
            stage["layer"],
            stage["model"],
            stage.get("graph_profile") or "upstream",
        )
        failed_level = failed_levels.get(group)
        if failed_level is not None and stage["concurrency"] > failed_level:
            skipped_stages.append(
                {
                    "stage_id": stage["stage_id"],
                    "reason": "higher_load_after_failed_level",
                }
            )
            continue
        try:
            stage_started_at = datetime.now(UTC).isoformat()
            if stage["layer"] == "U":
                gateway = stage["gateway"]
                key = os.environ.get(gateway["api_key_env"], "")
                if not key:
                    raise PlanError("上游网关凭据未配置")

                def send(
                    index: int, frozen: dict[str, Any] = stage, secret: str = key
                ) -> dict[str, Any]:
                    workload = frozen["workloads"][index % len(frozen["workloads"])]
                    with httpx.Client(timeout=None) as client:
                        return send_gateway_sample(
                            client,
                            base_url=frozen["gateway"]["base_url"],
                            api_key=secret,
                            model=frozen["model"],
                            workload=workload,
                            deadline_seconds=float(frozen["request_deadline_seconds"]),
                            token_ceiling=int(frozen["token_ceiling_per_request"]),
                        )

                stop = None
            else:
                if stage["layer"] == "E":
                    _meter_snapshot(stage, plan["run_id"], limits)
                send, setup = _prepare_product(stage, budget, evidence)
                all_rows.extend(setup)

                def stop(frozen: dict[str, Any] = stage) -> str | None:
                    if frozen["layer"] == "E":
                        try:
                            snapshot = _meter_snapshot(frozen, plan["run_id"], limits)
                            return snapshot["budget"].get("stopped_reason")
                        except (httpx.HTTPError, PlanError, KeyError):
                            return "model_budget_meter_unavailable"
                    return None

            def save(row: dict[str, Any]) -> None:
                row.update(
                    {
                        "schema_version": BUNDLE_SCHEMA,
                        "run_id": plan["run_id"],
                        "layer": stage["layer"],
                        "model": stage["model"],
                        "cohort": {
                            "warmup": "warmup",
                            "smoke": "precheck",
                            "fault": "diagnostic",
                            "burst": "burst",
                            "recovery": "recovery",
                        }.get(stage["phase"], "measured"),
                    }
                )
                all_rows.append(row)
                with (output / "requests.partial.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")

            execution_stage = (
                {**stage, "max_samples": stage.get("max_samples", stage["min_samples"])}
                if stage["layer"] != "U"
                else stage
            )
            sampler = ResourceSampler(list(stage.get("resource_containers") or []))
            sampler.start()
            try:
                result = run_stage(
                    execution_stage, send, budget=budget, on_row=save, should_stop=stop
                )
            finally:
                sampler.stop()
            all_resources.extend(
                {
                    **row,
                    "schema_version": BUNDLE_SCHEMA,
                    "run_id": plan["run_id"],
                    "stage_id": stage["stage_id"],
                }
                for row in sampler.rows
            )
            if stage["layer"] == "U":
                all_calls.extend(
                    {**row, "kind": "model", "physical_attempt_index": 0}
                    for row in result["rows"]
                )
            else:
                attempt_map = {
                    row["attempt_id"]: row["sample_id"]
                    for row in all_rows
                    if row.get("attempt_id")
                }
                calls = _collect_calls(
                    stage_started_at, attempt_map, source=stage["observation_source"]
                )
                calls = [
                    row
                    for row in calls
                    if row.get("sample_id") in set(attempt_map.values())
                ]
                all_calls.extend(calls)
                assess_product_rows(stage, result["rows"], calls, evidence)
            stage_measurements.append(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"rows", "arrivals"}
                }
            )
            _jsonl(output / f"{stage['stage_id']}-arrivals.jsonl", result["arrivals"])
            summary = summarize_stage(stage, result)
            summaries.append(summary)
            if summary["verdict"] == "FAIL":
                failed_levels[group] = min(
                    failed_levels.get(group, stage["concurrency"]), stage["concurrency"]
                )
            if summary["verdict"] in {"BLOCKED", "PARTIAL"}:
                break
            # A failed exploratory level prevents higher load in that group;
            # independent plans may still measure another model/layer.
            if result["stop_reason"] == "error_window":
                break
        except (PlanError, httpx.HTTPError, KeyError) as exc:
            stage_errors.append(
                {
                    "stage_id": stage["stage_id"],
                    "status": "BLOCKED",
                    "reason": str(exc)
                    if isinstance(exc, PlanError)
                    else type(exc).__name__,
                }
            )
            break
    _jsonl(output / "requests.jsonl", all_rows)
    _jsonl(output / "calls.jsonl", all_calls)
    _jsonl(output / "resources.jsonl", all_resources)
    _jsonl(
        output / "checks.jsonl",
        [
            {"sample_id": row.get("sample_id"), **row["quality_check"]}
            for row in all_rows
            if row.get("quality_check")
        ],
    )
    (output / "browser.jsonl").write_text("")
    (output / "stage-measurements.json").write_text(
        json.dumps(stage_measurements, ensure_ascii=False, indent=2) + "\n"
    )
    summary = capacity_verdict(plan, summaries)
    summary.update(
        {
            "budget": budget.snapshot(),
            "stage_errors": stage_errors,
            "skipped_stages": skipped_stages,
            "network_scope": "real"
            if any(stage["layer"] in {"U", "E"} for stage in plan["stages"])
            else "controlled",
            "browser": "not_collected_by_cli",
        }
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# 模型与产品容量报告",
        "",
        f"运行：{plan['run_id']}；目标：{plan['objective']}。",
        "",
        "本报告由原始请求重建。接口时间不含浏览器渲染；未知费用不记零。",
        "",
    ]
    for group in summary["by_model_and_layer"]:
        lines.append(
            f"- {group['layer']} / {group['model']} / {group.get('graph_profile')}：{group['capacity_status']}；最高已通过闭环并发 {group.get('highest_passed_closed_concurrency')}，首失败档 {group.get('first_failed_concurrency')}；不宣称绝对最大容量。"
        )
    for error in stage_errors:
        lines.append(f"- 受阻 {error['stage_id']}：{error['reason']}")
    lines.extend(
        [
            "",
            "未执行的阶段保留在 summary.json。预置/预热/测量/诊断必须分别对账，保留/回收按 manifest 的计划执行。",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Validate a plan offline, or run with explicit execution authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument(
        "--runner", choices=["capacity", "run_baseline", "load"], default="capacity"
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--enable-real-models", action="store_true")
    parser.add_argument("--enable-load", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        valid = validate_plan(
            plan,
            runner=args.runner,
            enable_real_models=args.enable_real_models,
            enable_load=args.enable_load,
            require_execution=args.command == "run",
        )
        if args.command == "validate":
            sys.stdout.write(json.dumps(valid, ensure_ascii=False) + "\n")
            return 0
        if args.runner != "capacity" or not args.out or not args.evidence:
            raise PlanError("run 需要 capacity 执行器及独立 --out/--evidence")
        result = execute(plan, args.out, args.evidence)
        sys.stdout.write(
            json.dumps(
                {
                    "run_id": plan["run_id"],
                    "all_planned_stages_executed": result[
                        "all_planned_stages_executed"
                    ],
                    "stage_errors": result["stage_errors"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 0 if not result["stage_errors"] else 2
    except (PlanError, ValueError, KeyError) as exc:
        sys.stderr.write(f"发送前校验失败或执行受阻：{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
