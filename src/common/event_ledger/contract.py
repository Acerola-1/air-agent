"""事件台账契约加载与校验（v1）.

契约单一事实源在 `docs/contracts/agent-events-v1/`（JSON），本模块只做加载与
校验，不重复定义字典含义。TS 侧 `frontend/lib/agent-events/contract.ts` 实现
同一组规则，共享同一份 `samples/samples.json` 做双语言契约测试。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# 运行期读取**包内副本**：langgraph-api 容器只挂载 `./src`，仓库 docs/ 不在
# 容器内。`docs/contracts/agent-events-v1/` 是发布契约，两者由契约测试断言一致
# （test_embedded_copy_matches_published）。
_EMBEDDED_DIR = Path(__file__).resolve().parent / "contract_data"
_PUBLISHED_DIR = (
    Path(__file__).resolve().parents[3] / "docs" / "contracts" / "agent-events-v1"
)


def _contract_path(name: str) -> Path:
    """优先包内副本；缺失时回退发布目录（本地未同步时的兜底）."""
    embedded = _EMBEDDED_DIR / name
    if embedded.exists():
        return embedded
    return _PUBLISHED_DIR / name


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TS_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

_OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")
# 载荷内需要按字典校验枚举的字段 → 字典来源
_ENUM_FIELDS = ("state", "origin", "phase", "evidence_source", "operation_kind")

# 模型用量计数窄例外：仅 runtime.model_usage 的 runtime
# 来源、payload **顶层**、以下登记白名单键允许"非布尔非负整数"计数。字符串 /
# bool / 负数 / 浮点 / 嵌套键 / 其他类型一律仍走原规则；禁止键（token 等）扫描
# 先于本例外，嵌套 secret 照常硬拒绝。不能扩大到其他 event_type 或来源。
_NUMERIC_USAGE_EVENT_TYPES = frozenset({"runtime.model_usage"})
_NUMERIC_USAGE_TOP_LEVEL_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    }
)


@lru_cache
def registry() -> dict[str, Any]:
    """加载事件来源/类型登记（契约单一事实源）."""
    return json.loads(_contract_path("event-registry.json").read_text("utf-8"))


@lru_cache
def states() -> dict[str, Any]:
    """加载运行状态字典."""
    return json.loads(_contract_path("states.json").read_text("utf-8"))


@lru_cache
def metrics() -> dict[str, Any]:
    """加载指标口径字典."""
    return json.loads(_contract_path("metrics.json").read_text("utf-8"))


@lru_cache
def samples() -> dict[str, Any]:
    """加载合法/非法样本集."""
    return json.loads(_contract_path("samples.json").read_text("utf-8"))


@dataclass(frozen=True)
class ValidationResult:
    """校验结果：valid + 原因码（原因码取自契约 reason_codes."""

    valid: bool
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """返回校验结果的字典形状."""
        return {"valid": self.valid, "reason": self.reason, "detail": self.detail}


def _uuid_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _iter_keys(node: Any) -> Any:
    """递归产出所有 dict 键（用于禁止键扫描."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


def canonical_digest(payload: Any) -> str:
    """规范载荷摘要：sha256(canonical json)。去重与冲突判定唯一依据."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _enum_values(payload: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    st = states()
    return {
        "state": tuple(st["states"].keys()),
        "origin": tuple(st["cancel_origins"].keys()),
        "phase": tuple(st["cancel_phases"].keys()),
        "evidence_source": tuple(st["evidence_sources"].keys()),
        "operation_kind": _OPERATION_KINDS,
    }


def capability_payload_valid(payload: dict[str, Any], rules: dict[str, Any]) -> bool:
    """Validate only the new metadata fact; never inspect business result fields."""
    if (
        payload.get("capability_kind") not in rules["kinds"]
        or payload.get("action") not in rules["actions"]
        or payload.get("status") not in rules["statuses"]
    ):
        return False
    for key in ("capability_id",):
        if key in payload and (
            not isinstance(payload[key], str)
            or not re.fullmatch(rules["id_pattern"], payload[key])
        ):
            return False
    for key in ("capability_version", "catalog_version"):
        if key in payload and (
            not isinstance(payload[key], str)
            or not re.fullmatch(rules["version_pattern"], payload[key])
        ):
            return False
    for key in ("candidate_ids", "source_ids"):
        if key in payload:
            value = payload[key]
            if (
                not isinstance(value, list)
                or len(value) > 128
                or any(
                    not isinstance(v, str)
                    or not (
                        v == "independent"
                        and key == "source_ids"
                        or re.fullmatch(rules["id_pattern"], v)
                    )
                    for v in value
                )
            ):
                return False
    for key in rules["counts"]:
        if key in payload and (type(payload[key]) is not int or payload[key] < 0):
            return False
    if "candidate_count" in payload and payload["candidate_count"] != len(
        payload.get("candidate_ids", [])
    ):
        return False
    if payload["action"] in {
        "selected",
        "loaded",
        "activated",
        "reused",
        "executed",
    } and (not payload.get("capability_id") or not payload.get("capability_version")):
        return False
    return True


def validate_event(event: Any) -> ValidationResult:
    """按契约 README 的顺序判定事件；返回首个命中的原因码."""
    reg = registry()
    env = reg["envelope"]
    if not isinstance(event, dict):
        return ValidationResult(False, "missing_field", "event must be an object")

    # 1. 主版本
    version = event.get("schema_version")
    if not isinstance(version, str) or not version.startswith("1."):
        return ValidationResult(
            False, "schema_version_unsupported", f"schema_version={version!r}"
        )

    # 2. 必填
    for field in env["required"]:
        if event.get(field) is None:
            return ValidationResult(False, "missing_field", field)

    # 3. UUID 标识
    for field in ("event_id", "tenant_id", "user_id", "membership_id"):
        if not _uuid_ok(event.get(field)):
            return ValidationResult(False, "bad_uuid", field)

    # 4. 时间
    if not (
        isinstance(event.get("occurred_at"), str)
        and _TS_Z_RE.match(event["occurred_at"])
    ):
        return ValidationResult(False, "bad_timestamp", "occurred_at")

    # 5. 类型登记
    event_type = str(event["event_type"])
    entry = reg["event_types"].get(event_type)
    if entry is None:
        return ValidationResult(False, "unknown_type", event_type)

    # 6. 启用
    if not entry.get("enabled"):
        return ValidationResult(False, "type_not_enabled", event_type)

    # 7. 来源
    source = str(event.get("source") or "")
    if source not in entry["sources"]:
        return ValidationResult(
            False, "source_not_allowed", f"source={source} type={event_type}"
        )

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ValidationResult(False, "missing_field", "payload")

    # 8. 禁止键（任意层级）：票据/凭据/全文永远是硬拒绝，优先于白名单判定——
    #    即使某个危险键碰巧被登记进白名单也必须拒绝。唯一例外：runtime.model_usage
    #    的 runtime 来源、payload **顶层**、_NUMERIC_USAGE_TOP_LEVEL_KEYS 内的
    #    登记键——设计 §3 授权的窄例外（usage 字段名必然含 "token" 子串）。
    #    数值形状非法的例外键跳过 forbidden 判定，落到 8.5 给出专用原因码。
    patterns = tuple(env["forbidden_payload_key_patterns"])
    numeric_exception_active = (
        event_type in _NUMERIC_USAGE_EVENT_TYPES and source == "runtime"
    )
    for key in _iter_keys(payload):
        low = key.lower()
        if not any(pat in low for pat in patterns):
            continue
        if numeric_exception_active and key in _NUMERIC_USAGE_TOP_LEVEL_KEYS:
            continue
        return ValidationResult(False, "forbidden_payload_key", key)

    # 8.5 窄例外的显式类型判定：顶层登记键必须是非布尔非负整数；字符串 / bool /
    # 负数 / 浮点给出专用原因码。嵌套键不在例外内（_iter_keys 已扫描整棵树）。
    if numeric_exception_active:
        for key in _NUMERIC_USAGE_TOP_LEVEL_KEYS:
            if key in payload:
                value = payload[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return ValidationResult(
                        False, "usage_tokens_not_non_negative_int", key
                    )

    # 9. 字段白名单
    allowed = set(entry.get("payload_fields") or [])
    for key in payload:
        if key not in allowed:
            return ValidationResult(False, "payload_field_not_allowed", key)

    # 10. 载荷必填
    for field in entry.get("required_payload_fields") or []:
        if payload.get(field) is None:
            return ValidationResult(False, "missing_required_payload_field", field)

    if event_type == "runtime.capability" and not capability_payload_valid(
        payload, reg["capability_fact"]
    ):
        return ValidationResult(
            False, "invalid_enum_value", "runtime.capability metadata"
        )

    # 11. 枚举字典
    enums = _enum_values(payload)
    for field in _ENUM_FIELDS:
        if field in payload:
            value = payload[field]
            if value not in enums[field]:
                return ValidationResult(
                    False, "invalid_enum_value", f"{field}={value!r}"
                )

    # 12. 单条大小（每条上限对全部来源一致；整批上限在 validate_batch 检查）
    size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
    limit = int(env["limits"]["single_event_bytes"])
    if size > limit:
        return ValidationResult(False, "payload_too_large", f"{size}>{limit}")

    return ValidationResult(True, "ok")


@dataclass(frozen=True)
class BatchResult:
    """批量逐项结果：整体不接受时 items 仍逐项给出原因（不宣称整批成功."""

    accepted: bool
    batch_reason: str
    items: tuple[ValidationResult, ...]

    def as_dict(self) -> dict[str, Any]:
        """返回批量校验结果的字典形状."""
        return {
            "accepted": self.accepted,
            "batch_reason": self.batch_reason,
            "items": [item.as_dict() for item in self.items],
        }


def validate_batch(events: Any, *, source: str) -> BatchResult:
    """批量校验：先判批量上限（条数/整批字节），再逐项判定."""
    env = registry()["envelope"]
    if not isinstance(events, list) or not events:
        return BatchResult(False, "empty_batch", ())
    items = tuple(validate_event(ev) for ev in events)
    if source == "browser":
        max_items = int(env["limits"]["browser_batch_events"])
        if len(events) > max_items:
            return BatchResult(False, f"batch_too_many>{max_items}", items)
        total = len(json.dumps(events, ensure_ascii=False).encode("utf-8"))
        max_bytes = int(env["limits"]["browser_batch_bytes"])
        if total > max_bytes:
            return BatchResult(False, f"batch_too_large:{total}>{max_bytes}", items)
    return BatchResult(all(i.valid for i in items), "ok", items)


def overrides_from_server(
    event: dict[str, Any], trusted: dict[str, str]
) -> dict[str, Any]:
    """服务端身份覆盖：客户端提交的可信字段一律替换为服务端上下文值.

    返回新字典，不改动入参。`trusted` 至少含 tenant_id/user_id/membership_id/source
    """
    out = dict(event)
    for field in registry()["envelope"]["server_overrides_client"]:
        if field in trusted:
            out[field] = trusted[field]
    return out


def enabled_event_types() -> frozenset[str]:
    """服务端身份覆盖后的新事件字典."""
    return frozenset(
        name
        for name, entry in registry()["event_types"].items()
        if entry.get("enabled")
    )


def reserved_event_types() -> frozenset[str]:
    """当前已启用的事件类型集合."""
    return frozenset(
        name
        for name, entry in registry()["event_types"].items()
        if not entry.get("enabled")
    )
