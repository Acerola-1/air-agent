"""Latency decomposition must conserve wall time and not double-count nesting."""

from datetime import UTC, datetime

from scripts.product_chat_perf.latency_breakdown import breakdown, union_us


def test_union_counts_nested_and_overlapping_spans_once():
    assert union_us([(0, 10), (2, 8), (8, 12), (20, 25)]) == 17
    assert union_us([]) == 0
    assert union_us([(10, 5), (0, 0)]) == 0


def test_prebody_clipping_and_exclusive_tool_time_conserve_wall_time():
    start = int(datetime(2026, 10, 25, tzinfo=UTC).timestamp() * 1e6)
    row = {
        "case_id": "case",
        "category": "Q2",
        "planned_at": "2026-10-25T00:00:00.030000+00:00",
        "observed_ms": 25,
        "client_confirmation_end_ms": 30,
        "client_first_answer_ms": 20,
    }

    def span(name, kind, begin, end):
        return {
            "name": name,
            "type": kind,
            "start_us": start + begin * 1000,
            "end_us": start + end * 1000,
            "span_id": name,
        }

    spans = [
        span("chain", "CHAIN", 0, 100),
        span("m1", "GENERATION", 1, 10),
        span("m2", "GENERATION", 5, 15),
        span("tool", "TOOL", 10, 18),
        span("after", "GENERATION", 30, 40),
    ]
    result = breakdown(row, spans)
    assert result["model_union_ms"] == 14
    assert result["tool_excluding_model_overlap_ms"] == 3
    assert result["unattributed_ms"] == 3
    assert (
        sum(
            result[k]
            for k in [
                "model_union_ms",
                "tool_excluding_model_overlap_ms",
                "unattributed_ms",
            ]
        )
        == result["first_body_ms"]
    )
    assert result["model_intervals_before_body"] == 2
    assert not result["clock_alignment_warning"]


def test_missing_trace_and_missing_body_are_not_zero_latency_success():
    row = {"case_id": "case", "client_first_answer_ms": 20}
    assert breakdown(row, [])["status"] == "missing_trace"
    row["client_first_answer_ms"] = None
    assert breakdown(row, [])["status"] == "no_body_observed"


def test_unclosed_model_span_is_not_valid_component_measurement():
    start = int(datetime(2026, 10, 25, tzinfo=UTC).timestamp() * 1e6)
    row = {
        "case_id": "case",
        "category": "Q1",
        "planned_at": "2026-10-25T00:00:00.020000+00:00",
        "observed_ms": 20,
        "client_first_answer_ms": 10,
    }
    spans = [
        {
            "name": "m",
            "type": "GENERATION",
            "start_us": start,
            "end_us": None,
            "span_id": "m",
        }
    ]
    assert breakdown(row, spans)["status"] == "incomplete_component_spans"


def test_model_roles_come_from_parent_nodes_not_model_name():
    start = int(datetime(2026, 10, 25, tzinfo=UTC).timestamp() * 1e6)
    row = {
        "case_id": "case",
        "category": "Q1",
        "planned_at": "2026-10-25T00:00:00.050000+00:00",
        "observed_ms": 50,
        "client_first_answer_ms": 40,
    }

    def span(span_id, name, kind, begin, end, parent=None):
        return {
            "span_id": span_id,
            "name": name,
            "type": kind,
            "start_us": start + begin * 1000,
            "end_us": start + end * 1000,
            "parent_span_id": parent,
        }

    spans = [
        span("p", "call_model", "CHAIN", 0, 20),
        span("a", "answer_model", "CHAIN", 20, 50),
        span("m1", "same-model", "GENERATION", 5, 15, "p"),
        span("m2", "same-model", "GENERATION", 25, 45, "a"),
    ]
    result = breakdown(row, spans)
    assert result["model_phase_union_ms"] == {
        "planning": 10,
        "answer": 15,
        "unclassified": 0,
    }
    assert result["model_phase_calls"] == {
        "planning": 1,
        "answer": 1,
        "unclassified": 0,
    }
