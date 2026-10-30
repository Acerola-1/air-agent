"""Validate the frozen Basic QA conversation acceptance inputs, without running the graph."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/testing/data"


def read_json(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def check() -> None:
    dataset = read_json("basic-qa-conversation-loop.v1.json")
    reference = read_json("basic-qa-reference-cases.v1.json")
    fixtures = read_json("basic-qa-reference-fixtures.v1.json")
    assert dataset["dataset_version"] == "bqa-conversation-loop-v1"
    assert dataset["clock"] == {
        "instant": "2026-05-08T16:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "policy": dataset["clock"]["policy"],
    }
    assert dataset["reference"]["dataset_version"] == reference["dataset_version"]
    assert dataset["reference"]["fixture_version"] == fixtures["fixture_version"]
    assert dataset["clock"]["instant"] == fixtures["reference_clock"]
    assert fixtures["synthetic"] is True
    source = {case["case_id"]: case for case in reference["cases"]}
    cases = dataset["cases"]
    ids = {case["id"] for case in cases}
    assert len(ids) == len(cases)
    assert {case["group"] for case in cases} == set("1234567") | {"reference"}
    counts = {
        "search",
        "embedding_attempts",
        "load",
        "domain_requests",
        "planning_tool_batches",
        "model_requests",
    }
    matrix = set()
    for case in cases:
        assert case["mode"] in {"fast", "expert"}
        assert case["input"]["turns"] and all(case["input"]["turns"])
        assert case["input"]["setup"]
        assert set(case["expected_target"]) == {
            "city",
            "date_or_period",
            "metric",
            "scope",
        }
        assert case["allowed_actions"] and case["forbidden_actions"]
        assert case["answer_and_evidence_checks"] and case["evidence_basis"]
        assert set(case["count_ranges"]) == counts
        for minimum, maximum in case["count_ranges"].values():
            assert type(minimum) is int and minimum >= 0
            assert maximum is None or (type(maximum) is int and maximum >= minimum)
        if case["positive_control"]:
            assert case["positive_control"] in ids
        if case["group"] != "reference":
            continue
        original = source[case["reference_case_id"]]
        assert case["input"]["turns"] == [original["question"]]
        assert case["answer_and_evidence_checks"] == original["answer_checks"]
        assert case["fixture_refs"] == original["fixture_refs"]
        assert all(name in fixtures for name in case["fixture_refs"])
        assert (
            case["expected_skill"] == "skill:basic-qa:" + original["expected_skills"][0]
        )
        pair = (case["expected_skill"], case["mode"])
        assert pair not in matrix
        matrix.add(pair)
    all_skills = {
        "skill:basic-qa:" + skill
        for case in reference["cases"]
        for skill in case["expected_skills"]
    }
    assert len(all_skills) == 9
    assert matrix == {
        (skill, mode) for skill in all_skills for mode in ("fast", "expert")
    }
    for case in cases:
        if case["group"] in {"6", "7"} and case["id"] != "C23":
            assert case["positive_control"] == "C23"
            assert case["count_ranges"]["domain_requests"] == [0, 0]
    print(f"PASS: {len(cases)} cases; seven groups; {len(matrix)} Skill/mode pairs")  # noqa: T201
    print(  # noqa: T201
        "Scope: static fixture consistency only; no graph, model, service, SDK or browser was run."
    )


if __name__ == "__main__":
    check()
