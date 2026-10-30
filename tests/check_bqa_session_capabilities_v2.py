"""Check the frozen v2 matrix without claiming execution evidence."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check():
    data = json.loads(
        (ROOT / "docs/testing/data/bqa-session-capabilities-v2.json").read_text()
    )
    parent = ROOT / data["parent"]["path"]
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == data["parent"]["sha256"]
    assert data["parent"]["count"] == 47
    expected = {
        f"{letter}{i:02}"
        for letter, n in (("A", 8), ("B", 8), ("C", 9), ("D", 6))
        for i in range(1, n + 1)
    }
    cases = data["cases"]
    assert len({c["id"] for c in cases}) == len(cases) == 49
    assert {c["id"] for c in cases if c["id"][0] != "R"} == expected
    matrix = set()
    for case in cases:
        assert case["input"]["turns"] or case["input"].get("operations")
        assert case["clock"]["instant"] and case["clock"]["timezone"]
        assert case["initial_state"] and case["parameters"] and case["expected"]
        assert case["counters"] and case["judgment"]
        if case["id"].startswith("R-"):
            matrix.add((case["expected_skill"], case["mode"]))
    assert len(matrix) == 18
    assert all(
        (skill, mode) in matrix for skill, _ in matrix for mode in ("fast", "expert")
    )
    return {
        "cases": len(cases),
        "groups": len(expected),
        "skill_mode_pairs": len(matrix),
        "execution": "NOT_RUN",
    }


if __name__ == "__main__":
    print(json.dumps(check()))  # noqa: T201
