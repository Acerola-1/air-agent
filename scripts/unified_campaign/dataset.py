"""Load the frozen Basic QA bank and the two public-graph representatives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "docs/testing/data/basic-qa-reference-cases.v1.json"
FIXTURES_PATH = ROOT / "docs/testing/data/basic-qa-reference-fixtures.v1.json"
REPRESENTATIVE_PATH = (
    ROOT / "docs/testing/data/public-graph-representative-cases.v1.json"
)
DATASET_VERSION = "bqa-reference-202605-v1"


@dataclass(frozen=True)
class PlannedSample:
    """One planned send. Question text stays in memory and out of reports."""

    sample_id: str
    case_id: str
    category: str
    graph_id: str
    cohort: str
    content: str
    content_sha256: str
    deadline_seconds: int
    expected_intent: str | None
    menu_name: str | None
    parent_case_id: str | None
    history_index: int | None
    counts_as_semantic_sample: bool


def sha256_file(path: Path) -> str:
    """Return the hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    """Return the hex digest of a UTF-8 string."""
    return hashlib.sha256(value.encode()).hexdigest()


def load_plan(round_id: str = "round-1") -> tuple[list[PlannedSample], dict[str, str]]:
    """Build measured, history, and representative samples from the frozen files."""
    dataset = json.loads(CASES_PATH.read_text())
    representatives = json.loads(REPRESENTATIVE_PATH.read_text())
    if dataset["dataset_version"] != DATASET_VERSION:
        raise ValueError("题库版本与冻结版本不一致")
    cases = dataset["cases"]
    if len(cases) != 40 or len({case["case_id"] for case in cases}) != 40:
        raise ValueError("固定题库必须是 40 道不重复题")
    samples: list[PlannedSample] = []
    for case in cases:
        if case["category"] == "Q4":
            user_index = 0
            for message in case["history"]:
                if message["role"] != "user":
                    continue
                samples.append(
                    _sample(
                        sample_id=f"{round_id}:{case['case_id']}:history:{user_index}",
                        case_id=case["case_id"],
                        category="Q4",
                        graph_id="basic-qa",
                        cohort="history",
                        content=message["content"],
                        deadline_seconds=int(case["deadline_seconds"]),
                        expected_intent=case["expected_intent"],
                        menu_name=None,
                        parent_case_id=case["case_id"],
                        history_index=user_index,
                        counts_as_semantic_sample=False,
                    )
                )
                user_index += 1
        samples.append(
            _sample(
                sample_id=f"{round_id}:{case['case_id']}:measured",
                case_id=case["case_id"],
                category=case["category"],
                graph_id="basic-qa",
                cohort="measured",
                content=case["question"],
                deadline_seconds=int(case["deadline_seconds"]),
                expected_intent=case["expected_intent"],
                menu_name=None,
                parent_case_id=None,
                history_index=None,
                counts_as_semantic_sample=True,
            )
        )
    for item in representatives["cases"]:
        samples.append(
            _sample(
                sample_id=f"{round_id}:{item['case_id']}:representative",
                case_id=item["case_id"],
                category="representative",
                graph_id=item["graph_id"],
                cohort="representative",
                content=item["question"],
                deadline_seconds=int(item["deadline_seconds"]),
                expected_intent=item.get("expected_intent"),
                menu_name=item.get("menu_name"),
                parent_case_id=None,
                history_index=None,
                counts_as_semantic_sample=False,
            )
        )
    digests = {
        "cases_sha256": sha256_file(CASES_PATH),
        "fixtures_sha256": sha256_file(FIXTURES_PATH),
        "representatives_sha256": sha256_file(REPRESENTATIVE_PATH),
        "dataset_version": DATASET_VERSION,
        "representative_version": representatives["version"],
    }
    return samples, digests


def _sample(**kwargs: object) -> PlannedSample:
    content = str(kwargs.pop("content"))
    return PlannedSample(
        content=content,
        content_sha256=sha256_text(content),
        **kwargs,  # type: ignore[arg-type]
    )
