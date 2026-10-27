"""Deterministic claim allowlist and controlled report rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from intelligent_tracing.policy import FIXED_LIMITATIONS, AnalysisPolicy
from intelligent_tracing.schemas import (
    AllowedClaim,
    ClaimAllowlist,
    ClaimType,
    KnowledgeBasisItem,
    MeteoFindings,
    ProcessResult,
    ReportPlan,
    RuntimeScope,
    SpatialFindings,
    StationFindings,
    TracingRequest,
    ValidatedReport,
)

_LIMITATION_TEXT = {
    "trajectory_unavailable": "当前不提供轨迹/PSCF分析，不能据此判断传输路径。",
    "emission_inventory_missing": "缺少排放源清单，不能确认具体企业或排口。",
    "no_chemical_composition": "缺少化学组分资料，不能判断二次生成或源类贡献。",
    "coverage_insufficient": "目标站点数据覆盖不足，不能把未知当作无异常。",
    "baseline_insufficient": "比较基线覆盖或样本不足，未生成相对异常结论。",
    "pblh_unavailable": "边界层高度不可用，未用其他字段替代。",
    "nwp_available_not_used_for_wind": "模式气象产品可用，但风场配对优先使用观测气象序列。",
    "nwp_unavailable": "未取得模式气象产品，风场与边界层证据受限。",
    "grid_station_interpolated": "网格与站点同源（站点插值），不能当作独立交叉验证。",
    "grid_source_unknown": "网格来源关系未知，不能据此增强独立证据强度。",
    "neighbor_coverage_insufficient": "部分邻站数据覆盖不足，未纳入同步判断。",
    "valid_neighbors_insufficient": "有效邻站数量不足，空间格局结论受限。",
    "wind_pairs_insufficient": "风场与浓度配对样本不足，未给出方向级结论。",
    "calm_dominant": "静风比例较高，风向不具代表性。",
    "direction_unstable": "风向不稳定，未强行给出主导来向。",
    "sector_coverage_insufficient": "扇区覆盖不足，未给出方向级结论。",
    "wind_direction_association_not_source": "风场关联不等于来源或距离。",
    "process_association_not_causation": "过程解释仅为关联，不是因果确认或贡献率。",
    "lag_correlation_not_causal": "滞后相关仅为统计关联，不是因果或概率。",
    "target_anomaly_unknown": "目标异常状态无法判断，未生成依赖异常分段的空间/过程结论。",
    "grid_topology_unknown": "网格拓扑未知，未推断连片范围。",
    "grid_time_inconsistent": "网格时刻不一致或未知，未拼接连片范围。",
    "grid_bounded_snapshot": "网格仅为有界同刻样本，不能代表整个过程或全区覆盖。",
    "neighbors_truncated": "邻站按距离有界选取，存在未读取的半径内候选。",
    "station_history_unavailable": "站点辅助历史获取失败，基线与滑动统计按实际覆盖处理。",
    "nwp_time_inconsistent": "模式起报或时效信息矛盾，相关字段未用于过程判断。",
    "no_target_anomaly": "目标站未检出异常，未生成空间/过程结论。",
    "neighbor_geometry_unknown": "邻站几何未知，未纳入半径调查。",
    "constant_baseline": "基线方差为零，异常判定同时依赖绝对变化量。",
    "station_data_unavailable": "站点数据获取失败，未用其他数据替代。",
    "grid_data_unavailable": "网格数据获取失败，空间格局未知。",
    "meteo_data_unavailable": "气象数据获取失败，风场与边界层证据不可用。",
    "process_weather_or_spatial_unavailable": "气象或空间数据不可用，未形成过程归因。",
    "budget_exhausted": "运行预算已耗尽，以上为已验证的部分结果。",
    "evaluation_profile_not_defined": "该污染物尚无独立评价口径，未套用 PM2.5 评价。",
    "partial_8h_window": "8小时窗口有效小时不足8小时，按策略保留部分窗口。",
    "cross_day_8h_window": "8小时窗口跨日，已按定义纳入并按跨日口径标注。",
    "pm25_24h_sliding_supplementary": "24小时滑动均值只作补充统计，不是自然日均或法规评价。",
    "partial_24h_window": "24小时窗口有效小时不足24小时，按策略保留部分窗口。",
    "wind_speed_invalid": "风速字段超出策略范围，已按缺测处理。",
    "wind_direction_invalid": "风向字段超出策略范围，已按缺测处理。",
    "wind_component_invalid": "风分量字段超出策略范围，已按缺测处理。",
    "humidity_invalid": "湿度字段超出策略范围，已按缺测处理。",
    "pressure_invalid": "气压字段超出策略范围，已按缺测处理。",
    "temperature_invalid": "温度字段超出策略范围，已按缺测处理。",
    "pblh_missing": "边界层高度缺测，未用其他字段替代。",
    "pblh_out_of_range": "边界层高度超出策略范围，已按缺测处理。",
    "nwp_run_time_unknown": "模式产品缺少起报时间，来源时效未知。",
    "naive_or_unknown_timestamp": "时间戳缺少时区，未纳入分析。",
    "not_hour_aligned": "时间戳未对齐整点，未纳入小时序列。",
    "missing_value": "存在缺测值，已按缺测处理。",
    "unknown_unit": "单位无法识别，未纳入分析。",
    "negative_concentration": "负浓度超出策略范围，未纳入分析。",
    "concentration_out_of_range": "浓度超出策略范围，未纳入分析。",
    "unit_converted": "浓度单位已按策略换算。",
    "duplicate_timestamps": "存在重复小时记录，保留最后一条。",
    "gap_exceeds_policy": "连续缺口超过策略限制。",
    "nwp_source": "部分气象字段来自历史模式产品，已保留来源与时效限制。",
}

_ASSESSMENT_TEXT = {
    "transport_association": "与上风向传输相符（仅为关联，不是因果确认）",
    "local_accumulation_consistent": "与静稳条件下的局地累积相符",
    "mixed": "证据混合，存在替代解释，未形成单一过程结论",
    "insufficient": "证据不足，未形成过程结论",
}

_SPATIAL_TEXT = {
    "isolated": "孤立异常",
    "multi_station_sync": "多站同步",
    "regional_contiguous": "区域连片",
    "mixed": "混合空间格局",
    "unknown": "空间格局未知",
}


@dataclass(frozen=True)
class ReportSection:
    """One deterministically rendered, independently publishable section."""

    section_id: str
    kind: str
    claim_ids: tuple[str, ...]
    blocks: tuple[str, ...]

    @property
    def text(self) -> str:
        """Markdown text for this section (same join rule as the full report)."""
        return "\n\n".join(self.blocks)


@dataclass(frozen=True)
class ReportSectionPlan:
    """Frozen section descriptor; its text is rendered only at publish time."""

    section_id: str
    kind: str
    claim_ids: tuple[str, ...] = ()
    fixed: str = ""


def _fmt(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日%H时")


def _claim(
    claim_id: str,
    claim_type: ClaimType,
    *,
    level: int | None,
    parameters: dict[str, Any],
    evidence_ids: tuple[str, ...],
    counter_ids: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    requirements: tuple[str, ...] = (),
) -> AllowedClaim:
    return AllowedClaim(
        claim_id=claim_id,
        claim_type=claim_type,
        level=level,  # type: ignore[arg-type]
        parameters=parameters,
        supporting_evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        counter_evidence_ids=tuple(dict.fromkeys(counter_ids)),
        limitations=limitations,
        semantic_requirements=requirements,
    )


def build_claim_allowlist(
    request: TracingRequest,
    station: StationFindings,
    spatial: SpatialFindings,
    meteo: MeteoFindings,
    process: ProcessResult,
    *,
    policy: AnalysisPolicy,
    extra_limitations: tuple[str, ...] = (),
) -> ClaimAllowlist:
    """Generate the only claims that the renderer/gate may publish."""
    claims: list[AllowedClaim] = []
    if station.coverage.expected_hours:
        claims.append(
            _claim(
                "coverage.target",
                ClaimType.COVERAGE,
                level=1,
                parameters={
                    "coverage_ratio": round(station.coverage.ratio, 4),
                    "valid_hours": station.coverage.valid_hours,
                    "expected_hours": station.coverage.expected_hours,
                    "max_gap_hours": station.coverage.max_gap_hours,
                    "coverage_sufficient": station.coverage.sufficient,
                    "baseline_valid_hours": station.baseline_coverage.valid_hours
                    if station.baseline_coverage
                    else 0,
                    "baseline_expected_hours": station.baseline_coverage.expected_hours
                    if station.baseline_coverage
                    else 0,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_series",),
            )
        )
    if station.status == "anomaly" and station.anomaly_segments:
        segment = station.anomaly_segments[0]
        claims.append(
            _claim(
                "anomaly.present",
                ClaimType.ANOMALY_PRESENT,
                level=1,
                parameters={
                    "start": segment.start.isoformat(),
                    "end": segment.end.isoformat(),
                    "duration_hours": segment.duration_hours,
                    "peak_value": segment.peak_value,
                    "peak_at": segment.peak_at.isoformat(),
                    "direction": segment.direction,
                    "segment_count": len(station.anomaly_segments),
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_anomaly",),
            )
        )
        claims.append(
            _claim(
                "anomaly.duration",
                ClaimType.DURATION,
                level=1,
                parameters={
                    "duration_hours": segment.duration_hours,
                    "start": segment.start.isoformat(),
                    "end": segment.end.isoformat(),
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_anomaly",),
            )
        )
    elif station.status == "no_anomaly":
        claims.append(
            _claim(
                "anomaly.none",
                ClaimType.NO_ANOMALY,
                level=1,
                parameters={
                    "coverage_sufficient": station.coverage.sufficient,
                    "baseline_sufficient": (
                        station.baseline_coverage is not None
                        and station.baseline_coverage.ratio
                        >= policy.baseline.min_coverage_ratio
                        and "baseline_insufficient" not in station.limitations
                    ),
                    "min_abs_delta": policy.require_supported(
                        request.pollutant
                    ).anomaly_abs_delta,
                    "min_duration_hours": policy.baseline.min_duration_hours,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=(
                    "station_series",
                    "coverage_sufficient",
                    "baseline_sufficient",
                ),
            )
        )
    elif "station_data_unavailable" in station.limitations:
        claims.append(
            _claim(
                "anomaly.unavailable",
                ClaimType.DATA_INSUFFICIENT,
                level=1,
                parameters={"unavailable_reason": "station_data_unavailable"},
                evidence_ids=(),
                limitations=station.limitations,
                requirements=("data_unavailable",),
            )
        )
    else:
        claims.append(
            _claim(
                "anomaly.insufficient",
                ClaimType.DATA_INSUFFICIENT,
                level=1,
                parameters={
                    "coverage_ratio": round(station.coverage.ratio, 4),
                    "valid_hours": station.coverage.valid_hours,
                    "expected_hours": station.coverage.expected_hours,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_series",),
            )
        )
    if (
        station.coverage.sufficient
        and station.peak_value is not None
        and station.peak_at
    ):
        claims.append(
            _claim(
                "observation.peak",
                ClaimType.PEAK,
                level=1,
                parameters={
                    "peak_value": station.peak_value,
                    "peak_at": station.peak_at.isoformat(),
                    "coverage_sufficient": True,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_series", "coverage_sufficient"),
            )
        )
    if (
        station.coverage.sufficient
        and station.mean_value is not None
        and station.min_value is not None
    ):
        claims.append(
            _claim(
                "observation.summary",
                ClaimType.OBSERVATION_SUMMARY,
                level=1,
                parameters={
                    "mean_value": station.mean_value,
                    "min_value": station.min_value,
                    "coverage_sufficient": True,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_series", "coverage_sufficient"),
            )
        )
    if station.change_rate is not None and station.coverage.sufficient:
        claims.append(
            _claim(
                "observation.change_rate",
                ClaimType.CHANGE_RATE,
                level=2,
                parameters={
                    "change_rate": round(station.change_rate, 4),
                    "coverage_sufficient": True,
                },
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_series", "coverage_sufficient"),
            )
        )
    for evaluation in station.evaluations:
        claims.append(
            _claim(
                f"evaluation.{evaluation.metric}.{evaluation.period_end.isoformat()}",
                ClaimType.EVALUATION,
                level=2 if evaluation.metric == "daily_max_8h" else 1,
                parameters={
                    "metric": evaluation.metric,
                    "period_start": evaluation.period_start.isoformat(),
                    "period_end": evaluation.period_end.isoformat(),
                    "value": round(evaluation.value, 3),
                    "unit": evaluation.unit,
                    "valid_hours": evaluation.valid_hours,
                    "required_hours": evaluation.required_hours,
                    "limitations": list(evaluation.limitations),
                },
                evidence_ids=(evaluation.evidence_id,),
                limitations=evaluation.limitations,
                requirements=("evaluation_evidence", "not_source_contribution"),
            )
        )
    if (
        meteo.background_mean_wind_speed is not None
        and meteo.weather_wind_samples >= policy.wind.min_paired_samples
    ):
        claims.append(
            _claim(
                "weather.background",
                ClaimType.WEATHER_BACKGROUND,
                level=1,
                parameters={
                    "mean_wind_speed": meteo.background_mean_wind_speed,
                    "source_types": list(meteo.source_types),
                    "wind_speed_samples": meteo.weather_wind_samples,
                },
                evidence_ids=meteo.evidence_ids,
                limitations=meteo.limitations,
                requirements=("weather_series", "not_source_distance"),
            )
        )
    if station.status == "anomaly" and spatial.pattern != "unknown":
        claims.append(
            _claim(
                "spatial.pattern",
                ClaimType.SPATIAL_PATTERN,
                level=3
                if spatial.pattern in {"multi_station_sync", "regional_contiguous"}
                else 2,
                parameters={
                    "pattern": spatial.pattern,
                    "valid_neighbor_count": spatial.valid_neighbor_count,
                    "sync_neighbor_count": sum(
                        1 for item in spatial.sync_neighbors if item.anomalous
                    ),
                    "grid_contiguous_cells": (
                        spatial.grid.contiguous_cell_count if spatial.grid else 0
                    ),
                    "grid_source_relation": (
                        spatial.grid.source_relation
                        if spatial.grid
                        else "not_applicable"
                    ),
                },
                evidence_ids=spatial.evidence_ids,
                limitations=spatial.limitations,
                requirements=("spatial_evidence", "phenomenon_only"),
            )
        )
    wind = meteo.wind
    if (
        station.status == "anomaly"
        and wind is not None
        and wind.direction_claim_allowed
        and wind.dominant_sector
    ):
        claims.append(
            _claim(
                "wind.sector",
                ClaimType.WIND_SECTOR,
                level=2,
                parameters={
                    "sector_start": wind.dominant_sector.sector_start,
                    "sector_end": wind.dominant_sector.sector_end,
                    "mean_concentration": round(
                        wind.dominant_sector.mean_concentration, 3
                    ),
                    "sample_count": wind.dominant_sector.sample_count,
                    "calm_ratio": round(wind.calm_ratio, 4),
                    "resultant_length": round(wind.resultant_length or 0.0, 4),
                    "sector_coverage": round(wind.sector_coverage, 4),
                },
                evidence_ids=meteo.evidence_ids,
                limitations=(
                    *wind.limitations,
                    "wind_direction_association_not_source",
                ),
                requirements=("wind_pairing", "not_source_distance"),
            )
        )
    lag_associations = meteo.lag_associations if station.status == "anomaly" else ()
    for association in lag_associations:
        if (
            association.best_lag_hours >= 1
            and abs(association.r) >= policy.wind.lag_min_abs_r
        ):
            claims.append(
                _claim(
                    f"lag.{association.neighbor_code}",
                    ClaimType.LAG_ASSOCIATION,
                    level=2,
                    parameters={
                        "neighbor_code": association.neighbor_code,
                        "best_lag_hours": association.best_lag_hours,
                        "r": round(association.r, 4),
                        "pairs": association.pairs,
                    },
                    evidence_ids=(association.evidence_id,),
                    limitations=(
                        "lag_correlation_not_causal",
                        *association.limitations,
                    ),
                    requirements=("lag_evidence", "not_causal"),
                )
            )
    if station.status == "anomaly":
        claims.append(
            _claim(
                "process.assessment",
                ClaimType.PROCESS_ASSESSMENT,
                level=3 if process.assessment == "transport_association" else 1,
                parameters={
                    "assessment": process.assessment,
                    "transport_components": list(process.transport_components),
                    "counter_evidence": list(process.counter_evidence),
                    "alternative_explanations": list(process.alternative_explanations),
                },
                evidence_ids=process.evidence_ids,
                limitations=process.limitations,
                requirements=(
                    "process_evidence",
                    "transport_association"
                    if process.assessment == "transport_association"
                    else "assessment_only",
                    "not_causation",
                ),
            )
        )
    if station.status != "anomaly":
        process = process.model_copy(update={"source_hypotheses": ()})
    for hypothesis in process.source_hypotheses:
        claims.append(
            _claim(
                "hypothesis.unidentified_source",
                ClaimType.SOURCE_HYPOTHESIS,
                level=None,
                parameters={
                    "status": "unverified",
                    "source_class": "unidentified",
                    "hypothesis": hypothesis,
                },
                evidence_ids=process.evidence_ids,
                limitations=process.limitations,
                requirements=(
                    "unverified_only",
                    "no_source_inventory",
                ),
            )
        )
    all_limitations = tuple(
        dict.fromkeys(
            (
                *FIXED_LIMITATIONS,
                *station.limitations,
                *spatial.limitations,
                *meteo.limitations,
                *process.limitations,
                *extra_limitations,
            )
        )
    )
    for code in all_limitations:
        claims.append(
            _claim(
                f"limitation.{code}",
                ClaimType.LIMITATION,
                level=None,
                parameters={"code": code},
                evidence_ids=(),
                requirements=("limitation_text",),
            )
        )
    claims.append(
        _claim(
            "next_steps",
            ClaimType.NEXT_STEP,
            level=None,
            parameters={
                "steps": [
                    "补充独立排放清单与化学组分资料",
                    "在授权范围内补充更多邻站与更长气象序列",
                    "如需轨迹证据，需先完成单独的轨迹能力验收",
                ]
            },
            evidence_ids=(),
            requirements=("no_new_facts",),
        )
    )
    digest_payload = [claim.model_dump(mode="json") for claim in claims]
    digest = hashlib.sha256(
        json.dumps(
            digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    return ClaimAllowlist(
        analysis_id=request.analysis_id,
        revision=request.revision,
        policy_version=policy.version,
        claims=tuple(claims),
        limitations=all_limitations,
        digest=digest,
    )


def fallback_plan(allowlist: ClaimAllowlist) -> ReportPlan:
    """Plan containing every allowed claim, including mandatory limitations."""
    return ReportPlan(
        analysis_id=allowlist.analysis_id,
        revision=allowlist.revision,
        claim_ids=tuple(claim.claim_id for claim in allowlist.claims),
        review_claim_ids=(),
    )


def _by_id(allowlist: ClaimAllowlist) -> dict[str, AllowedClaim]:
    return {claim.claim_id: claim for claim in allowlist.claims}


def _render_claim_blocks(claim: AllowedClaim, profile: Any) -> list[str]:
    """Render one allowlisted claim into canonical Markdown blocks."""
    if claim.claim_type == ClaimType.COVERAGE:
        baseline_text = (
            f"\n基线覆盖：有效 {claim.parameters['baseline_valid_hours']} / 应有 "
            f"{claim.parameters['baseline_expected_hours']} 小时。"
            if "baseline_valid_hours" in claim.parameters
            and "baseline_expected_hours" in claim.parameters
            else ""
        )
        return [
            "### 数据覆盖\n"
            f"数据完整率 {claim.parameters['coverage_ratio'] * 100:.1f}%"
            f"（有效 {claim.parameters['valid_hours']} / 应有 "
            f"{claim.parameters['expected_hours']} 小时，最大连续缺口 "
            f"{claim.parameters['max_gap_hours']} 小时）。"
            f"{baseline_text}"
        ]
    if claim.claim_type == ClaimType.ANOMALY_PRESENT:
        return [
            "### 异常过程\n"
            f"发现异常时段：{_fmt(datetime.fromisoformat(claim.parameters['start']))}"
            f"至{_fmt(datetime.fromisoformat(claim.parameters['end']))}，"
            f"持续 {claim.parameters['duration_hours']} 小时，"
            f"峰值 {claim.parameters['peak_value']:.1f} {profile.display_unit}。"
            "该结论只描述统计异常，不等于法规超标或污染来源确认。"
        ]
    if claim.claim_type == ClaimType.NO_ANOMALY:
        return [
            "### 异常过程\n"
            "未发现符合当前冻结策略的异常时段。该结论以数据覆盖与基线充分为前提，"
            "不代表其他时段或其他污染物无异常。"
        ]
    if claim.claim_type == ClaimType.DATA_INSUFFICIENT:
        return [
            "### 异常过程\n"
            "当前数据覆盖或基线不足，无法判断是否存在异常；未把未知当作无异常。"
        ]
    if claim.claim_type == ClaimType.PEAK:
        if claim.parameters.get("peak_value") is None:
            return []
        return [
            "### 峰值\n"
            f"峰值 {claim.parameters['peak_value']:.1f} {profile.display_unit}，"
            f"出现在 {_fmt(datetime.fromisoformat(claim.parameters['peak_at']))}。"
        ]
    if claim.claim_type == ClaimType.OBSERVATION_SUMMARY:
        return [
            "### 观测统计\n"
            f"分析窗口有效样本均值 {claim.parameters['mean_value']:.1f} {profile.display_unit}，"
            f"最低值 {claim.parameters['min_value']:.1f} {profile.display_unit}。"
            "该均值为有效样本统计，不作为法规日均评价。"
        ]
    if claim.claim_type == ClaimType.WEATHER_BACKGROUND:
        source = "历史模式" if "nwp" in claim.parameters["source_types"] else "气象资料"
        return [
            "### 气象背景\n"
            f"{source}有效气象小时的平均风速 "
            f"{claim.parameters['mean_wind_speed']:.1f} 米/秒，"
            f"有效风速样本 {claim.parameters['wind_speed_samples']} 小时。"
            "该背景描述不能单独确认污染来源或传输路径。"
        ]
    if claim.claim_type == ClaimType.DURATION:
        return [f"### 持续时间\n异常持续 {claim.parameters['duration_hours']} 小时。"]
    if claim.claim_type == ClaimType.CHANGE_RATE:
        return [
            "### 变化幅度\n"
            f"有效样本首末变化率 {claim.parameters['change_rate'] * 100:.1f}%。"
        ]
    if claim.claim_type == ClaimType.SPATIAL_PATTERN:
        pattern = claim.parameters["pattern"]
        return [
            "### 空间格局\n"
            f"空间格局：{_SPATIAL_TEXT.get(pattern, pattern)}"
            f"（有效邻站 {claim.parameters['valid_neighbor_count']} 个，"
            f"同步邻站 {claim.parameters['sync_neighbor_count']} 个，"
            f"连片网格 {claim.parameters['grid_contiguous_cells']} 个）。"
            "该描述只证明观测到的区域现象，不单独证明传输或来源方向。"
        ]
    if claim.claim_type == ClaimType.WIND_SECTOR:
        return [
            "### 风场关联\n"
            f"主导来向扇区 {claim.parameters['sector_start']:.0f}°–"
            f"{claim.parameters['sector_end']:.0f}°，该扇区平均浓度 "
            f"{claim.parameters['mean_concentration']:.1f} {profile.display_unit}，"
            f"样本 {claim.parameters['sample_count']} 个，"
            f"静风比例 {claim.parameters['calm_ratio'] * 100:.1f}%。"
            "相关不等于来源或距离，不能据此推导源距离。"
        ]
    if claim.claim_type == ClaimType.LAG_ASSOCIATION:
        return [
            "### 滞后关联\n"
            f"邻站 {claim.parameters['neighbor_code']} 领先 "
            f"{claim.parameters['best_lag_hours']} 小时，"
            f"r={claim.parameters['r']:.2f}，配对样本 "
            f"{claim.parameters['pairs']} 个；仅为统计关联，不是因果或概率。"
        ]
    if claim.claim_type == ClaimType.EVALUATION:
        metric_text = (
            "日最大8小时滑动均值"
            if claim.parameters["metric"] == "daily_max_8h"
            else "24小时滑动均值"
        )
        suffix = (
            "；该指标仅作补充统计，不是自然日均或法规评价"
            if claim.parameters["metric"] == "24h_sliding"
            else ""
        )
        return [
            "### 评价口径\n"
            f"{metric_text}：{claim.parameters['value']:.1f} "
            f"{profile.display_unit}，有效小时 "
            f"{claim.parameters['valid_hours']}/{claim.parameters['required_hours']}"
            f"{suffix}。"
        ]
    if claim.claim_type == ClaimType.PROCESS_ASSESSMENT:
        assessment = claim.parameters["assessment"]
        blocks = [
            "### 过程解释\n"
            f"过程判断：{_ASSESSMENT_TEXT.get(assessment, assessment)}。"
            "支持与反证、替代解释均按冻结策略保留。"
        ]
        if claim.parameters.get("transport_components"):
            blocks.append(
                "支持组件："
                + "、".join(claim.parameters["transport_components"])
                + "。"
            )
        if claim.parameters.get("counter_evidence"):
            blocks.append(
                "反证/限制：" + "、".join(claim.parameters["counter_evidence"]) + "。"
            )
        if claim.parameters.get("alternative_explanations"):
            blocks.append(
                "替代解释："
                + "、".join(claim.parameters["alternative_explanations"])
                + "。"
            )
        return blocks
    if claim.claim_type == ClaimType.SOURCE_HYPOTHESIS:
        return [
            "### 待核查假设\n"
            f"{claim.parameters['hypothesis']}（未识别源类，待核查；"
            "不是已识别企业、源类或贡献率）。"
        ]
    if claim.claim_type == ClaimType.LIMITATION:
        code = claim.parameters["code"]
        return [f"- 限制：{_LIMITATION_TEXT.get(code, code)}"]
    return []


_SECTION_KIND = {
    ClaimType.COVERAGE: "observation",
    ClaimType.ANOMALY_PRESENT: "observation",
    ClaimType.NO_ANOMALY: "observation",
    ClaimType.DATA_INSUFFICIENT: "observation",
    ClaimType.PEAK: "observation",
    ClaimType.OBSERVATION_SUMMARY: "observation",
    ClaimType.WEATHER_BACKGROUND: "spatial",
    ClaimType.DURATION: "observation",
    ClaimType.CHANGE_RATE: "observation",
    ClaimType.EVALUATION: "observation",
    ClaimType.SPATIAL_PATTERN: "spatial",
    ClaimType.WIND_SECTOR: "spatial",
    ClaimType.LAG_ASSOCIATION: "spatial",
    ClaimType.PROCESS_ASSESSMENT: "process",
    ClaimType.SOURCE_HYPOTHESIS: "process",
    ClaimType.LIMITATION: "limits",
    ClaimType.NEXT_STEP: "limits",
}


def plan_section_layout(
    request: TracingRequest,
    allowlist: ClaimAllowlist,
    plan: ReportPlan,
    *,
    policy: AnalysisPolicy,
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = (),
) -> tuple[ReportSectionPlan, ...]:
    """Freeze section order/claims without rendering any section text.

    Rendering happens per section in :func:`render_section_plan`, after the
    preceding section is already public and validated.  This is what makes the
    section barrier proof meaningful rather than a split of a prebuilt body.
    """
    claims = _by_id(allowlist)
    selected = [claims[claim_id] for claim_id in plan.claim_ids if claim_id in claims]
    sections: list[ReportSectionPlan] = [
        ReportSectionPlan(section_id="header", kind="header", fixed="header")
    ]
    index = 0
    current_kind = ""
    current_ids: list[str] = []
    for claim in selected:
        # A claim with no renderable block is still part of the plan; reject it
        # at precheck rather than silently dropping it.
        kind = _SECTION_KIND.get(claim.claim_type, "other")
        if kind != current_kind and current_ids:
            index += 1
            sections.append(
                ReportSectionPlan(
                    section_id=f"claims-{index}",
                    kind=current_kind,
                    claim_ids=tuple(current_ids),
                )
            )
            current_ids = []
        current_kind = kind
        current_ids.append(claim.claim_id)
    if current_ids:
        index += 1
        sections.append(
            ReportSectionPlan(
                section_id=f"claims-{index}",
                kind=current_kind,
                claim_ids=tuple(current_ids),
            )
        )
    if knowledge_basis:
        sections.append(
            ReportSectionPlan(
                section_id="knowledge", kind="knowledge", fixed="knowledge"
            )
        )
    sections.append(
        ReportSectionPlan(section_id="limits", kind="limits", fixed="limits")
    )
    return tuple(sections)


def render_section_plan(
    request: TracingRequest,
    allowlist: ClaimAllowlist,
    plan: ReportPlan,
    section: ReportSectionPlan,
    *,
    policy: AnalysisPolicy,
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = (),
) -> ReportSection:
    """Render exactly one frozen section, without touching any other section."""
    profile = policy.require_supported(request.pollutant)
    if section.fixed == "header":
        blocks = (
            f"## {profile.display_name} 污染过程与来向线索分析",
            f"分析对象：{request.target.station_name or request.target.station_code}"
            f"（{request.target.station_code}）  ",
            f"时间范围：{_fmt(request.time_window.start)}至{_fmt(request.time_window.end)}  ",
            f"分析策略：{allowlist.policy_version}",
        )
    elif section.fixed == "knowledge":
        blocks = tuple(render_knowledge_lines(knowledge_basis))
    elif section.fixed == "limits":
        blocks = (
            "### 限制与口径",
            "无法计算贡献率：当前缺少排放清单、化学组分和轨迹证据，"
            "不能确认具体企业、源类、贡献率或来源概率。",
            "以上统计百分比仅描述数据覆盖或观测特征，不是源贡献率。",
            "本报告不包含轨迹/PSCF 分析，轨迹能力当前不可用。",
        )
    else:
        claims = _by_id(allowlist)
        rendered: list[str] = []
        for claim_id in section.claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            rendered.extend(_render_claim_blocks(claim, profile))
        blocks = tuple(rendered)
    return ReportSection(
        section_id=section.section_id,
        kind=section.kind,
        claim_ids=section.claim_ids,
        blocks=blocks,
    )


def plan_report_sections(
    request: TracingRequest,
    allowlist: ClaimAllowlist,
    plan: ReportPlan,
    *,
    policy: AnalysisPolicy,
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = (),
) -> tuple[ReportSection, ...]:
    """Render every section (compatibility helper for full-report callers)."""
    return tuple(
        render_section_plan(
            request,
            allowlist,
            plan,
            section,
            policy=policy,
            knowledge_basis=knowledge_basis,
        )
        for section in plan_section_layout(
            request,
            allowlist,
            plan,
            policy=policy,
            knowledge_basis=knowledge_basis,
        )
    )


def report_plan_digest(
    request: TracingRequest,
    allowlist: ClaimAllowlist,
    plan: ReportPlan,
    sections: tuple[ReportSectionPlan, ...],
    *,
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = (),
) -> str:
    """Freeze the publication plan identity before the first section is sent."""
    payload = {
        "analysis_id": request.analysis_id,
        "revision": request.revision,
        "request_digest": request.request_digest or request.canonical_digest(),
        "allowlist_digest": allowlist.digest,
        "policy_version": allowlist.policy_version,
        "claim_ids": list(plan.claim_ids),
        "knowledge": [item.citation_id for item in knowledge_basis],
        "sections": [
            {
                "id": section.section_id,
                "kind": section.kind,
                "claim_ids": list(section.claim_ids),
                "fixed": section.fixed,
            }
            for section in sections
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def render_report(
    request: TracingRequest,
    allowlist: ClaimAllowlist,
    plan: ReportPlan,
    *,
    scope: RuntimeScope,
    report_id: str,
    policy: AnalysisPolicy,
    created_at: datetime | None = None,
    run_id: str = "",
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = (),
    sections: tuple[ReportSection, ...] | None = None,
) -> ValidatedReport:
    """Render a controlled Markdown report from allowlisted claims only."""
    resolved_sections = sections or plan_report_sections(
        request,
        allowlist,
        plan,
        policy=policy,
        knowledge_basis=knowledge_basis,
    )
    body = "\n\n".join(
        block
        for section in resolved_sections
        for block in section.blocks
        if block != ""
    )
    profile = policy.require_supported(request.pollutant)
    claims = _by_id(allowlist)
    selected = [claims[claim_id] for claim_id in plan.claim_ids if claim_id in claims]
    title = (
        f"{profile.display_name} 污染过程与来向线索分析"
        f"（{request.target.station_name or request.target.station_code}）"
    )
    report = ValidatedReport(
        report_id=report_id,
        analysis_id=request.analysis_id,
        revision=request.revision,
        request_digest=request.request_digest or request.canonical_digest(),
        policy_version=allowlist.policy_version,
        run_id=run_id,
        claim_ids=tuple(claim.claim_id for claim in selected),
        limitations=allowlist.limitations,
        body_markdown=body,
        canvas={
            "title": title,
            "content_type": "markdown",
            "content": body,
            "open_in": "canvas_window",
        },
        evidence_ids=tuple(
            dict.fromkeys(
                evidence_id
                for claim in selected
                for evidence_id in (
                    *claim.supporting_evidence_ids,
                    *claim.counter_evidence_ids,
                )
            )
        ),
        scope=scope,
        content_sha256="",
        created_at=created_at or datetime.now(UTC),
        knowledge_basis=knowledge_basis,
    )
    return report.model_copy(update={"content_sha256": report.compute_digest()})


def render_knowledge_lines(
    knowledge_basis: tuple[KnowledgeBasisItem, ...],
) -> list[str]:
    """One canonical source block; a heading alone grants no scan exemption."""
    if not knowledge_basis:
        return []
    lines = [
        "### 知识依据",
        "以下为独立的知识库引用，只说明标准/资料原文，不作为本次污染监测证据、"
        "claim、贡献率或来源归因依据。",
    ]
    for item in knowledge_basis:
        location = (
            f"（位置：{json.dumps(item.location, ensure_ascii=False)}）"
            if item.location
            else "（位置未提供）"
        )
        lines.append(
            f"- 《{item.doc_name}》{location}：{item.excerpt} [[cite:{item.citation_id}]]"
        )
    return lines
