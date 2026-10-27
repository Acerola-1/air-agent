"""Evidence loss, duplicate case labels and failed history must not become PASS."""

import hashlib
import json
from dataclasses import replace

import pytest

from scripts.product_chat_perf.formal_review import (
    history_complete,
    identity_evidence,
    latency_summary,
    recover_answers,
    review_bundle,
)


def request(key="one", stamp="2026-10-25T03:00:00+00:00", answer="answer"):
    return {
        "submission_key": key,
        "attempt_id": key,
        "sample_id": "same-question",
        "case_id": "BQA-001",
        "category": "Q1",
        "cohort": "measured",
        "stage": "baseline",
        "planned_at": stamp,
        "thread_id": "thread",
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "answer_nonempty": True,
        "answer_associated": True,
        "primary_result": "stream_completed",
        "client_first_answer_ms": 20,
        "client_complete_ms": 25,
    }


def test_duplicate_question_labels_recover_distinct_request_answers():
    first = request(answer="baseline")
    second = request("two", "2026-10-25T04:00:00+00:00", "load")
    second.update(stage="concurrency", concurrency_level=10)
    answers = [
        {
            "sample_id": "same-question",
            "answer": "baseline",
            "recorded_at": "2026-10-25T03:00:00.001+00:00",
        },
        {
            "sample_id": "same-question",
            "answer": "load",
            "recorded_at": "2026-10-25T04:00:00.001+00:00",
        },
    ]
    recovered, proofs = recover_answers([first, second], answers)
    assert recovered == {"one": "baseline", "two": "load"}
    assert all(p["sha256_verified"] for p in proofs)


def test_ambiguous_time_match_is_rejected_instead_of_picking_latest():
    rows = [request(), request("two")]
    with pytest.raises(ValueError, match="不唯一"):
        recover_answers(
            rows,
            [
                {
                    "sample_id": "same-question",
                    "recorded_at": rows[0]["planned_at"],
                    "answer": "answer",
                }
            ],
        )


def test_digest_mismatch_and_duplicate_physical_ids_fail_closed():
    row = request()
    with pytest.raises(ValueError, match="SHA256"):
        recover_answers([row], [{"submission_key": "one", "answer": "wrong"}])
    with pytest.raises(ValueError, match="重复物理"):
        recover_answers([row, row], [])
    with pytest.raises(ValueError, match="未找回"):
        recover_answers([row], [])


@pytest.mark.parametrize("observations", [[], [{"user_id": None, "session_id": None}]])
def test_missing_identity_evidence_is_unknown(observations):
    assert identity_evidence(observations, "root-uuid", "thread") == {
        "user_id": "UNKNOWN",
        "session_id": "UNKNOWN",
    }


def test_identity_mismatch_has_positive_control():
    valid = [{"user_id": "root-uuid", "session_id": "thread"}]
    assert identity_evidence(valid, "root-uuid", "thread") == {
        "user_id": "PASS",
        "session_id": "PASS",
    }
    assert identity_evidence(valid, "other-user", "other-thread") == {
        "user_id": "FAIL",
        "session_id": "FAIL",
    }


def test_history_failure_or_wrong_thread_blocks_measurement():
    row = request()
    row["category"] = "Q4"
    history = [{**row, "cohort": "history"} for _ in range(5)]
    assert history_complete(row, history)
    assert not history_complete(row, history[:-1])
    assert not history_complete(
        row, [{**history[0], "primary_result": "runtime_aborted"}, *history[1:]]
    )
    assert not history_complete(
        row, [{**history[0], "thread_id": "other"}, *history[1:]]
    )


def test_latency_preserves_rejected_denominator_and_missing_gap():
    failed = {"primary_result": "admission_failed"}
    result = latency_summary([request(), failed])
    assert result["attempted"] == 2
    assert result["completed"] == 1
    assert result["no_first_body"] == 1
    assert result["max_effective_body_gap_ms"] is None


def test_offline_bundle_is_immutable_and_quality_needs_actual_oracle(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    row = request()
    raw = json.dumps(row) + "\n"
    (source / "requests.baseline.jsonl").write_text(raw)
    (source / "langfuse-reconcile.json").write_text(
        json.dumps(
            {
                "association": [
                    {
                        "attempt_id": "one",
                        "expected_trace_id": "trace",
                        "trace_found": False,
                        "identity_user_ok": True,
                        "identity_session_ok": True,
                    }
                ]
            }
        )
    )
    answers = tmp_path / "private.jsonl"
    answers.write_text(json.dumps({"submission_key": "one", "answer": "answer"}) + "\n")
    out = tmp_path / "review"
    result = review_bundle(source, answers, out)
    assert result["quality"] == {"BLOCKED": 1}
    assert result["langfuse"]["user_identity"] == {"UNKNOWN": 1}
    assert (source / "requests.baseline.jsonl").read_text() == raw
    assert 'answer"' not in (out / "answer-associations.jsonl").read_text()
    with pytest.raises(ValueError, match="禁止覆盖"):
        review_bundle(source, answers, out)


def test_collector_scopes_sample_and_persists_physical_answer_identity(
    monkeypatch, tmp_path
):
    from scripts import formal_root_round as collector
    from scripts.unified_campaign.dataset import load_plan

    sample = next(s for s in load_plan()[0] if s.cohort == "measured")
    ids = []

    def send(client, sample, **kwargs):
        ids.append(sample.sample_id)
        return {
            "sample_id": sample.sample_id,
            "submission_key": str(len(ids)),
            "_answer_text": "body",
        }

    monkeypatch.setattr(collector, "send_sample", send)
    session = {
        "cookie": "not-a-real-cookie",
        "tenant_id": "tenant",
        "context_version": 1,
    }
    for level in [1, 2, 5, 10]:
        row = collector._send_one(
            None, replace(sample), session, None, "concurrency", "run", level
        )
        collector._write_row(
            tmp_path / "requests.jsonl", row, tmp_path / "answers.jsonl"
        )
    assert len(set(ids)) == 4
    answer_rows = [
        json.loads(s) for s in (tmp_path / "answers.jsonl").read_text().splitlines()
    ]
    assert [r["submission_key"] for r in answer_rows] == ["1", "2", "3", "4"]
