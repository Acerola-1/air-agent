"""确定性权限规则引擎.

LLM 只负责抽取 PermissionExtractedSlots，代码规则引擎负责判定权限。
核心原则：
1. LLM 只允许输出 PermissionExtractedSlots，不得输出 PermissionResult。
2. MCP 工具只负责提供可信事实，不负责最终权限判定。
3. 代码规则引擎是 PermissionResult 的唯一生产者。
4. 所有权限规则必须可单测、可解释、可复现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common.permission.correction_templates import (
    build_region_replacement_correction_text,
    build_time_truncation_correction_text,
)
from common.permission.facts import PermissionFacts, StationPermissionCandidate
from common.permission.region import (
    ALLOWED_LEVELS,
    build_permission_region_context,
    calculate_exemption_window,
    calculate_time_intersection,
    default_allowed_region,
    is_scope_excluded,
    truncate_time_span,
)
from common.permission.result import AllowedRegion, PermissionResult, StationOverride

RULE_VERSION = "V2.3"
PERMISSION_FAIL_CLOSED = True


def _parse_beijing_time(value: str) -> datetime:
    """解析北京时间字符串."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析北京时间: {value}")


def _extract_user_level(user_profile: dict[str, Any]) -> str | None:
    """从用户画像提取用户层级."""
    bound = user_profile.get("bound_region")
    if isinstance(bound, dict):
        level = bound.get("level")
        if level in {"province", "city", "district"}:
            return str(level)
    # 兼容旧格式
    level = user_profile.get("region_level") or user_profile.get("level")
    if level in {"province", "city", "district"}:
        return str(level)
    return None


def _extract_requested_region(
    requested_region: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """提取并校验请求行政区."""
    if not requested_region or not isinstance(requested_region, dict):
        return None
    if requested_region.get("ambiguous") is True:
        return None
    # 兼容 MCP 返回的字符串 "true"/"false"
    found = requested_region.get("found")
    if found is not True:
        # 尝试字符串兼容
        if isinstance(found, str) and found.lower() in ("true", "1"):
            pass  # 字符串 true 视为 found
        else:
            return None
    return requested_region


def _allow(
    *,
    exemption: str = "",
    exemption_window: list[str] | None = None,
    reason: str = "",
    original_time_span: list[str] | None = None,
    time_granularity: str = "",
    matched_rules: list[str] | None = None,
    debug_context: dict[str, Any] | None = None,
    station_overrides: list[StationOverride] | None = None,
    accessible_station_count: int | None = None,
    total_station_count: int | None = None,
    unavailable_station_count: int | None = None,
) -> PermissionResult:
    """构造放行结果."""
    return PermissionResult(
        permitted=True,
        exemption=exemption,
        exemption_window=exemption_window,
        fix_strategy="",
        legal_time_span=None,
        allowed_region=None,
        reason=reason,
        correction_text="",
        original_time_span=original_time_span,
        time_granularity=time_granularity,
        rule_version=RULE_VERSION,
        matched_rules=matched_rules or [],
        debug_context=debug_context,
        auto_filled=False,
        station_overrides=station_overrides or [],
        accessible_station_count=accessible_station_count,
        total_station_count=total_station_count,
        unavailable_station_count=unavailable_station_count,
    )


def _reject(
    *,
    fix_strategy: str = "reject",
    reason: str = "",
    correction_text: str = "",
    original_time_span: list[str] | None = None,
    time_granularity: str = "",
    matched_rules: list[str] | None = None,
    debug_context: dict[str, Any] | None = None,
    station_overrides: list[StationOverride] | None = None,
    accessible_station_count: int | None = None,
    total_station_count: int | None = None,
    unavailable_station_count: int | None = None,
) -> PermissionResult:
    """构造拒绝结果."""
    return PermissionResult(
        permitted=False,
        exemption="",
        exemption_window=None,
        fix_strategy=fix_strategy,
        legal_time_span=None,
        allowed_region=None,
        reason=reason,
        correction_text=correction_text,
        original_time_span=original_time_span,
        time_granularity=time_granularity,
        rule_version=RULE_VERSION,
        matched_rules=matched_rules or [],
        debug_context=debug_context,
        auto_filled=False,
        station_overrides=station_overrides or [],
        accessible_station_count=accessible_station_count,
        total_station_count=total_station_count,
        unavailable_station_count=unavailable_station_count,
    )


def _build_time_truncation_result(
    original_span: list[str],
    legal_span: list[str],
    time_granularity: str,
    requested_region: dict[str, Any] | None = None,
    metric_names: list[str] | None = None,
    station_overrides: list[StationOverride] | None = None,
    accessible_station_count: int | None = None,
    total_station_count: int | None = None,
    unavailable_station_count: int | None = None,
) -> PermissionResult:
    """构造时间截断修正结果."""
    correction_text = build_time_truncation_correction_text(
        legal_span=legal_span,
        requested_region=requested_region,
        metric_names=metric_names,
    )
    return PermissionResult(
        permitted=False,
        exemption="",
        exemption_window=None,
        fix_strategy="truncate_time",
        legal_time_span=legal_span,
        allowed_region=None,
        reason="原始时间范围超出豁免窗口，已截断到合法范围",
        correction_text=correction_text,
        original_time_span=original_span,
        time_granularity=time_granularity,
        rule_version=RULE_VERSION,
        auto_filled=False,
        station_overrides=station_overrides or [],
        accessible_station_count=accessible_station_count,
        total_station_count=total_station_count,
        unavailable_station_count=unavailable_station_count,
    )


def _build_region_replacement_result(
    requested: dict[str, Any],
    replacement: dict[str, Any],
    time_granularity: str = "",
    original_time_span: list[str] | None = None,
    auto_filled: bool = False,
    region_context: dict[str, Any] | None = None,
) -> PermissionResult:
    """构造行政区替换修正结果."""
    template = build_region_replacement_correction_text(
        region_context=region_context,
        replacement=replacement,
        time_granularity=time_granularity,
    )
    allowed = AllowedRegion(
        name=str(replacement.get("name", "")),
        level=str(replacement.get("level", "")),
        data_type=str(replacement.get("data_type", "summary")),
    )
    return PermissionResult(
        permitted=False,
        exemption="",
        exemption_window=None,
        fix_strategy="replace_region",
        legal_time_span=None,
        allowed_region=allowed,
        reason="请求行政区超出权限范围，已替换为有权限的行政区",
        correction_text=template.text,
        original_time_span=original_time_span,
        time_granularity=time_granularity,
        rule_version=RULE_VERSION,
        debug_context={
            "correction_template": template.template_id,
            "correction_fallback_reason": template.fallback_reason,
        },
        auto_filled=auto_filled,
    )


def _build_region_auto_fill_result(
    replacement: dict[str, Any],
    time_granularity: str = "",
    original_time_span: list[str] | None = None,
) -> PermissionResult:
    """构造默认行政区自动补全结果."""
    allowed = AllowedRegion(
        name=str(replacement.get("name", "")),
        level=str(replacement.get("level", "")),
        data_type=str(replacement.get("data_type", "summary")),
    )
    return PermissionResult(
        permitted=True,
        exemption="normal",
        exemption_window=None,
        fix_strategy="",
        legal_time_span=None,
        allowed_region=allowed,
        reason="用户未指定行政区，已使用用户关联区域作为默认查询区域",
        correction_text=(
            f"根据您的关联区域，已自动补全查询区域为{allowed.name}。"
            if allowed.name
            else "根据您的关联区域，已自动补全查询区域。"
        ),
        original_time_span=original_time_span,
        time_granularity=time_granularity,
        rule_version=RULE_VERSION,
        matched_rules=["region_auto_filled_from_bound_region"],
        debug_context={"auto_filled": True, "default_region": replacement},
        auto_filled=True,
    )


def _station_region_to_requested_region(
    station: StationPermissionCandidate,
) -> dict[str, Any] | None:
    """将站点归属行政区转换为现有 requested_region 兼容结构."""
    region = station.region
    if not isinstance(region, dict):
        return None
    name = str(region.get("name") or "").strip()
    level = str(region.get("level") or "").strip()
    city = str(region.get("city") or "").strip()
    province = str(region.get("province") or "").strip()
    if not name or level not in {"province", "city", "district"}:
        return None
    if not city or not province:
        return None
    return {
        "found": True,
        "ambiguous": False,
        "region": {"name": name, "level": level},
        "city": {"name": city},
        "province": {"name": province},
    }


def _station_override(
    station: StationPermissionCandidate,
) -> StationOverride:
    """构造后续查询可执行站点覆盖项."""
    return StationOverride(
        station_name=station.station_name,
        station_type=station.station_type,
        region=station.region,
        source_region=station.source_region,
    )


def _build_station_crop_text(count: int) -> str:
    return (
        f"根据权限，仅展示您可访问的 {count} 个站点数据。"
        "如需查看其他区域站点，可查询最近7天数据。"
    )


def _requested_region_summary(
    requested_region: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """提取时间截断文案使用的请求区域摘要."""
    if not requested_region:
        return None
    region = requested_region.get("region")
    source = region if isinstance(region, dict) else requested_region
    if not isinstance(source, dict):
        return None
    name = source.get("name") or source.get("region_name")
    level = source.get("level")
    if not name:
        return None
    return {"name": str(name), "level": str(level or "")}


def _metric_names_from_facts(facts: PermissionFacts) -> list[str] | None:
    """从原始 slots 提取时间截断文案可选指标名称."""
    raw_slots = facts.raw_slots
    if not isinstance(raw_slots, dict):
        return None
    metric_names = raw_slots.get("metric_names")
    if not isinstance(metric_names, list):
        return None
    normalized = [str(item).strip() for item in metric_names if str(item).strip()]
    return normalized or None


def _check_station_candidates(
    facts: PermissionFacts,
) -> tuple[
    list[StationPermissionCandidate], list[dict[str, Any]], list[dict[str, Any]]
]:
    """按站点归属行政区判断候选站点可访问性."""
    allowed: list[StationPermissionCandidate] = []
    rejected: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for station in facts.station_candidates:
        requested_region = _station_region_to_requested_region(station)
        if requested_region is None:
            invalid.append(
                {
                    "station_name": station.station_name,
                    "station_type": station.station_type,
                    "reason": "station_region_invalid",
                }
            )
            continue
        region_context = build_permission_region_context(
            facts.user_profile,
            requested_region,
        )
        if region_context["region_allowed_without_time_exemption"]:
            allowed.append(station)
            continue
        rejected.append(
            {
                "station_name": station.station_name,
                "station_type": station.station_type,
                "region": station.region,
                "reason": "station_region_not_allowed",
                "replacement_region": region_context.get("replacement_region"),
            }
        )

    for station in facts.unavailable_stations:
        invalid.append(
            {
                "station_name": station.station_name,
                "station_type": station.station_type,
                "reason": "station_region_missing",
            }
        )

    return allowed, rejected, invalid


def _check_station_permission(
    facts: PermissionFacts,
    *,
    time_intersection: str,
    exemption_window: list[str] | None,
    scope_excluded: bool,
) -> PermissionResult:
    """执行站点权限分支."""
    total_count = len(facts.station_candidates) + len(facts.unavailable_stations)
    unavailable_count = len(facts.unavailable_stations)
    all_candidate_overrides = [
        _station_override(station) for station in facts.station_candidates
    ]

    if total_count == 0:
        return _reject(
            fix_strategy="reject",
            reason="站点归属解析未返回可用站点",
            correction_text="该数据暂不可用。",
            original_time_span=facts.original_time_span,
            time_granularity=facts.time_granularity,
            matched_rules=["station_scope_empty"],
            total_station_count=0,
            accessible_station_count=0,
            unavailable_station_count=0,
        )

    if time_intersection == "full_window":
        return _allow(
            exemption="full_window",
            exemption_window=exemption_window,
            reason="查询时间完全在豁免窗口内，站点不受行政区限制",
            original_time_span=facts.original_time_span,
            time_granularity=facts.time_granularity,
            matched_rules=["time_exemption_full_window", "station_time_exempted"],
            debug_context={
                "exemption_window": exemption_window,
                "scope_excluded": scope_excluded,
                "unavailable_stations": [
                    station.station_name for station in facts.unavailable_stations
                ],
            },
            station_overrides=all_candidate_overrides,
            accessible_station_count=len(all_candidate_overrides),
            total_station_count=total_count,
            unavailable_station_count=unavailable_count,
        )

    if time_intersection == "partial":
        if exemption_window and len(exemption_window) == 2 and facts.original_time_span:
            legal_span = truncate_time_span(facts.original_time_span, exemption_window)
            return _build_time_truncation_result(
                facts.original_time_span,
                legal_span,
                facts.time_granularity,
                station_overrides=all_candidate_overrides,
                accessible_station_count=len(all_candidate_overrides),
                total_station_count=total_count,
                unavailable_station_count=unavailable_count,
            )

    allowed_stations, rejected_stations, invalid_stations = _check_station_candidates(
        facts
    )
    allowed_overrides = [_station_override(station) for station in allowed_stations]
    debug_context = {
        "station_region_mode": facts.station_region_mode,
        "rejected_stations": rejected_stations,
        "invalid_stations": invalid_stations,
    }

    if not allowed_overrides:
        single_unavailable = total_count == 1 and bool(invalid_stations)
        correction = (
            "该数据暂不可用。"
            if single_unavailable
            else "因数据权限限制，您无权访问该站点数据。"
        )
        return _reject(
            fix_strategy="reject",
            reason="全部候选站点不可访问",
            correction_text=correction,
            original_time_span=facts.original_time_span,
            time_granularity=facts.time_granularity,
            matched_rules=["station_all_rejected"],
            debug_context=debug_context,
            accessible_station_count=0,
            total_station_count=total_count,
            unavailable_station_count=len(invalid_stations),
        )

    if len(allowed_overrides) == total_count and not invalid_stations:
        return _allow(
            exemption="normal",
            reason="站点归属行政区权限通过",
            original_time_span=facts.original_time_span,
            time_granularity=facts.time_granularity,
            matched_rules=["station_region_allowed"],
            debug_context=debug_context,
            station_overrides=allowed_overrides,
            accessible_station_count=len(allowed_overrides),
            total_station_count=total_count,
            unavailable_station_count=0,
        )

    return PermissionResult(
        permitted=False,
        exemption="",
        fix_strategy="filter_stations",
        legal_time_span=None,
        allowed_region=None,
        reason="部分站点超出权限范围，已裁剪为可访问站点集合",
        correction_text=_build_station_crop_text(len(allowed_overrides)),
        original_time_span=facts.original_time_span,
        time_granularity=facts.time_granularity,
        rule_version=RULE_VERSION,
        matched_rules=["station_partial_allowed"],
        debug_context=debug_context,
        auto_filled=False,
        station_overrides=allowed_overrides,
        accessible_station_count=len(allowed_overrides),
        total_station_count=total_count,
        unavailable_station_count=len(invalid_stations),
    )


def check_permission(facts: PermissionFacts) -> PermissionResult:
    """执行确定性权限审查.

    执行顺序（固定，不可调整）：
    1. 校验用户画像可用性
    2. 校验行政区解析结果
    3. 判断是否命中适用范围排除项
    4. 计算豁免窗口
    5. 判断时间交集
    6. full_window -> 放行
    7. partial -> 截断时间
    8. none -> 基础行政区权限
    9. 行政区权限通过 -> 放行
    10. 行政区权限不通过且有 replacement -> 替换
    11. 无可用替代 -> 硬拒绝
    """
    user_profile = facts.user_profile
    requested_region_raw = facts.requested_region
    time_granularity = facts.time_granularity
    original_time_span = facts.original_time_span

    # 1. 校验用户画像可用性
    user_level = _extract_user_level(user_profile)
    if user_level is None:
        return _reject(
            fix_strategy="reject",
            reason="无法获取用户权限画像，拒绝访问",
            correction_text="因数据权限限制，无法获取您的权限信息，拒绝访问。",
            time_granularity=time_granularity,
            original_time_span=original_time_span,
            matched_rules=["user_profile_missing"],
        )

    # 2. 校验行政区解析结果
    requested_region = _extract_requested_region(requested_region_raw)
    if requested_region is None and requested_region_raw is not None:
        return _reject(
            fix_strategy="reject",
            reason="无法唯一解析请求行政区，拒绝访问",
            correction_text="无法识别您查询的行政区，请补充明确的省、市或区县名称。",
            time_granularity=time_granularity,
            original_time_span=original_time_span,
            matched_rules=["requested_region_unresolved"],
            debug_context={
                "requested_region_found": requested_region_raw.get("found"),
                "requested_region_ambiguous": requested_region_raw.get("ambiguous"),
            },
        )

    # 3. 判断是否命中适用范围排除项
    scope_excluded = False
    if original_time_span and len(original_time_span) == 2:
        try:
            beijing_time = _parse_beijing_time(facts.beijing_time)
            scope_excluded = is_scope_excluded(
                original_time_span, time_granularity, beijing_time
            )
        except (ValueError, TypeError):
            scope_excluded = False

    # 4. 计算豁免窗口
    try:
        beijing_time = _parse_beijing_time(facts.beijing_time)
        exemption_window = calculate_exemption_window(time_granularity, beijing_time)
    except (ValueError, TypeError):
        exemption_window = None

    # 5. 判断时间交集（仅在未命中排除项且有时间范围时）
    time_intersection = "none"
    if not scope_excluded and original_time_span and len(original_time_span) == 2:
        if exemption_window and len(exemption_window) == 2:
            time_intersection = calculate_time_intersection(
                original_time_span, exemption_window, time_granularity
            )

    # 站点查询继承站点所属行政区权限；时间豁免仍优先。
    if facts.data_scope == "station":
        return _check_station_permission(
            facts,
            time_intersection=time_intersection,
            exemption_window=exemption_window,
            scope_excluded=scope_excluded,
        )

    # 6. full_window -> 直接放行
    if time_intersection == "full_window":
        return _allow(
            exemption="full_window",
            exemption_window=exemption_window,
            reason="查询时间完全在豁免窗口内，享受时间豁免",
            original_time_span=original_time_span,
            time_granularity=time_granularity,
            matched_rules=["time_exemption_full_window"],
            debug_context={
                "exemption_window": exemption_window,
                "scope_excluded": scope_excluded,
            },
        )

    # 7. partial -> 优先截断时间
    if time_intersection == "partial":
        if exemption_window and len(exemption_window) == 2:
            legal_span = truncate_time_span(original_time_span, exemption_window)
            return _build_time_truncation_result(
                original_time_span,
                legal_span,
                time_granularity,
                requested_region=_requested_region_summary(requested_region),
                metric_names=_metric_names_from_facts(facts),
            )

    # 8. none -> 执行基础行政区权限判断
    if requested_region is None:
        replacement = default_allowed_region(user_profile)
        return _build_region_auto_fill_result(
            replacement,
            time_granularity=time_granularity,
            original_time_span=original_time_span,
        )

    region_context = build_permission_region_context(user_profile, requested_region)
    req_region_dict = region_context["requested_region"]

    if region_context["region_allowed_without_time_exemption"]:
        return _allow(
            exemption="normal",
            reason="行政区权限通过",
            original_time_span=original_time_span,
            time_granularity=time_granularity,
            matched_rules=["region_allowed_without_time_exemption"],
            debug_context={
                "level_allowed": region_context.get("level_allowed"),
                "same_province": region_context.get("same_province"),
                "same_city": region_context.get("same_city"),
            },
        )

    # 10. 行政区权限不通过，使用确定性行政区上下文给出的替代行政区
    replacement = region_context.get("replacement_region")
    if replacement:
        return _build_region_replacement_result(
            req_region_dict,
            replacement,
            time_granularity=time_granularity,
            original_time_span=original_time_span,
            region_context=region_context,
        )

    # 11. 无可用替代 -> 硬拒绝
    return _reject(
        fix_strategy="reject",
        reason="请求行政区超出权限范围，且无法找到替代区域",
        correction_text="因数据权限限制，您无权访问该数据。",
        time_granularity=time_granularity,
        original_time_span=original_time_span,
        matched_rules=["region_replacement_failed"],
    )


def _build_user_region_dict(user_profile: dict[str, Any]) -> dict[str, Any]:
    """将用户画像转换为内部格式."""
    bound = user_profile.get("bound_region")
    if isinstance(bound, dict):
        level = str(bound.get("level") or "")
        name = str(bound.get("name") or "")
    else:
        level = str(user_profile.get("region_level") or user_profile.get("level") or "")
        name = str(user_profile.get("region_name") or user_profile.get("name") or "")

    city_obj = user_profile.get("city")
    if isinstance(city_obj, dict):
        city_name = str(city_obj.get("name") or "")
    else:
        city_name = str(user_profile.get("city_name") or "")

    province_obj = user_profile.get("province")
    if isinstance(province_obj, dict):
        province_name = str(province_obj.get("name") or "")
    else:
        province_name = str(user_profile.get("province_name") or "")

    return {
        "name": name,
        "level": level,
        "city_name": city_name,
        "province_name": province_name,
    }


def _build_requested_region_dict(requested_region: dict[str, Any]) -> dict[str, Any]:
    """将 MCP 返回的 requested_region 转换为内部格式."""
    return {
        "name": str(
            requested_region.get("name") or requested_region.get("region_name") or ""
        ),
        "level": str(requested_region.get("level") or ""),
        "province_name": str(requested_region.get("province_name") or ""),
        "city_name": str(requested_region.get("city_name") or ""),
    }


def is_region_level_allowed(
    user_level: str | None, requested_level: str | None
) -> bool:
    """判断用户层级是否允许访问目标层级."""
    if not user_level or not requested_level:
        return False
    return requested_level in ALLOWED_LEVELS.get(user_level, set())
