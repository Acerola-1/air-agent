"""Only attested original spans may be reingested; acceptance is not persistence."""

import hashlib
import json

import httpx
import pytest

from scripts.product_chat_perf import langfuse_recovery as recovery

TRACE = "a" * 32


@pytest.fixture(autouse=True)
def no_real_clickhouse(monkeypatch):
    monkeypatch.setattr(recovery, "existing_span_ids", lambda allowed: set())


def test_original_json_keeps_unicode_line_separator_content():
    raw = json.dumps({"original": "one\u2028two\u0085three"}, ensure_ascii=False)
    assert recovery.extract_original_json("log\nRECOVERY_JSON:" + raw + "\n") == raw


def resources():
    return [
        {
            "resource": {"attributes": []},
            "scopeSpans": [
                {
                    "scope": {"name": "langfuse"},
                    "spans": [
                        {
                            "traceId": TRACE,
                            "spanId": f"{i:016x}",
                            "startTimeUnixNano": "12345",
                            "endTimeUnixNano": "67890",
                        }
                        for i in range(3)
                    ],
                }
            ],
        }
    ]


def test_bounded_original_span_batches_preserve_ids_and_times():
    chunks = list(recovery.span_batches(resources(), {TRACE}, size=2))
    spans = [
        s
        for c in chunks
        for r in c["resourceSpans"]
        for scope in r["scopeSpans"]
        for s in scope["spans"]
    ]
    assert len(chunks) == 2
    assert [s["spanId"] for s in spans] == [f"{i:016x}" for i in range(3)]
    assert {s["startTimeUnixNano"] for s in spans} == {"12345"}
    with pytest.raises(ValueError, match="目标之外"):
        list(recovery.span_batches(resources(), {"b" * 32}))


def test_stored_buffer_ids_are_wire_encoded_without_changing_identity():
    original = resources()
    span = original[0]["scopeSpans"][0]["spans"][0]
    span["traceId"] = {"type": "Buffer", "data": list(bytes.fromhex(TRACE))}
    span["spanId"] = {"type": "Buffer", "data": [1] * 8}
    span["parentSpanId"] = {"type": "Buffer", "data": []}
    wire = list(recovery.span_batches(original, {TRACE}))[0]
    result = wire["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert result["traceId"] == TRACE
    assert result["spanId"] == "01" * 8
    assert result["parentSpanId"] == ""
    assert isinstance(span["spanId"], dict)


def saved_payload(tmp_path):
    raw = json.dumps({"payloads": [{"resourceSpans": resources()}]})
    payload = tmp_path / "original.json"
    payload.write_text(raw)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "trace_ids": [TRACE],
                "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
        )
    )
    return manifest, payload


def test_replay_requires_apply_and_original_digest(tmp_path):
    manifest, payload = saved_payload(tmp_path)
    receipt = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="--apply"):
        recovery.replay(manifest, payload, receipt, apply=False)
    payload.write_text("changed")
    with pytest.raises(ValueError, match="摘要不符"):
        recovery.replay(manifest, payload, receipt, apply=True)
    assert not receipt.exists()


def test_official_ingestion_receipt_does_not_claim_persistence(tmp_path, monkeypatch):
    manifest, payload = saved_payload(tmp_path)
    monkeypatch.setattr(
        recovery,
        "dotenv_values",
        lambda _: {
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
            "LANGFUSE_PUBLIC_KEY": "synthetic-public",
            "LANGFUSE_SECRET_KEY": "synthetic-secret",
        },
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    original = httpx.Client
    monkeypatch.setattr(
        recovery.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    result = recovery.replay(manifest, payload, tmp_path / "receipt.json", apply=True)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/public/otel/v1/traces"
    assert requests[0].headers["x-langfuse-ingestion-version"] == "4"
    assert result["model_calls"] == 0
    assert "not proof" in result["note"]
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    message = ExportTraceServiceRequest.FromString(requests[0].content)
    span = message.resource_spans[0].scope_spans[0].spans[0]
    assert span.trace_id == bytes.fromhex(TRACE)
    assert span.start_time_unix_nano == 12345
    assert requests[0].headers["content-type"] == "application/x-protobuf"


def test_partial_ingestion_is_recorded_as_failure(tmp_path, monkeypatch):
    manifest, payload = saved_payload(tmp_path)
    monkeypatch.setattr(
        recovery,
        "dotenv_values",
        lambda _: {
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
            "LANGFUSE_PUBLIC_KEY": "synthetic-public",
            "LANGFUSE_SECRET_KEY": "synthetic-secret",
        },
    )
    original = httpx.Client
    monkeypatch.setattr(
        recovery.httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"partialSuccess": {"rejectedSpans": 1}}
                )
            ),
        ),
    )
    result = recovery.replay(manifest, payload, tmp_path / "receipt.json", apply=True)
    assert result["stopped_reason"] == "ingestion_failed_or_partial"


def test_existing_span_id_prevents_replay_across_time_precisions(tmp_path, monkeypatch):
    manifest, payload = saved_payload(tmp_path)
    monkeypatch.setattr(
        recovery,
        "existing_span_ids",
        lambda allowed: {(TRACE, f"{i:016x}") for i in range(3)},
    )
    monkeypatch.setattr(
        recovery,
        "dotenv_values",
        lambda _: {
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
            "LANGFUSE_PUBLIC_KEY": "synthetic-public",
            "LANGFUSE_SECRET_KEY": "synthetic-secret",
        },
    )
    original = httpx.Client

    def unexpected(request):
        pytest.fail("已存在的 span 不应再补录")

    monkeypatch.setattr(
        recovery.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(unexpected)),
    )
    result = recovery.replay(manifest, payload, tmp_path / "receipt.json", apply=True)
    assert result["already_present_spans_skipped"] == 3
    assert result["planned_batches"] == 0


def test_network_unknown_keeps_receipt_without_blind_retry(tmp_path, monkeypatch):
    manifest, payload = saved_payload(tmp_path)
    monkeypatch.setattr(
        recovery,
        "dotenv_values",
        lambda _: {
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3001",
            "LANGFUSE_PUBLIC_KEY": "synthetic-public",
            "LANGFUSE_SECRET_KEY": "synthetic-secret",
        },
    )
    original = httpx.Client

    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(
        recovery.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(timeout)),
    )
    result = recovery.replay(manifest, payload, tmp_path / "receipt.json", apply=True)
    assert result["batches"][0]["outcome"] == "UNKNOWN"
    assert result["stopped_reason"] == "ingestion_outcome_unknown_no_automatic_retry"
