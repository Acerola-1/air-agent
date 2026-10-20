"""Deterministic report gate: allowlist, evidence semantics and rendering."""

from __future__ import annotations

import uuid

from intelligent_tracing.claims import render_report
from intelligent_tracing.evidence import EvidenceRegistry, EvidenceRegistryError
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import (
    AllowedClaim,
    ClaimAllowlist,
    ClaimType,
    ReportPlan,
    ReportValidationResult,
    RuntimeScope,
    TracingRequest,
    ValidatedReport,
)


class ReportGate:
    """Validate every report plan before any user-visible publication."""

    def __init__(self, *, registry: EvidenceRegistry, policy: AnalysisPolicy) -> None:
        """Bind the gate to the trusted evidence registry and policy."""
        self._registry = registry
        self._policy = policy

    def validate_and_render(
        self,
        plan: ReportPlan,
        allowlist: ClaimAllowlist,
        request: TracingRequest,
        scope: RuntimeScope,
        *,
        report_id: str | None = None,
        run_id: str = "",
    ) -> ReportValidationResult:
        """Validate a plan; on failure return errors and never retry a model."""
        errors = self.validate_plan(plan, allowlist)
        if errors:
            return ReportValidationResult(ok=False, errors=tuple(errors))
        claims = {claim.claim_id: claim for claim in allowlist.claims}
        selected = [claims[claim_id] for claim_id in plan.claim_ids]
        semantic_errors = self._validate_claim_semantics(selected, request, scope)
        if semantic_errors:
            return ReportValidationResult(ok=False, errors=tuple(semantic_errors))
        report = render_report(
            request,
            allowlist,
            plan,
            scope=scope,
            report_id=report_id or f"rep_{uuid.uuid4().hex}",
            policy=self._policy,
            run_id=run_id,
        )
        text_errors = self._validate_text_boundary(report, allowlist)
        if text_errors:
            return ReportValidationResult(ok=False, errors=tuple(text_errors))
        return ReportValidationResult(ok=True, report=report)

    def validate_plan(self, plan: ReportPlan, allowlist: ClaimAllowlist) -> list[str]:
        """Structural allowlist checks; free text has no channel here."""
        errors: list[str] = []
        if plan.analysis_id != allowlist.analysis_id:
            errors.append("plan_analysis_mismatch")
        if plan.revision != allowlist.revision:
            errors.append("plan_revision_mismatch")
        allowed = {claim.claim_id for claim in allowlist.claims}
        if len(set(plan.claim_ids)) != len(plan.claim_ids):
            errors.append("duplicate_claim_id")
        unknown = [claim_id for claim_id in plan.claim_ids if claim_id not in allowed]
        if unknown:
            errors.append("claim_not_allowlisted:" + ",".join(sorted(unknown)))
        unknown_review = [
            claim_id for claim_id in plan.review_claim_ids if claim_id not in allowed
        ]
        if unknown_review:
            errors.append("review_claim_not_allowlisted")
        limitation_ids = {
            claim.claim_id
            for claim in allowlist.claims
            if claim.claim_type == ClaimType.LIMITATION
        }
        if not limitation_ids.issubset(set(plan.claim_ids)):
            errors.append("limitations_missing")
        process_ids = {
            claim.claim_id
            for claim in allowlist.claims
            if claim.claim_type == ClaimType.PROCESS_ASSESSMENT
        }
        if process_ids and not process_ids.issubset(set(plan.claim_ids)):
            errors.append("process_assessment_missing")
        return errors

    def _validate_claim_semantics(
        self,
        claims: list[AllowedClaim],
        request: TracingRequest,
        scope: RuntimeScope,
    ) -> list[str]:
        errors: list[str] = []
        for claim in claims:
            try:
                records = self._registry.get_many(
                    claim.supporting_evidence_ids,
                    scope=scope,
                    analysis_id=request.analysis_id,
                    revision=request.revision,
                )
                counter_records = self._registry.get_many(
                    claim.counter_evidence_ids,
                    scope=scope,
                    analysis_id=request.analysis_id,
                    revision=request.revision,
                )
            except EvidenceRegistryError as exc:
                errors.append(f"evidence_invalid:{claim.claim_id}:{exc}")
                continue
            products = {record.product for record in records}
            requirements = set(claim.semantic_requirements)
            if "data_unavailable" in requirements:
                reason = claim.parameters.get("unavailable_reason")
                if reason not in {
                    "station_data_unavailable",
                    "grid_data_unavailable",
                    "meteo_data_unavailable",
                }:
                    errors.append(f"unknown_unavailable_reason:{claim.claim_id}")
                if reason not in claim.limitations:
                    errors.append(f"unavailable_reason_not_declared:{claim.claim_id}")
            if "station_series" in requirements and "station_series" not in products:
                errors.append(f"missing_station_series:{claim.claim_id}")
            if "station_anomaly" in requirements and "station_anomaly" not in products:
                errors.append(f"missing_station_anomaly:{claim.claim_id}")
            if "coverage_sufficient" in requirements:
                if not claim.parameters.get("coverage_sufficient") or not any(
                    record.sample_count >= self._policy.quality.min_samples
                    for record in records
                ):
                    errors.append(f"coverage_insufficient:{claim.claim_id}")
            if "baseline_sufficient" in requirements:
                if not claim.parameters.get("baseline_sufficient") or not any(
                    record.product == "baseline_series"
                    and record.sample_count >= self._policy.baseline.min_samples
                    for record in records
                ):
                    errors.append(f"baseline_insufficient:{claim.claim_id}")
            if "spatial_evidence" in requirements:
                if not products & {"neighbor_series", "spatial_grid"}:
                    errors.append(f"missing_spatial_evidence:{claim.claim_id}")
                pattern = claim.parameters.get("pattern")
                if pattern in {"multi_station_sync", "regional_contiguous"}:
                    sync_count = int(claim.parameters.get("sync_neighbor_count") or 0)
                    grid_relation = claim.parameters.get("grid_source_relation")
                    contiguous = int(claim.parameters.get("grid_contiguous_cells") or 0)
                    independent_grid = (
                        grid_relation == "independent"
                        and contiguous >= self._policy.spatial.grid_min_contiguous_cells
                    )
                    if (
                        sync_count < self._policy.spatial.min_sync_neighbors
                        and not independent_grid
                    ):
                        errors.append(f"spatial_claim_unsupported:{claim.claim_id}")
                if pattern == "regional_contiguous":
                    grid_relation = claim.parameters.get("grid_source_relation")
                    if grid_relation in {"station_interpolated", "unknown"} and (
                        "spatial_grid" in products
                    ):
                        # Same-source/unknown grid cannot be the only support;
                        # station sync is checked above, so this is a retained
                        # limitation rather than a hard failure.
                        pass
            if "evaluation_evidence" in requirements:
                if "evaluation_metric" not in products:
                    errors.append(f"missing_evaluation_evidence:{claim.claim_id}")
                else:
                    profile = self._policy.require_supported(request.pollutant)
                    metric = claim.parameters.get("metric")
                    minimum = 6 if metric == "daily_max_8h" else 18
                    required = 8 if metric == "daily_max_8h" else 24
                    if claim.parameters.get("unit") != profile.unit:
                        errors.append(f"evaluation_unit_mismatch:{claim.claim_id}")
                    if int(claim.parameters.get("valid_hours") or 0) < minimum:
                        errors.append(
                            f"evaluation_validity_insufficient:{claim.claim_id}"
                        )
                    if int(claim.parameters.get("required_hours") or 0) != required:
                        errors.append(f"evaluation_window_mismatch:{claim.claim_id}")
            if "not_source_contribution" in requirements:
                forbidden = {
                    "contribution",
                    "source_probability",
                    "source_class",
                    "enterprise",
                }
                if forbidden & set(claim.parameters):
                    errors.append(f"evaluation_overclaim:{claim.claim_id}")
            if "wind_pairing" in requirements:
                if "wind_pairing" not in products:
                    errors.append(f"missing_wind_pairing:{claim.claim_id}")
                else:
                    if (
                        float(claim.parameters.get("sector_coverage") or 0.0)
                        < self._policy.wind.min_sector_coverage
                    ):
                        errors.append(f"sector_coverage_insufficient:{claim.claim_id}")
                    if float(claim.parameters.get("calm_ratio") or 0.0) > 0.5:
                        errors.append(f"calm_dominant:{claim.claim_id}")
                    if (
                        float(claim.parameters.get("resultant_length") or 0.0)
                        < self._policy.wind.min_resultant_length
                    ):
                        errors.append(f"direction_unstable:{claim.claim_id}")
                    if not any(
                        record.sample_count >= self._policy.wind.min_paired_samples
                        for record in records
                    ):
                        errors.append(f"wind_samples_insufficient:{claim.claim_id}")
            if "not_source_distance" in requirements:
                forbidden = {"distance_km", "source_distance", "source_probability"}
                if forbidden & set(claim.parameters):
                    errors.append(f"source_distance_inferred:{claim.claim_id}")
            if "lag_evidence" in requirements:
                if "lag_analysis" not in products:
                    errors.append(f"missing_lag_evidence:{claim.claim_id}")
                if (
                    abs(float(claim.parameters.get("r") or 0.0))
                    < self._policy.wind.lag_min_abs_r
                ):
                    errors.append(f"lag_r_insufficient:{claim.claim_id}")
                if int(claim.parameters.get("best_lag_hours") or 0) < 1:
                    errors.append(f"lag_not_positive:{claim.claim_id}")
                if not any(
                    record.sample_count >= self._policy.wind.lag_min_pairs
                    for record in records
                ):
                    errors.append(f"lag_pairs_insufficient:{claim.claim_id}")
            if "process_evidence" in requirements:
                if not products & {"station_series", "station_anomaly"}:
                    errors.append(f"process_missing_station:{claim.claim_id}")
                assessment = claim.parameters.get("assessment")
                if assessment in {
                    "transport_association",
                    "local_accumulation_consistent",
                    "mixed",
                }:
                    if not products & {"neighbor_series", "spatial_grid"}:
                        errors.append(f"process_missing_spatial:{claim.claim_id}")
                    if "wind_pairing" not in products:
                        errors.append(f"process_missing_wind:{claim.claim_id}")
            if "transport_association" in requirements:
                components = set(claim.parameters.get("transport_components") or [])
                required = {"spatial_sync", "wind_direction_stable"}
                if not required.issubset(components):
                    errors.append(f"transport_components_missing:{claim.claim_id}")
                if not components & {
                    "temporal_lead",
                    "lag_correlation",
                    "upwind_gradient",
                }:
                    errors.append(
                        f"transport_direction_evidence_missing:{claim.claim_id}"
                    )
                if not products & {"lag_analysis", "temporal_lead"}:
                    errors.append(
                        f"transport_temporal_evidence_missing:{claim.claim_id}"
                    )
                if (
                    not counter_records
                    and not claim.parameters.get("counter_evidence")
                    and not claim.parameters.get("alternative_explanations")
                ):
                    errors.append(
                        f"transport_counter_evidence_missing:{claim.claim_id}"
                    )
            if "unverified_only" in requirements:
                if (
                    claim.parameters.get("status") != "unverified"
                    or claim.parameters.get("source_class") != "unidentified"
                ):
                    errors.append(f"source_hypothesis_not_unverified:{claim.claim_id}")
                text = str(claim.parameters.get("hypothesis") or "")
                if any(
                    word in text
                    for word in (
                        "已确认",
                        "确定是",
                        "贡献率",
                        "概率",
                        "某企业",
                        "某工厂",
                    )
                ):
                    errors.append(f"source_hypothesis_overclaim:{claim.claim_id}")
            if "no_source_inventory" in requirements:
                if "emission_inventory" in products:
                    errors.append(f"unexpected_inventory_evidence:{claim.claim_id}")
            if "limitation_text" in requirements:
                code = claim.parameters.get("code")
                if code not in self._limitation_codes(claim):
                    errors.append(f"unknown_limitation:{claim.claim_id}")
            if "not_causation" in requirements:
                if "因果" in str(claim.parameters) and "不" not in str(
                    claim.parameters
                ):
                    errors.append(f"causal_overclaim:{claim.claim_id}")
        return errors

    def _limitation_codes(self, claim: AllowedClaim) -> set[str]:
        from intelligent_tracing.claims import _LIMITATION_TEXT

        return set(_LIMITATION_TEXT)

    def _validate_text_boundary(
        self, report: ValidatedReport, allowlist: ClaimAllowlist
    ) -> list[str]:
        errors: list[str] = []
        required = {
            "trajectory_unavailable",
            "emission_inventory_missing",
            "no_chemical_composition",
        }
        if not required.issubset(set(report.limitations)):
            errors.append("fixed_limitations_missing")
        if "无法计算贡献率" not in report.body_markdown:
            errors.append("contribution_refusal_missing")
        if report.content_sha256 != report.compute_digest():
            errors.append("report_digest_mismatch")
        for line in report.body_markdown.splitlines():
            if any(
                word in line
                for word in ("已确认企业", "确定是某", "来源概率为", "贡献率为")
            ):
                if not any(
                    safe in line for safe in ("不能", "无法", "不包含", "未", "待核查")
                ):
                    errors.append("attribution_overclaim_in_text")
                    break
        return errors
