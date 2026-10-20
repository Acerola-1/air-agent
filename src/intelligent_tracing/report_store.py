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


class ArtifactReportStore:
    """G1 adapter over the existing scoped artifact table.

    The adapter is intentionally thin: it delegates storage ownership and
    retention to ``common.storage.artifact_db`` and re-verifies the report
    digest/scope on read.  It is not exercised by G0 fixture tests.
    """

    async def save(self, report: ValidatedReport, *, run_id: str) -> str:
        """Persist the report as a validated-report artifact."""
        from common.storage.artifact_db import save_tool_artifact

        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告摘要不一致，拒绝持久化")
        return await save_tool_artifact(
            tool_call_id=report.report_id,
            tool_name="validated_tracing_report",
            raw_payload=report.model_dump_json(),
            tenant_id=report.scope.tenant_id,
            user_id=report.scope.user_id,
            thread_id=report.scope.thread_id,
            run_id=run_id,
            summary=report.body_markdown[:500],
            kind="validated_report",
            processing_metadata={
                "analysis_id": report.analysis_id,
                "revision": str(report.revision),
                "policy_version": report.policy_version,
                "gate_version": report.gate_version,
                "content_sha256": report.content_sha256,
            },
        )

    async def get(
        self,
        report_id: str,
        *,
        scope: RuntimeScope,
        run_id: str,
    ) -> ValidatedReport | None:
        """Read and re-verify a report from the scoped artifact table."""
        from common.storage.artifact_db import get_tool_artifact

        record = await get_tool_artifact(
            report_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            thread_id=scope.thread_id,
        )
        if record is None:
            return None
        report = ValidatedReport.model_validate_json(record["raw_payload"])
        if report.scope != scope:
            raise ReportRereadError("报告作用域与当前身份不一致")
        if report.content_sha256 != report.compute_digest():
            raise ReportRereadError("报告内容摘要校验失败")
        return report
