"""Deterministic claim allowlist and controlled report rendering."""

from __future__ import annotations

import hashlib
import json
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
                "anomaly.peak",
                ClaimType.PEAK,
                level=1,
                parameters={
                    "peak_value": station.peak_value,
                    "peak_at": station.peak_at.isoformat() if station.peak_at else None,
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
    if station.change_rate is not None and station.status == "anomaly":
        claims.append(
            _claim(
                "anomaly.change_rate",
                ClaimType.CHANGE_RATE,
                level=2,
                parameters={"change_rate": round(station.change_rate, 4)},
                evidence_ids=station.evidence_ids,
                limitations=station.limitations,
                requirements=("station_anomaly",),
            )
        )
    for evaluation in station.evaluations:
        claims.append(
            _claim(
                f"evaluation.{evaluation.metric}",
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
) -> ValidatedReport:
    """Render a controlled Markdown report from allowlisted claims only."""
    claims = _by_id(allowlist)
    selected = [claims[claim_id] for claim_id in plan.claim_ids if claim_id in claims]
    # The renderer reads the pollutant profile from the frozen request; it
    # never reuses PM2.5 labels for another pollutant.
    profile = policy.require_supported(request.pollutant)
    lines: list[str] = [
        f"## {profile.display_name} 污染过程与来向线索分析",
        "",
        f"分析对象：{request.target.station_name or request.target.station_code}（{request.target.station_code}）  ",
        f"时间范围：{_fmt(request.time_window.start)}至{_fmt(request.time_window.end)}  ",
        f"分析策略：{allowlist.policy_version}",
        "",
    ]
    for claim in selected:
        if claim.claim_type == ClaimType.COVERAGE:
            lines.append(
                "### 数据覆盖\n"
                f"数据完整率 {claim.parameters['coverage_ratio'] * 100:.1f}%"
                f"（有效 {claim.parameters['valid_hours']} / 应有 "
                f"{claim.parameters['expected_hours']} 小时，最大连续缺口 "
                f"{claim.parameters['max_gap_hours']} 小时）。"
            )
        elif claim.claim_type == ClaimType.ANOMALY_PRESENT:
            lines.append(
                "### 异常过程\n"
                f"发现异常时段：{_fmt(datetime.fromisoformat(claim.parameters['start']))}"
                f"至{_fmt(datetime.fromisoformat(claim.parameters['end']))}，"
                f"持续 {claim.parameters['duration_hours']} 小时，"
                f"峰值 {claim.parameters['peak_value']:.1f} {profile.display_unit}。"
                "该结论只描述统计异常，不等于法规超标或污染来源确认。"
            )
        elif claim.claim_type == ClaimType.NO_ANOMALY:
            lines.append(
                "### 异常过程\n"
                "未发现符合当前冻结策略的异常时段。该结论以数据覆盖与基线充分为前提，"
                "不代表其他时段或其他污染物无异常。"
            )
        elif claim.claim_type == ClaimType.DATA_INSUFFICIENT:
            lines.append(
                "### 异常过程\n"
                "当前数据覆盖或基线不足，无法判断是否存在异常；未把未知当作无异常。"
            )
        elif claim.claim_type == ClaimType.PEAK:
            if claim.parameters.get("peak_value") is not None:
                lines.append(
                    "### 峰值\n"
                    f"峰值 {claim.parameters['peak_value']:.1f} {profile.display_unit}，"
                    f"出现在 {_fmt(datetime.fromisoformat(claim.parameters['peak_at']))}。"
                )
        elif claim.claim_type == ClaimType.DURATION:
            lines.append(
                f"### 持续时间\n异常持续 {claim.parameters['duration_hours']} 小时。"
            )
        elif claim.claim_type == ClaimType.CHANGE_RATE:
            lines.append(
                "### 变化幅度\n"
                f"时段首末变化率 {claim.parameters['change_rate'] * 100:.1f}%。"
            )
        elif claim.claim_type == ClaimType.SPATIAL_PATTERN:
            pattern = claim.parameters["pattern"]
            lines.append(
                "### 空间格局\n"
                f"空间格局：{_SPATIAL_TEXT.get(pattern, pattern)}"
                f"（有效邻站 {claim.parameters['valid_neighbor_count']} 个，"
                f"同步邻站 {claim.parameters['sync_neighbor_count']} 个，"
                f"连片网格 {claim.parameters['grid_contiguous_cells']} 个）。"
                "该描述只证明观测到的区域现象，不单独证明传输或来源方向。"
            )
        elif claim.claim_type == ClaimType.WIND_SECTOR:
            lines.append(
                "### 风场关联\n"
                f"主导来向扇区 {claim.parameters['sector_start']:.0f}°–"
                f"{claim.parameters['sector_end']:.0f}°，该扇区平均浓度 "
                f"{claim.parameters['mean_concentration']:.1f} {profile.display_unit}，"
                f"样本 {claim.parameters['sample_count']} 个，"
                f"静风比例 {claim.parameters['calm_ratio'] * 100:.1f}%。"
                "相关不等于来源或距离，不能据此推导源距离。"
            )
        elif claim.claim_type == ClaimType.LAG_ASSOCIATION:
            lines.append(
                "### 滞后关联\n"
                f"邻站 {claim.parameters['neighbor_code']} 领先 "
                f"{claim.parameters['best_lag_hours']} 小时，"
                f"r={claim.parameters['r']:.2f}，配对样本 "
                f"{claim.parameters['pairs']} 个；仅为统计关联，不是因果或概率。"
            )
        elif claim.claim_type == ClaimType.EVALUATION:
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
            lines.append(
                "### 评价口径\n"
                f"{metric_text}：{claim.parameters['value']:.1f} "
                f"{profile.display_unit}，有效小时 "
                f"{claim.parameters['valid_hours']}/{claim.parameters['required_hours']}"
                f"{suffix}。"
            )
        elif claim.claim_type == ClaimType.PROCESS_ASSESSMENT:
            assessment = claim.parameters["assessment"]
            lines.append(
                "### 过程解释\n"
                f"过程判断：{_ASSESSMENT_TEXT.get(assessment, assessment)}。"
                "支持与反证、替代解释均按冻结策略保留。"
            )
            if claim.parameters.get("transport_components"):
                lines.append(
                    "支持组件："
                    + "、".join(claim.parameters["transport_components"])
                    + "。"
                )
            if claim.parameters.get("counter_evidence"):
                lines.append(
                    "反证/限制："
                    + "、".join(claim.parameters["counter_evidence"])
                    + "。"
                )
            if claim.parameters.get("alternative_explanations"):
                lines.append(
                    "替代解释："
                    + "、".join(claim.parameters["alternative_explanations"])
                    + "。"
                )
        elif claim.claim_type == ClaimType.SOURCE_HYPOTHESIS:
            lines.append(
                "### 待核查假设\n"
                f"{claim.parameters['hypothesis']}（未识别源类，待核查；"
                "不是已识别企业、源类或贡献率）。"
            )
        elif claim.claim_type == ClaimType.LIMITATION:
            code = claim.parameters["code"]
            text = _LIMITATION_TEXT.get(code, code)
            lines.append(f"- 限制：{text}")
    if knowledge_basis:
        lines.extend(render_knowledge_lines(knowledge_basis))
    lines.extend(
        [
            "",
            "### 限制与口径",
            "无法计算贡献率：当前缺少排放清单、化学组分和轨迹证据，"
            "不能确认具体企业、源类、贡献率或来源概率。",
            "以上统计百分比仅描述数据覆盖或观测特征，不是源贡献率。",
            "本报告不包含轨迹/PSCF 分析，轨迹能力当前不可用。",
        ]
    )
    body = "\n\n".join(
        line if line.startswith("- ") or line.startswith("###") else line
        for line in lines
        if line != ""
    )
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
