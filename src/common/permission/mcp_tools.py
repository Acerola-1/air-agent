"""MCP 工具封装：权限审查相关的 MCP 工具调用.

提供对 get_user_profile、resolve_region_scope 和站点归属解析 MCP 工具的封装，
统一处理参数构造、响应解析和异常处理。
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from common import mcp_client
from common.config import config
from common.mcp_utils import coerce_jsonish, select_tool

STATION_SCOPE_TOOL_NAMES = (
    "query_station_info",
    "resolve_station_scope",
    "resolve_station_region_scope",
    "query_station_scope",
)

# ── 进程内 TTL 缓存：用户画像与行政区解析结果 ──────────────────
# 两类事实在权限审查中每请求都需要，但变更频率极低，缓存后可避免
# 每次请求重复的 MCP 网络往返。仅缓存成功结果，TTL<=0 时禁用。
_PROFILE_CACHE_MAX_ENTRIES = 1024
_REGION_CACHE_MAX_ENTRIES = 2048

_profile_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_region_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def clear_permission_fact_caches() -> None:
    """清空用户画像与行政区解析缓存，供测试和运维使用."""
    _profile_cache.clear()
    _region_cache.clear()


def _is_valid_profile(profile: dict[str, Any]) -> bool:
    """判断画像是否可用（found 为真且能提取出行政区层级）.

    仅可用画像才允许写入缓存：偶发上游抖动返回的空/未命中画像不缓存，
    避免把一次瞬时失败放大成整个 TTL 窗口内的持续拒绝。
    """
    if profile.get("found") is not True:
        return False
    bound = profile.get("bound_region")
    if isinstance(bound, dict) and bound.get("level") in {
        "province",
        "city",
        "district",
    }:
        return True
    level = profile.get("region_level") or profile.get("level")
    return level in {"province", "city", "district"}


def _cache_get(
    cache: dict[Any, tuple[float, dict[str, Any]]],
    key: Any,
) -> dict[str, Any] | None:
    """读取未过期的缓存条目，过期时同步清理."""
    entry = cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        cache.pop(key, None)
        return None
    return value


def _cache_put(
    cache: dict[Any, tuple[float, dict[str, Any]]],
    key: Any,
    value: dict[str, Any],
    ttl_seconds: int,
    max_entries: int,
) -> None:
    """写入缓存条目，超出容量时按插入顺序淘汰最老条目."""
    if ttl_seconds <= 0:
        return
    if len(cache) >= max_entries:
        cache.pop(next(iter(cache)), None)
    cache[key] = (time.monotonic() + ttl_seconds, value)


async def fetch_user_profile(user_id: str) -> dict[str, Any] | None:
    """调用 MCP get_user_profile 获取用户权限画像.

    Args:
        user_id: 用户唯一标识

    Returns:
        用户画像字典，包含 found、bound_region、city、province、frequent_regions 等字段。
        失败时返回 None。

    Raises:
        不抛出异常，内部捕获并记录日志。
    """
    cached_profile = _cache_get(_profile_cache, user_id)
    if cached_profile is not None:
        return cached_profile

    profile_tool = select_tool(
        mcp_client.get_profile_mcp_tools(),
        "get_user_profile",
    )
    if profile_tool is None:
        logger.warning("get_user_profile MCP 工具不可用")
        return None

    try:
        raw_profile = await profile_tool.ainvoke({"userId": user_id})
        coerced_profile = coerce_jsonish(raw_profile)
        if isinstance(coerced_profile, dict):
            # 仅缓存可用画像；无效/未命中画像照常返回但不缓存，保证下次重试
            if _is_valid_profile(coerced_profile):
                _cache_put(
                    _profile_cache,
                    user_id,
                    coerced_profile,
                    config.PERMISSION_PROFILE_CACHE_TTL_SECONDS,
                    _PROFILE_CACHE_MAX_ENTRIES,
                )
            else:
                logger.warning(
                    "用户画像不可用，跳过缓存以便下次重试: user_id={}, profile={}",
                    user_id,
                    coerced_profile,
                )
            return coerced_profile
    except Exception as exc:
        logger.warning("get_user_profile 调用失败: {}", exc)

    return None


async def resolve_region(
    query: str,
    *,
    city: str = "",
    province: str = "",
) -> dict[str, Any] | None:
    """调用 MCP resolve_region_scope 解析行政区.

    将用户问题中的行政区名称、简称、别名解析为标准行政区对象。

    Args:
        query: 要解析的行政区文本，如"西湖区"、"杭州市"、"浙江"
        city: 消歧上下文-地市名称，用于处理重名区县（可选）
        province: 消歧上下文-省份名称，用于处理重名地市/区县（可选）

    Returns:
        解析后的行政区字典，包含 found、ambiguous、region、city、province 等字段。
        失败时返回 None。

    Raises:
        不抛出异常，内部捕获并记录日志。
    """
    cache_key = (query, city, province)
    cached_region = _cache_get(_region_cache, cache_key)
    if cached_region is not None:
        return cached_region

    region_tool = select_tool(
        mcp_client.get_region_mcp_tools(),
        "resolve_region_scope",
    )
    if region_tool is None:
        logger.warning("resolve_region_scope MCP 工具不可用")
        return None

    try:
        raw_region = await region_tool.ainvoke(
            {
                "query": query,
                "city": city,
                "province": province,
            },
        )
        coerced_region = coerce_jsonish(raw_region)
        if isinstance(coerced_region, dict):
            # 仅缓存成功解析结果；未命中/歧义结果不缓存，保证下次重试
            if coerced_region.get("found") is True:
                _cache_put(
                    _region_cache,
                    cache_key,
                    coerced_region,
                    config.PERMISSION_REGION_CACHE_TTL_SECONDS,
                    _REGION_CACHE_MAX_ENTRIES,
                )
            return coerced_region
    except Exception as exc:
        logger.warning("resolve_region_scope 调用失败: {}", exc)

    return None


def _clean_text(value: Any) -> str:
    """将工具返回字段清洗为字符串."""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_station_region(value: Any) -> dict[str, str] | None:
    """归一化站点所属行政区字段."""
    if not isinstance(value, dict):
        return None
    name = _clean_text(value.get("name") or value.get("region_name"))
    level = _clean_text(value.get("level"))
    city = _clean_text(value.get("city") or value.get("city_name"))
    province = _clean_text(value.get("province") or value.get("province_name"))

    city_value = value.get("city")
    if isinstance(city_value, dict):
        city = _clean_text(city_value.get("name"))
    province_value = value.get("province")
    if isinstance(province_value, dict):
        province = _clean_text(province_value.get("name"))

    if not name or level not in {"province", "city", "district"}:
        return None
    if not city or not province:
        return None
    return {
        "name": name,
        "level": level,
        "city": city,
        "province": province,
    }


def normalize_station_scope_result(raw: Any) -> dict[str, Any]:
    """将站点归属工具结果归一化为权限事实可用结构."""
    coerced = coerce_jsonish(raw)
    if not isinstance(coerced, dict):
        return {"found": False, "stations": [], "unavailable_stations": []}

    raw_stations = coerced.get("stations")
    if not isinstance(raw_stations, list):
        raw_stations = []

    stations: list[dict[str, Any]] = []
    unavailable_stations: list[dict[str, Any]] = []
    for item in raw_stations:
        if not isinstance(item, dict):
            continue
        station_name = _clean_text(
            item.get("station_name") or item.get("name") or item.get("stationName")
        )
        if not station_name:
            continue
        station_type = _clean_text(
            item.get("station_type") or item.get("type") or item.get("stationType")
        )
        normalized = {
            "station_name": station_name,
            "station_type": station_type,
            "region": _normalize_station_region(item.get("region")),
            "raw": item,
        }
        if normalized["region"] is None:
            unavailable_stations.append(normalized)
            continue
        stations.append(normalized)

    found = coerced.get("found")
    return {
        "found": bool(stations) if found is None else bool(found),
        "stations": stations,
        "unavailable_stations": unavailable_stations,
    }


async def resolve_station_scope(
    *,
    region: str,
    station_names: list[str] | None = None,
    station_types: list[str] | None = None,
) -> dict[str, Any]:
    """调用业务 MCP 工具解析站点归属行政区.

    Args:
        region: 本次限定查询区域名称
        station_names: 用户明确指定的站点名称列表
        station_types: 用户明确指定的站点类型列表

    Returns:
        归一化后的站点归属结果，包含 found、stations 和 unavailable_stations。
    """
    station_tool = None
    business_tools = mcp_client.get_business_mcp_tools()
    for tool_name in STATION_SCOPE_TOOL_NAMES:
        station_tool = select_tool(business_tools, tool_name)
        if station_tool is not None:
            break
    if station_tool is None:
        logger.warning("站点归属解析 MCP 工具不可用")
        return {"found": False, "stations": [], "unavailable_stations": []}

    try:
        raw_result = await station_tool.ainvoke(
            {
                "region": region,
                "station_names": station_names or [],
                "station_types": station_types or [],
            },
        )
        return normalize_station_scope_result(raw_result)
    except Exception as exc:
        logger.warning("站点归属解析工具调用失败: {}", exc)
        return {"found": False, "stations": [], "unavailable_stations": []}
