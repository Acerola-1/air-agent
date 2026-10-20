"""Request resolution, disambiguation and freezing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.runtime import BusinessBinding
from intelligent_tracing.schemas import (
    StationMeta,
    Target,
    TracingRequest,
)


class RequestValidationError(ValueError):
    """A candidate request is incomplete, ambiguous or not authorized."""


@dataclass(frozen=True)
class StationCandidate:
    """One station candidate returned for disambiguation."""

    station_code: str
    name: str
    region_code: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class StationResolution:
    """Resolved target or an explicit ambiguity."""

    target: Target | None
    candidates: tuple[StationCandidate, ...] = ()
    ambiguous: bool = False


def _strip_region_prefix(name: str, region_name: str) -> str:
    """Allow a user to say 郑州市X when the catalogue stores only X."""
    text = name.strip()
    prefix = region_name.strip()
    if prefix and text.startswith(prefix) and len(text) > len(prefix):
        return text[len(prefix) :].strip()
    return text


def resolve_station(
    *,
    station_code: str = "",
    station_name: str = "",
    region_code: str = "",
    region_name: str = "",
    catalog: tuple[StationMeta, ...],
) -> StationResolution:
    """Resolve a station by code first, then by exact name within a region."""
    if station_code:
        matches = [s for s in catalog if s.station_code == station_code]
        if not matches:
            raise RequestValidationError("站点代码不在当前授权目录中")
        station = matches[0]
        if region_code and station.region_code and station.region_code != region_code:
            raise RequestValidationError("站点不属于请求中的区域")
        resolved_region = station.region_code or region_code
        return StationResolution(
            target=Target(
                station_code=station.station_code,
                station_name=station.name,
                region_code=resolved_region,
                region_name=region_name or station.region_name,
                latitude=station.latitude,
                longitude=station.longitude,
                coordinate_source="platform_catalog",
                admin_scope=(resolved_region,) if resolved_region else (),
            )
        )
    if not station_name:
        raise RequestValidationError("缺少站点代码或名称")
    matches = [
        s
        for s in catalog
        if s.name == station_name and (not region_code or s.region_code == region_code)
    ]
    if not matches:
        normalized = _strip_region_prefix(station_name, region_name)
        if normalized != station_name:
            matches = [
                s
                for s in catalog
                if s.name == normalized
                and (not region_code or s.region_code == region_code)
            ]
    if len(matches) == 1:
        station = matches[0]
        resolved_region = station.region_code or region_code
        return StationResolution(
            target=Target(
                station_code=station.station_code,
                station_name=station.name,
                region_code=resolved_region,
                region_name=region_name or station.region_name,
                latitude=station.latitude,
                longitude=station.longitude,
                coordinate_source="platform_catalog",
                admin_scope=(resolved_region,) if resolved_region else (),
            )
        )
    if len(matches) > 1:
        return StationResolution(
            target=None,
            candidates=tuple(
                StationCandidate(
                    station_code=s.station_code,
                    name=s.name,
                    region_code=region_code,
                    latitude=s.latitude,
                    longitude=s.longitude,
                )
                for s in matches
            ),
            ambiguous=True,
        )
    raise RequestValidationError("当前授权目录中未找到该站点")


def _capability_ref(config: dict[str, Any] | None) -> tuple[str, ...]:
    configurable = (config or {}).get("configurable") or {}
    metadata = (config or {}).get("metadata") or {}
    values = {
        str(configurable.get("capability_selection_id") or ""),
        str(configurable.get("capability_selection_digest") or ""),
        str(metadata.get("capability_selection_id") or ""),
        str(metadata.get("capability_selection_digest") or ""),
    }
    return tuple(value for value in values if value)


def validate_and_freeze_request(
    candidate: TracingRequest,
    *,
    policy: AnalysisPolicy,
    binding: BusinessBinding | None,
    config: dict[str, Any] | None = None,
) -> TracingRequest:
    """Validate policy, time window, business permit and selection reference.

    业务许可只来自可信 config（`binding`）。`capability_snapshot_ref` 仅用于
    回读已受理的技能/工具选择：提供时必须与已受理快照一致，但缺失它不会
    替代也不会伪造业务授权。
    """
    profile = policy.require_supported(candidate.pollutant)
    if candidate.analysis_policy_version != policy.version:
        raise RequestValidationError("分析策略版本与当前冻结策略不一致")
    if binding is None:
        raise RequestValidationError("缺少可信业务快照，拒绝冻结请求")
    if (
        candidate.business_code != binding.business_code
        or candidate.business_policy_version != binding.business_policy_version
        or candidate.business_permission != binding.business_permission
        or candidate.graph_id != binding.graph_id
    ):
        raise RequestValidationError("请求业务字段与可信业务快照不一致")
    if candidate.time_window.timezone not in ("Asia/Shanghai",):
        raise RequestValidationError("当前仅支持 Asia/Shanghai 时区")
    if candidate.baseline_window is not None:
        if candidate.baseline_window.end > candidate.time_window.start:
            raise RequestValidationError("基线窗口必须早于目标时间窗")
        if candidate.baseline_window.timezone != candidate.time_window.timezone:
            raise RequestValidationError("基线窗口与目标时间窗时区必须一致")
    if candidate.target.latitude == 0 and candidate.target.longitude == 0:
        raise RequestValidationError("站点坐标无效")
    if profile.key != candidate.pollutant.lower():
        raise RequestValidationError("污染物画像不匹配")
    if candidate.capability_snapshot_ref:
        allowed_refs = _capability_ref(config)
        if allowed_refs and candidate.capability_snapshot_ref not in allowed_refs:
            raise RequestValidationError("能力选择引用与已受理快照不一致")
    return candidate.frozen()
