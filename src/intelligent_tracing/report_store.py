"""Validated report persistence and scoped reread."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from intelligent_tracing.schemas import RuntimeScope, ValidatedReport


class ReportRereadError(RuntimeError):
    """A stored report cannot be verified for the current scope."""


class ReportStore(Protocol):
    """Store/retrieve immutable validated reports."""

    async def save(self, report: ValidatedReport) -> None:
        """Persist a report after its digest and scope are validated."""
        ...

    async def get(
        self, report_id: str, *, scope: RuntimeScope
    ) -> ValidatedReport | None:
        """Return a report only when scope and digest still verify."""
        ...


@dataclass
class InMemoryReportStore:
    """Controlled in-memory store for G0 tests and fixture spikes."""

    def __post_init__(self) -> None:
        """Initialise the private mapping."""
        self._reports: dict[str, str] = {}

    async def save(self, report: ValidatedReport) -> None:
        """Store the canonical JSON; reject digest mismatches."""
        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告摘要不一致，拒绝持久化")
        self._reports[report.report_id] = report.model_dump_json()

    async def get(
        self, report_id: str, *, scope: RuntimeScope
    ) -> ValidatedReport | None:
        """Read and re-verify scope, revision and digest."""
        raw = self._reports.get(report_id)
        if raw is None:
            return None
        report = ValidatedReport.model_validate(json.loads(raw))
        if report.scope != scope:
            raise ReportRereadError("报告作用域与当前身份不一致")
        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告内容摘要校验失败")
        return report


class PersistentReportStore:
    """Production report store over the scoped artifact table.

    Ownership and retention stay in ``common.storage.artifact_db``; this store
    only adds report-specific validation (run binding, scope, digest, kind).
    """

    async def save(self, report: ValidatedReport) -> None:
        """Persist one validated report; never store an unbound or tampered one."""
        from common.storage.artifact_db import save_tool_artifact

        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告摘要不一致，拒绝持久化")
        if not report.run_id:
            raise ReportRereadError("报告缺少运行归属，拒绝持久化")
        await save_tool_artifact(
            tool_call_id=report.report_id,
            tool_name="validated_tracing_report",
            raw_payload=report.model_dump_json(),
            tenant_id=report.scope.tenant_id,
            user_id=report.scope.user_id,
            thread_id=report.scope.thread_id,
            run_id=report.run_id,
            summary=report.body_markdown[:500],
            kind="validated_report",
            processing_metadata={
                "analysis_id": report.analysis_id,
                "revision": str(report.revision),
                "policy_version": report.policy_version,
                "gate_version": report.gate_version,
                "content_sha256": report.content_sha256,
                "business_code": report.scope.business_code,
                "business_policy_version": report.scope.business_policy_version,
            },
        )

    async def get(
        self, report_id: str, *, scope: RuntimeScope
    ) -> ValidatedReport | None:
        """Read by report id within the current scope and re-verify its digest."""
        from common.storage.artifact_db import (
            ArtifactExpired,
            get_tool_artifact_by_call,
        )

        try:
            row = await get_tool_artifact_by_call(
                report_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                thread_id=scope.thread_id,
            )
        except ArtifactExpired as exc:
            raise ReportRereadError("报告已过期") from exc
        if row is None:
            return None
        if row.get("kind") != "validated_report":
            raise ReportRereadError("归档不是已验证报告")
        report = ValidatedReport.model_validate_json(row["raw_payload"])
        if report.scope != scope:
            raise ReportRereadError("报告作用域与当前身份不一致")
        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告内容摘要校验失败")
        if not report.run_id or str(row.get("run_id") or "") != report.run_id:
            raise ReportRereadError("报告运行归属不一致")
        return report


def default_report_store() -> ReportStore:
    """Choose the production store when the runtime DB is configured."""
    import os

    if os.getenv("RUNTIME_DATABASE_URI"):
        return PersistentReportStore()
    return InMemoryReportStore()
