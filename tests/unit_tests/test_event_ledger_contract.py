"""事件台账 v1 契约测试（Python 侧）.

共享样本集 `docs/contracts/agent-events-v1/samples/samples.json`；TS 侧.
`frontend/tests/agent-events-contract.test.cjs` 用同一组样本，任一侧漂移即失败。
"""

from __future__ import annotations

import json
from pathlib import Path

from common.event_ledger.contract import (
    canonical_digest,
    enabled_event_types,
    registry,
    reserved_event_types,
    samples,
    states,
    validate_batch,
    validate_event,
)

EXPECTED = {
    "ok",
    "unknown_type",
    "type_not_enabled",
    "source_not_allowed",
    "missing_field",
    "missing_required_payload_field",
    "forbidden_payload_key",
    "payload_field_not_allowed",
    "schema_version_unsupported",
    "bad_timestamp",
    "bad_uuid",
    "payload_too_large",
    "invalid_enum_value",
}


def _cases() -> list[dict]:
    return samples()["cases"]


def test_sample_cases_have_known_reason_codes() -> None:
    declared = set(samples()["reason_codes"])
    for case in _cases():
        assert case["expect"]["reason"] in declared, case["name"]
        assert case["expect"]["reason"] in EXPECTED, case["name"]


def test_each_sample_matches_expected_verdict() -> None:
    """逐条比对期望：合法样本通过、非法样本命中声明的原因码."""
    failures = []
    for case in _cases():
        got = validate_event(case["event"])
        expected = case["expect"]
        if got.valid != expected["valid"] or (
            not got.valid and got.reason != expected["reason"]
        ):
            failures.append(f"{case['name']}: expected={expected} got={got.as_dict()}")
    assert not failures, "\n".join(failures)


def test_valid_samples_count_is_nonzero() -> None:
    valid = [c for c in _cases() if c["expect"]["valid"]]
    assert len(valid) >= 9, "至少覆盖全部 9 个启用类型"


def test_browser_cannot_claim_server_facts() -> None:
    case = next(c for c in _cases() if c["name"] == "browser-terminal-claim-invalid")
    assert validate_event(case["event"]).reason == "source_not_allowed"


def test_reserved_types_rejected_not_enabled() -> None:
    reserved = reserved_event_types()
    assert len(reserved) >= 8
    for name in reserved:
        event = {
            "schema_version": "1.0",
            "event_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "event_type": name,
            "occurred_at": "2026-10-13T00:00:00.000Z",
            "source": registry()["event_types"][name]["sources"][0],
            "tenant_id": "11111111-1111-4111-8111-111111111111",
            "user_id": "22222222-2222-4222-8222-222222222222",
            "membership_id": "33333333-3333-4333-8333-333333333333",
            "payload": {},
        }
        assert validate_event(event).reason == "type_not_enabled", name


def test_enabled_types_are_the_v1_core_family() -> None:
    assert enabled_event_types() == {
        "request.accepted",
        "run.admission_confirmed",
        "run.state_observed",
        "run.cancel_requested",
        "run.resumed",
        "answer.confirmed",
        "answer.partial",
        "trace.linked",
        "resource.deleted",
    }


def test_canonical_digest_is_key_order_independent() -> None:
    a = {"b": 1, "a": [1, 2], "c": {"y": 2, "x": 1}}
    b = {"c": {"x": 1, "y": 2}, "a": [1, 2], "b": 1}
    assert canonical_digest(a) == canonical_digest(b)
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})


def test_validate_batch_reports_per_item() -> None:
    good = next(c["event"] for c in _cases() if c["expect"]["valid"])
    bad = next(c["event"] for c in _cases() if not c["expect"]["valid"])
    result = validate_batch([good, bad, good], source="runtime")
    assert result.accepted is False
    assert [i.valid for i in result.items] == [True, False, True]
    assert result.items[1].reason == "source_not_allowed"


def test_validate_batch_rejects_browser_oversize_batch() -> None:
    good = next(c["event"] for c in _cases() if c["expect"]["valid"])
    many = [good] * (registry()["envelope"]["limits"]["browser_batch_events"] + 1)
    result = validate_batch(many, source="browser")
    assert result.accepted is False
    assert result.batch_reason.startswith("batch_too_many")


def test_states_dictionary_flags_terminal_and_ended() -> None:
    st = states()["states"]
    ended = {k for k, v in st.items() if v["counts_as_ended"]}
    assert ended == {"succeeded", "failed", "cancelled", "timed_out"}
    not_ended = {"waiting_input", "status_unknown", "admitting", "admission_unknown"}
    assert not_ended.isdisjoint(ended)


def test_embedded_copy_matches_published_contract() -> None:
    """包内副本必须与发布契约逐字节一致（容器只挂载 src，运行期用包内副本）。"""
    published = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "agent-events-v1"
    )
    embedded = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "common"
        / "event_ledger"
        / "contract_data"
    )
    for name, pub in (
        ("event-registry.json", published / "event-registry.json"),
        ("states.json", published / "states.json"),
        ("metrics.json", published / "metrics.json"),
        ("samples.json", published / "samples" / "samples.json"),
    ):
        assert (embedded / name).read_bytes() == pub.read_bytes(), (
            f"{name} 与发布契约漂移"
        )


def test_registry_matches_checked_in_contract_file() -> None:
    """契约文件可解析且包含来源信任分级与限制."""
    reg = registry()
    assert set(reg["sources"]) == {"runtime", "server", "system", "browser"}
    assert reg["sources"]["browser"]["may_claim_terminal_facts"] is False
    assert reg["envelope"]["limits"]["single_event_bytes"] == 16384
    path = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "agent-events-v1"
    )
    assert (path / "event-envelope.schema.json").exists()
    json.loads((path / "event-envelope.schema.json").read_text("utf-8"))
