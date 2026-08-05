"""权限审查使用的确定性行政区辅助逻辑."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from langchain_core.tools import tool

RegionDict = dict[str, Any]

ALLOWED_LEVELS: dict[str, set[str]] = {
    "province": {"province", "city"},
    "city": {"province", "city", "district"},
    "district": {"city", "district"},
}


def is_region_level_allowed(
    user_level: str | None, requested_level: str | None
) -> bool:
    """判断用户层级是否允许访问目标层级."""
    if not user_level or not requested_level:
        return False
    return requested_level in ALLOWED_LEVELS.get(user_level, set())


def _normalize_profile(user_profile: RegionDict) -> RegionDict:
    """将 get_user_profile MCP 返回值或 agent 手动构造的参数统一为内部格式.

    兼容三种输入：
    1. 内部格式: {"region_level": "district", "region_name": "西湖区"}
    2. agent 拆取格式: {"level": "district", "name": "西湖区"}
    3. get_user_profile 原始返回: {"bound_region": {...}, "city": {...}, "province": {...}}
    """
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

    frequent = user_profile.get("frequent_regions")
    if not isinstance(frequent, list):
        frequent = user_profile.get("frequent_regions")

    return {
        "name": name,
        "level": level or "",
        "data_type": "detail" if level == "district" else "summary",
        "province_name": province_name,
        "city_name": city_name,
        "frequent_regions": frequent,
    }


def _region_from_profile(user_profile: RegionDict) -> RegionDict:
    normalized = _normalize_profile(user_profile)
    return {
        "name": normalized["name"],
        "level": normalized["level"],
        "data_type": normalized["data_type"],
        "province_name": normalized["province_name"],
        "city_name": normalized["city_name"],
    }


def _region_from_request(region: RegionDict) -> RegionDict:
    """将 resolve_region_scope 返回结果或扁平区域对象统一为内部格式."""
    region_obj = region.get("region")
    source = region_obj if isinstance(region_obj, dict) else region

    name = str(source.get("name") or source.get("region_name") or "")
    level = str(source.get("level") or "")

    province_obj = region.get("province")
    city_obj = region.get("city")
    province_name = ""
    city_name = ""
    if isinstance(province_obj, dict):
        province_name = str(province_obj.get("name") or "")
    if isinstance(city_obj, dict):
        city_name = str(city_obj.get("name") or "")

    return {
        "name": name,
        "level": level,
        "data_type": source.get("data_type")
        or region.get("data_type")
        or ("detail" if level == "district" else "summary"),
        "province_name": region.get("province_name") or province_name,
        "city_name": region.get("city_name") or city_name,
    }


def _same_province(user: RegionDict, requested: RegionDict) -> bool:
    user_province = user.get("province_name")
    requested_province = requested.get("province_name")
    return bool(
        user_province and requested_province and user_province == requested_province
    )


def _same_city(user: RegionDict, requested: RegionDict) -> bool:
    user_city = user.get("city_name") or (
        user.get("name") if user.get("level") == "city" else None
    )
    requested_city = requested.get("city_name") or (
        requested.get("name") if requested.get("level") == "city" else None
    )
    return bool(user_city and requested_city and user_city == requested_city)


def default_allowed_region(user_profile: RegionDict) -> RegionDict:
    """返回用户自身关联的默认安全行政区."""
    user = _region_from_profile(user_profile)
    return {
        "name": user.get("name") or "",
        "level": user.get("level") or "",
        "data_type": "detail" if user.get("level") == "district" else "summary",
    }


def _frequent_or_default_district(
    user_profile: RegionDict, user: RegionDict
) -> RegionDict:
    frequent_regions = user_profile.get("frequent_regions")
    if isinstance(frequent_regions, list):
        for item in frequent_regions:
            if isinstance(item, dict) and item.get("level") == "district":
                return {
                    "name": str(item.get("name") or ""),
                    "level": "district",
                    "data_type": "detail",
                }
    return default_allowed_region(user_profile)


def get_replacement_region(
    user_profile: RegionDict, requested_region: RegionDict
) -> RegionDict | None:
    """按修正矩阵返回建议替代行政区；无需替换时返回 None."""
    user = _region_from_profile(user_profile)
    requested = _region_from_request(requested_region)
    user_level = user.get("level")
    requested_level = requested.get("level")
    if not user_level or not requested_level:
        return default_allowed_region(user_profile)

    same_province = _same_province(user, requested)
    same_city = _same_city(user, requested)

    if user_level == "province":
        if requested_level == "district":
            if same_province and requested.get("city_name"):
                return {
                    "name": requested["city_name"],
                    "level": "city",
                    "data_type": "summary",
                }
            return {
                "name": requested.get("province_name") or requested.get("name") or "",
                "level": "province",
                "data_type": "summary",
            }
        if requested_level == "city" and not same_province:
            return {
                "name": requested.get("province_name") or requested.get("name") or "",
                "level": "province",
                "data_type": "summary",
            }
        return None

    if user_level == "city":
        if requested_level == "district":
            if same_city:
                return None
            if same_province:
                return _frequent_or_default_district(user_profile, user)
            return default_allowed_region(user_profile)
        if requested_level == "province" and not same_province:
            return default_allowed_region(user_profile)
        if requested_level == "city" and not same_province:
            return default_allowed_region(user_profile)
        return None

    if user_level == "district":
        if requested_level == "province":
            return default_allowed_region(user_profile)
        if not same_city:
            return default_allowed_region(user_profile)
        return None

    return default_allowed_region(user_profile)


def build_permission_region_context(
    user_profile: RegionDict,
    requested_region: RegionDict,
) -> RegionDict:
    """构建权限 SubAgent 使用的确定性行政区上下文."""
    user = _region_from_profile(user_profile)
    requested = _region_from_request(requested_region)
    replacement = get_replacement_region(user_profile, requested_region)
    level_allowed = is_region_level_allowed(
        str(user.get("level") or ""),
        str(requested.get("level") or ""),
    )
    relation_allowed = replacement is None
    return {
        "user_region": user,
        "requested_region": requested,
        "level_allowed": level_allowed,
        "same_province": _same_province(user, requested),
        "same_city": _same_city(user, requested),
        "region_allowed_without_time_exemption": level_allowed and relation_allowed,
        "default_allowed_region": default_allowed_region(user_profile),
        "replacement_region": replacement,
    }


@tool
def permission_region_context_tool(
    user_profile: dict[str, Any],
    requested_region: dict[str, Any],
) -> dict[str, Any]:
    """返回权限审查所需的确定性行政区上下文和修正目标建议."""
    return build_permission_region_context(user_profile, requested_region)


# ── 时间豁免窗口确定性计算 ────────────────────────────────


def calculate_exemption_window(
    granularity: str,
    beijing_time: datetime,
) -> list[str]:
    """按时间粒度生成豁免窗口 [start, end]."""
    today = beijing_time.replace(hour=0, minute=0, second=0, microsecond=0)

    if granularity in ("hourly", "daily_count"):
        start = today - timedelta(days=6)
        end = today + timedelta(days=1) - timedelta(seconds=1)
        return [start.strftime("%Y-%m-%d 00:00:00"), end.strftime("%Y-%m-%d 23:59:59")]

    if granularity in ("daily", "week", "other"):
        start = today - timedelta(days=6)
        end = today
        return [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]

    if granularity in ("month", "month_count"):
        start = (today - timedelta(days=32)).replace(day=1)
        last_of_month = (today.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)
        end = last_of_month
        return [start.strftime("%Y-%m-01"), end.strftime("%Y-%m-%d")]

    if granularity in ("year", "year_count"):
        start_year = today.year - 1
        end_year = today.year
        return [f"{start_year}-01-01", f"{end_year}-12-31"]

    # 未知粒度兜底：最近 7 天（日级）
    start = today - timedelta(days=6)
    end = today
    return [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]


def calculate_time_intersection(
    original_span: list[str],
    exemption_window: list[str],
    granularity: str,
) -> str:
    """判断原始时间与豁免窗口的关系.

    返回: "full_window" | "partial" | "none"
    """
    if not original_span or len(original_span) < 2:
        return "none"

    try:
        orig_start = _parse_date_str(original_span[0])
        orig_end = _parse_date_str(original_span[1])
        exempt_start = _parse_date_str(exemption_window[0])
        exempt_end = _parse_date_str(exemption_window[1])
    except (ValueError, IndexError):
        return "none"

    if (
        orig_start is None
        or orig_end is None
        or exempt_start is None
        or exempt_end is None
    ):
        return "none"

    # 完全包含
    if orig_start >= exempt_start and orig_end <= exempt_end:
        return "full_window"

    # 无交集
    if orig_end < exempt_start or orig_start > exempt_end:
        return "none"

    # 部分交集
    return "partial"


def truncate_time_span(
    original_span: list[str],
    exemption_window: list[str],
) -> list[str]:
    """将原始时间范围截断到与豁免窗口的交集."""
    if (
        not original_span
        or len(original_span) < 2
        or not exemption_window
        or len(exemption_window) < 2
    ):
        return original_span

    try:
        orig_start = _parse_date_str(original_span[0])
        orig_end = _parse_date_str(original_span[1])
        exempt_start = _parse_date_str(exemption_window[0])
        exempt_end = _parse_date_str(exemption_window[1])
    except (ValueError, IndexError):
        return original_span

    if (
        orig_start is None
        or orig_end is None
        or exempt_start is None
        or exempt_end is None
    ):
        return original_span

    truncated_start = max(orig_start, exempt_start)
    truncated_end = min(orig_end, exempt_end)

    if truncated_start > truncated_end:
        return original_span

    fmt = "%Y-%m-%d" if len(exemption_window[0]) <= 10 else "%Y-%m-%d %H:%M:%S"
    return [truncated_start.strftime(fmt), truncated_end.strftime(fmt)]


def is_scope_excluded(
    time_span: list[str] | None,
    granularity: str,
    beijing_time: datetime,  # noqa: ARG001
) -> bool:
    """判断是否命中适用范围排除项（不得享受时间豁免）.

    排除项：
    - 历史数据补查：查询时间范围跨度超过 1 年
    - 单次查询涉及超过 365 个时间点

    注意：判断的是时间跨度（span_end - span_start），而非时间起点距今的距离。
    "去年全年"这类 year 粒度查询跨度为1年，不应被排除，应享受近两年豁免。

    Args:
        time_span: 原始时间范围 [start, end]。
        granularity: 时间粒度。
        beijing_time: 当前北京时间（保留参数以兼容调用方，当前逻辑未使用）。
    """
    if not time_span or len(time_span) < 2:
        return False

    try:
        span_start = _parse_date_str(time_span[0])
        span_end = _parse_date_str(time_span[1])
    except (ValueError, IndexError):
        return False

    if span_start is None or span_end is None:
        return False

    # 历史数据补查：时间跨度超过 1 年（365 天）
    span_days = (span_end - span_start).days + 1
    if span_days > 365:
        return True

    # 时间点数量估算
    if granularity in ("hourly",):
        if span_days * 24 > 8760:
            return True
    elif granularity in ("daily", "daily_count", "week", "other"):
        if span_days > 365:
            return True
    elif granularity in ("month", "month_count"):
        months = (
            (span_end.year - span_start.year) * 12
            + span_end.month
            - span_start.month
            + 1
        )
        if months > 12:
            return True

    return False


def _parse_date_str(s: str | None) -> datetime | None:
    """解析日期字符串，支持多种格式."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
