"""Unit checks for the controlled P/R load runner's pure decision helpers."""

from __future__ import annotations

from scripts.product_chat_perf.load import _stats, _stop_rule


def _row(ms: int, result: str = "stream_completed") -> dict:
    return {
        "primary_result": result,
        "client_complete_ms": ms,
        "ended_at_offset_ms": ms,
        "client_first_answer_ms": max(1, ms - 100),
        "sent": True,
    }


def test_stop_rule_trips_on_twenty_ended_with_ten_percent_failures() -> None:
    rows = [_row(1000 + index) for index in range(17)]
    rows += [
        _row(1050, "client_timeout"),
        _row(1060, "runtime_aborted"),
        _row(1070, "runtime_aborted"),
    ]
    assert _stop_rule(rows) is True


def test_stop_rule_does_not_trip_before_twenty_samples() -> None:
    rows = [_row(1000 + index) for index in range(10)]
    rows += [_row(1050, "client_timeout") for _ in range(9)]
    assert _stop_rule(rows) is False


def test_stop_rule_does_not_trip_on_all_success() -> None:
    rows = [_row(1000 + index) for index in range(25)]
    assert _stop_rule(rows) is False


def test_stats_uses_linear_percentiles_and_counts_failures() -> None:
    rows = [_row(100), _row(200), _row(300), _row(400, "client_timeout")]
    stats = _stats(rows)
    assert stats["planned"] == 4
    assert stats["success"] == 3
    assert stats["first_answer_p50_ms"] == 100
    assert stats["outcomes"] == {"stream_completed": 3, "client_timeout": 1}


def test_run_round_loop_terminates_with_monkeypatched_sender(
    tmp_path, monkeypatch
) -> None:
    import scripts.product_chat_perf.load as load
    from scripts.unified_campaign.dataset import load_plan

    samples, _ = load_plan()
    measured = [
        sample
        for sample in samples
        if sample.cohort == "measured" and sample.category == "Q1"
    ][:2]
    samples = measured

    def fake_send(client, sample, config, cookie, thread_id=None):
        return {
            "primary_result": "stream_completed",
            "sent": True,
            "client_complete_ms": 1,
            "client_first_answer_ms": 1,
            "sample_id": sample.sample_id,
        }

    monkeypatch.setattr(load, "_send_one", fake_send)
    monkeypatch.setattr(load, "_load_measured_threads", lambda *args, **kwargs: {})
    config = {
        "bff_base_url": "http://127.0.0.1:1",
        "tenant_id": "tenant-a",
        "context_version": 1,
        "concurrency": 2,
        "round_id": "c2-r1",
        "warmup_seconds": 0.05,
        "measure_seconds": 0.05,
        "budgets": {"max_seconds": 10, "max_requests": 1000, "max_tokens": 1000},
    }
    rows = load._run_round(config, samples, "session=test", tmp_path, tmp_path)
    assert rows
    assert all(row["primary_result"] == "stream_completed" for row in rows)
