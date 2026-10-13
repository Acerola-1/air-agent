"""事件契约加载与校验（admin 侧）。

单一事实源同样是 `docs/contracts/agent-events-v1/*.json`。Agent 侧实现在
`src/common/event_ledger/contract.py`（独立 venv，不能互相 import），因此规则
在此**复用同一份契约文件**实现一遍；`tests/test_event_ledger_api.py` 用同一组
样本比对两侧判定，任一侧漂移即失败（跨项目契约测试）。

规则顺序见 `docs/contracts/agent-events-v1/README.md`「校验语义」。
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# app/services/event_contract.py → services → app → api → admin → repo root
_CONTRACT_DIR = Path(__file__).resolve().parents[4] / "docs" / "contracts" / "agent-events-v1"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TS_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")
_ENUM_FIELDS = ("state", "origin", "phase", "evidence_source", "operation_kind")


@lru_cache
def registry() -> dict[str, Any]:
    return json.loads((_CONTRACT_DIR / "event-registry.json").read_text("utf-8"))


@lru_cache
def states() -> dict[str, Any]:
    return json.loads((_CONTRACT_DIR / "states.json").read_text("utf-8"))


def canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _uuid_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _iter_keys(node: Any) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


def _enums() -> dict[str, tuple[str, ...]]:
    st = states()
    return {
        "state": tuple(st["states"].keys()),
        "origin": tuple(st["cancel_origins"].keys()),
        "phase": tuple(st["cancel_phases"].keys()),
        "evidence_source": tuple(st["evidence_sources"].keys()),
        "operation_kind": _OPERATION_KINDS,
    }


def validate_event(event: Any, *, trusted_source: str | None = None) -> tuple[bool, str, str]:
    """返回 (valid, reason, detail)。语义与 Agent 侧一致。"""
    reg = registry()
    env = reg["envelope"]
    if not isinstance(event, dict):
        return False, "missing_field", "event must be an object"
    version = event.get("schema_version")
    if not isinstance(version, str) or not version.startswith("1."):
        return False, "schema_version_unsupported", str(version)
    for field in env["required"]:
        if event.get(field) is None:
            return False, "missing_field", str(field)
    for field in ("event_id", "tenant_id", "user_id", "membership_id"):
        if not _uuid_ok(event.get(field)):
            return False, "bad_uuid", str(field)
    if not (isinstance(event.get("occurred_at"), str) and _TS_Z_RE.match(event["occurred_at"])):
        return False, "bad_timestamp", "occurred_at"
    event_type = str(event["event_type"])
    entry = reg["event_types"].get(event_type)
    if entry is None:
        return False, "unknown_type", event_type
    if not entry.get("enabled"):
        return False, "type_not_enabled", event_type
    source = trusted_source or str(event.get("source") or "")
    if source not in entry["sources"]:
        return False, "source_not_allowed", f"source={source} type={event_type}"
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False, "missing_field", "payload"
    patterns = tuple(env["forbidden_payload_key_patterns"])
    for key in _iter_keys(payload):
        low = key.lower()
        if any(pat in low for pat in patterns):
            return False, "forbidden_payload_key", key
    allowed = set(entry.get("payload_fields") or [])
    for key in payload:
        if key not in allowed:
            return False, "payload_field_not_allowed", key
    for field in entry.get("required_payload_fields") or []:
        if payload.get(field) is None:
            return False, "missing_required_payload_field", field
    enums = _enums()
    for field in _ENUM_FIELDS:
        if field in payload and payload[field] not in enums[field]:
            return False, "invalid_enum_value", f"{field}={payload[field]!r}"
    size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
    if size > int(env["limits"]["single_event_bytes"]):
        return False, "payload_too_large", str(size)
    return True, "ok", ""


def validate_batch(events: list[Any], *, source: str) -> dict[str, Any]:
    env = registry()["envelope"]
    if not events:
        return {"accepted": False, "batch_reason": "empty_batch", "items": []}
    items = []
    for event in events:
        valid, reason, detail = validate_event(event, trusted_source=source)
        items.append({"valid": valid, "reason": reason, "detail": detail})
    if source == "browser":
        if len(events) > int(env["limits"]["browser_batch_events"]):
            return {"accepted": False, "batch_reason": "batch_too_many", "items": items}
        total = len(json.dumps(events, ensure_ascii=False).encode("utf-8"))
        if total > int(env["limits"]["browser_batch_bytes"]):
            return {"accepted": False, "batch_reason": f"batch_too_large:{total}", "items": items}
    return {"accepted": all(i["valid"] for i in items), "batch_reason": "ok", "items": items}


def enabled_event_types() -> frozenset[str]:
    return frozenset(k for k, v in registry()["event_types"].items() if v.get("enabled"))


def reserved_event_types() -> frozenset[str]:
    return frozenset(k for k, v in registry()["event_types"].items() if not v.get("enabled"))
