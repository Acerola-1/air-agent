"""Execution-layer evidence registry.

Evidence is registered by deterministic data/analysis code, never by a model.
Records are bound to a trusted runtime scope, request revision and canonical
parameter/raw-data digests.  Raw payloads stay private to the registry.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from typing import Any

from intelligent_tracing.schemas import EvidenceRecord, RuntimeScope


def canonical_digest(payload: Any) -> str:
    """Stable digest for parameters or raw payloads."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceRegistryError(RuntimeError):
    """Evidence registration or lookup violated scope/revision integrity."""


class EvidenceRegistry:
    """In-memory evidence registry with scope and revision isolation."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._records: dict[str, EvidenceRecord] = {}
        self._raw: dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(
        self,
        *,
        scope: RuntimeScope,
        analysis_id: str,
        revision: int,
        tool_id: str,
        parameters: dict[str, Any],
        source_type: str,
        product: str,
        fetched_at: datetime,
        valid_from: datetime,
        valid_to: datetime,
        unit: str,
        algorithm_version: str,
        analysis_policy_version: str,
        raw_payload: Any,
        quality_flags: tuple[str, ...] = (),
        sample_count: int = 0,
        derived_from: tuple[str, ...] = (),
        source_relation: str = "not_applicable",
        nwp_run_time: datetime | None = None,
        nwp_lead_hours: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> EvidenceRecord:
        """Register one evidence record and return its stable view."""
        parameter_digest = canonical_digest(parameters)
        raw_digest = canonical_digest(raw_payload)
        identity = {
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "thread_id": scope.thread_id,
            "analysis_id": analysis_id,
            "revision": revision,
            "tool_id": tool_id,
            "parameter_digest": parameter_digest,
            "raw_digest": raw_digest,
        }
        evidence_id = "ev_" + canonical_digest(identity)[:32]
        with self._lock:
            existing = self._records.get(evidence_id)
            if existing is not None:
                if existing.raw_digest != raw_digest:
                    raise EvidenceRegistryError("同一证据 ID 出现不同内容")
                self._assert_scope(existing, scope, analysis_id, revision)
                return existing
        record = EvidenceRecord(
            evidence_id=evidence_id,
            analysis_id=analysis_id,
            revision=revision,
            tool_id=tool_id,
            parameter_digest=parameter_digest,
            source_type=source_type,  # type: ignore[arg-type]
            product=product,
            fetched_at=fetched_at,
            valid_from=valid_from,
            valid_to=valid_to,
            timezone="Asia/Shanghai",
            unit=unit,
            quality_flags=tuple(sorted(set(quality_flags))),
            sample_count=sample_count,
            raw_digest=raw_digest,
            algorithm_version=algorithm_version,
            analysis_policy_version=analysis_policy_version,
            derived_from=derived_from,
            source_relation=source_relation,  # type: ignore[arg-type]
            nwp_run_time=nwp_run_time,
            nwp_lead_hours=nwp_lead_hours,
            metadata=metadata or {},
            business_code=scope.business_code,
            business_policy_version=scope.business_policy_version,
            business_permission=scope.business_permission,
            scope=scope,
        )
        with self._lock:
            self._records[evidence_id] = record
            self._raw[evidence_id] = raw_payload
        return record

    def get(
        self,
        evidence_id: str,
        *,
        scope: RuntimeScope,
        analysis_id: str,
        revision: int,
    ) -> EvidenceRecord:
        """Resolve an evidence id, rejecting cross-scope/old revisions."""
        with self._lock:
            record = self._records.get(evidence_id)
        if record is None:
            raise EvidenceRegistryError("证据不存在")
        self._assert_scope(record, scope, analysis_id, revision)
        return record

    def get_many(
        self,
        evidence_ids: tuple[str, ...] | list[str],
        *,
        scope: RuntimeScope,
        analysis_id: str,
        revision: int,
    ) -> tuple[EvidenceRecord, ...]:
        """Resolve many evidence ids with the same integrity checks."""
        return tuple(
            self.get(
                evidence_id,
                scope=scope,
                analysis_id=analysis_id,
                revision=revision,
            )
            for evidence_id in evidence_ids
        )

    def raw_payload(self, evidence_id: str, *, scope: RuntimeScope) -> Any:
        """Return a private raw payload only to trusted execution code."""
        record = self._records.get(evidence_id)
        if record is None:
            raise EvidenceRegistryError("证据不存在")
        if record.scope.tenant_id != scope.tenant_id:
            raise EvidenceRegistryError("证据作用域不一致")
        return self._raw.get(evidence_id)

    def _assert_scope(
        self,
        record: EvidenceRecord,
        scope: RuntimeScope,
        analysis_id: str,
        revision: int,
    ) -> None:
        if (
            record.analysis_id != analysis_id
            or record.revision != revision
            or record.scope.tenant_id != scope.tenant_id
            or record.scope.user_id != scope.user_id
            or record.scope.thread_id != scope.thread_id
            or record.scope.graph_id != scope.graph_id
            or record.scope.context_version != scope.context_version
            or record.scope.epoch != scope.epoch
            or record.scope.business_code != scope.business_code
            or record.scope.business_policy_version != scope.business_policy_version
            or record.scope.business_permission != scope.business_permission
            or record.business_code != scope.business_code
            or record.business_policy_version != scope.business_policy_version
        ):
            raise EvidenceRegistryError("证据不属于当前请求作用域或修订")


def require_semantic(
    records: tuple[EvidenceRecord, ...],
    *,
    products: set[str] | None = None,
    source_types: set[str] | None = None,
    min_samples: int = 0,
    forbid_source_relations: set[str] | None = None,
) -> str | None:
    """Return an error string when records do not satisfy semantic requirements."""
    if not records:
        return "缺少证据"
    if products is not None and not any(r.product in products for r in records):
        return "证据产品不匹配"
    if source_types is not None and not any(
        r.source_type in source_types for r in records
    ):
        return "证据来源类型不匹配"
    if any(r.sample_count < min_samples for r in records):
        return "证据样本数不足"
    if forbid_source_relations and any(
        r.source_relation in forbid_source_relations for r in records
    ):
        return "证据来源关系不满足独立验证要求"
    return None
