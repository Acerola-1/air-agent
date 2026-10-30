"""Offline acceptance report; never used to gate a product answer.

Retains transport completion and scenario assertions separately so an ordinary
answer, refusal or leaked protocol cannot turn a missing query into a pass.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

DOMAIN = "mcp_city_common_get_air_quality_realtime_stat"
DISTRICT = "mcp_city_base_get_district_by_lnglat"
CONTROLS = {"search_skills", "search_mcp", "load_skill", "load_mcp_tool"}


def visible_error_status(turn, failures):
    """Old incomplete captures lack abort metadata; do not call that zero errors."""
    if (
        turn.get("user_visible_error")
        or turn.get("output_aborts")
        or "raw_internal_tool_protocol_in_answer" in failures
    ):
        return True
    if turn.get("incomplete") and "output_aborts" not in turn:
        return None
    return False


def assertions(case, index, turn):
    """Check frozen scenario behavior, not arbitrary business response fields."""
    calls = turn.get("calls", [])
    names = [c["name"] for c in calls]
    executed = turn.get("executions", [])
    text = "\n".join(turn.get("answer", []))
    failures = []
    if (
        not text
        or turn.get("controls")
        or turn.get("incomplete")
        or turn.get("output_aborts")
    ):
        failures.append("final_answer_incomplete")
    if "<｜｜DSML｜｜" in text:
        failures.append("raw_internal_tool_protocol_in_answer")
    if turn.get("errors"):
        failures.append("user_visible_tool_error")
    if case in {"A02", "A03"}:
        if index in {3, 4}:
            if calls:
                failures.append("unnecessary_call_for_restate_or_thanks")
        else:
            city = ["湖州", "杭州", "金华"][index] if index < 3 else "湖州"
            queries = [e for e in executed if e["name"] == DOMAIN]
            if not queries or any(
                city not in str(q["params"].get("region", "")) for q in queries
            ):
                failures.append("missing_or_wrong_city_query")
        if index > 0 and any(n in CONTROLS for n in names):
            failures.append("search_or_reload_on_normal_followup")
    if case in {"A05", "B01", "B02"} and index == 0:
        if "load_skill" not in names:
            failures.append("expected_skill_not_loaded")
        if not any(e["name"] == DOMAIN for e in executed):
            failures.append("air_query_not_executed")
    if case == "A05" and index > 0:
        if executed or calls:
            failures.append("tool_used_for_out_of_scope_request")
        if not any(word in text for word in ("范围", "专注", "负责", "专业", "只回答")):
            failures.append("missing_professional_scope_explanation")
        if len(re.findall(r"[。！？]", text)) > 2 or re.search(r"\n\s*-", text):
            failures.append("scope_explanation_not_brief")
    if case == "A04" and executed:
        failures.append("business_query_before_clarifying_context_free_ellipsis")
    if case == "A08" and "skill:basic-qa:station-extreme" not in turn.get(
        "active_skills", []
    ):
        failures.append("naturally_named_skill_not_activated")
    if case == "A06":
        if not {
            "skill:basic-qa:air-quality-basic-query",
            "skill:basic-qa:trend-analysis",
        } <= set(turn.get("active_skills", [])):
            failures.append("required_skill_combination_missing")
        if not {DOMAIN, "oneIndexManyCityLine"} <= {e["name"] for e in executed}:
            failures.append("required_query_combination_missing")
    if case == "A07" and index in {1, 2}:
        if "skill:basic-qa:comparison-composition" not in turn.get("active_skills", []):
            failures.append("dedicated_comparison_skill_bypassed")
        if index == 2 and any(n in CONTROLS for n in names):
            failures.append("reload_when_returning_to_basic_query")
    if case in {"B02", "B03"}:
        target = [e for e in executed if e["name"] == DISTRICT]
        if not target:
            failures.append("independent_coordinate_query_not_executed")
        for e in target:
            try:
                valid = (
                    float(e["params"]["longitude"]) == 120.15
                    and float(e["params"]["latitude"]) == 30.27
                )
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                failures.append("incorrect_coordinate_parameters")
        if "load_mcp_tool" not in names:
            failures.append("independent_tool_not_loaded")
        if case == "B03" and "load_skill" in names:
            failures.append("unrelated_skill_loaded_for_independent_query")
    return sorted(set(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("never overwrite prior acceptance evidence")
    raw = json.loads((args.raw / "report.json").read_text())
    report = {
        k: raw.get(k)
        for k in (
            "dataset_version",
            "clock",
            "model",
            "source_fingerprint",
            "model_config_fingerprint",
            "tools",
            "auth",
            "skill_search",
            "mcp_search",
        )
    }
    report.update(
        evidence_kind="configured_real_model_controlled_tools_offline_scenario_review",
        raw_evidence=str(args.raw),
        runs=[],
        limitations=[
            "not native SDK, live MCP, embedding or browser acceptance",
            "scenario assertions are offline tests and never block product answers",
            "answer factual quality is not fully assessed by these structural assertions",
            "request captures exclude authentication headers; private raw evidence stays ignored",
            "old incomplete captures without output abort metadata have unknown visible-error counts",
        ],
    )
    totals = Counter()
    for run in raw["runs"]:
        output = {"case_id": run["case_id"], "repeat": run["repeat"], "turns": []}
        for index, turn in enumerate(run["turns"]):
            failures = assertions(run["case_id"], index, turn)
            counts = Counter(c["name"] for c in turn.get("calls", []))
            receipt_versions = {
                c["args"].get("skill_id", c["args"].get("tool_id")): c["args"].get(
                    "expected_version", c["args"].get("expected_schema_version")
                )
                for c in turn.get("calls", [])
                if c["name"].startswith("load_")
            }
            first = not failures and bool(turn.get("first_pass_completed"))
            recovered = not [
                f for f in failures if f != "user_visible_tool_error"
            ] and bool(turn.get("recovered_completed"))
            record = {
                "input_id": f"{run['case_id']}-{run['repeat']}-{index + 1}",
                "input": turn["input"],
                "transport_completed_without_tool_errors": turn.get(
                    "first_pass_completed", False
                ),
                "first_pass_completed": first,
                "recovered_completed": recovered,
                "incomplete": not (first or recovered),
                "user_visible_error": visible_error_status(turn, failures),
                "output_aborts": turn.get("output_aborts"),
                "scenario_failures": failures,
                "search_skills": counts["search_skills"],
                "search_mcp": counts["search_mcp"],
                "load_skill": counts["load_skill"],
                "load_mcp_tool": counts["load_mcp_tool"],
                "embedding_documents": turn.get("embedding_documents"),
                "embedding_queries": turn.get("embedding_queries"),
                "activated": None,
                "reused": None,
                "model_requests": turn.get("model_requests"),
                "logical_tool_calls": len(turn.get("calls", [])),
                "network_attempts": turn.get("network_attempts"),
                "controlled_business_calls": len(turn.get("executions", [])),
                "parameter_assertions": "failed"
                if any("parameters" in f or "city" in f for f in failures)
                else "passed_for_asserted_fields",
                "material_and_schema_versions_loaded_this_turn": receipt_versions,
                "first_answer_ms": turn.get("first_answer_ms"),
                "total_ms": turn.get("elapsed_ms"),
                "usage": turn.get("model_usage"),
                "activation_observation_note": "activation/reuse facts were not collected by this controlled harness; not recorded as zero",
            }
            output["turns"].append(record)
            for name in (
                "first_pass_completed",
                "recovered_completed",
                "incomplete",
                "user_visible_error",
            ):
                totals[name] += int(record[name] is True)
            totals["user_visible_error_uncollected"] += int(
                record["user_visible_error"] is None
            )
        report["runs"].append(output)
    report["totals"] = dict(
        totals,
        runs=len(report["runs"]),
        turns=sum(len(r["turns"]) for r in report["runs"]),
    )
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["totals"]))  # noqa: T201


if __name__ == "__main__":
    main()
