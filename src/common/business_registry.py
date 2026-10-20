"""Agent 稳定业务目录与可信 Graph 映射（change simplify-agent-business-authorization）.

本模块是 Agent 侧唯一的业务准入策略来源：
- 稳定业务码、中文名、唯一 IAM 使用权限、实际 Graph；
- 业务可用的 MCP 服务及**显式工具白名单**（与部署连接配置的
  `approved_query_tools` 取交集；未登记工具不会自动开放）；
- 策略版本：许可快照与台账受理固定该版本，升级策略必须显式改版本，
  旧运行在逐发送复查时因版本不一致而停止。

扩展步骤见 docs/developer/business-authorization.md。新增业务必须同时：
登记资源与权限（IAM）、更新本注册表、由平台显式开通并由企业角色显式授予；
任何身份（含 root）都不会因前缀或管理员身份自动获得业务使用权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

BUSINESS_POLICY_VERSION = "2026-10-19.1"

_SERVER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class BusinessConnection:
    """业务允许使用的 MCP 服务及工具白名单."""

    server_key: str
    allowed_tools: frozenset[str]

    def allows(self, tool_name: str) -> bool:
        """工具必须被显式登记；空集合表示该业务不使用此服务."""
        return tool_name in self.allowed_tools


@dataclass(frozen=True)
class BusinessDefinition:
    """一个稳定业务的执行策略."""

    code: str
    name_zh: str
    permission: str
    graph: str
    connections: tuple[BusinessConnection, ...] = ()

    def connection(self, server_key: str) -> BusinessConnection | None:
        """返回该服务在业务内的显式工具白名单."""
        for item in self.connections:
            if item.server_key == server_key:
                return item
        return None

    def allows_tool(self, server_key: str, tool_name: str) -> bool:
        """业务是否显式允许该服务上的该工具."""
        conn = self.connection(server_key)
        return conn is not None and conn.allows(tool_name)


# 生产真实 MCP（ipp-air-mcp-server）的完整已审核查询工具清单。
# 该清单来自 2026-10-19 对 https://www.mapairs.com/product/datacenter/api2/mcp
# 的 tools/list 只读核验（109 项，全部为数据查询/分析/辅助时间读取，无写入操作）。
IPP_APPROVED_QUERY_TOOLS: tuple[str, ...] = (
    "broadcastHour",
    "cityFactorRankings",
    "compositeIndexSixFactorRatio",
    "concentration_ranking",
    "helper_getLatestTime2",
    "helper_get_latest_time",
    "integratedIndexRatio",
    "mcp_city_analysis_border",
    "mcp_city_analysis_boxPlot",
    "mcp_city_analysis_calculations",
    "mcp_city_analysis_calendar_day",
    "mcp_city_analysis_calendar_month",
    "mcp_city_analysis_calendar_year",
    "mcp_city_analysis_compliance_prediction",
    "mcp_city_analysis_concentrationContrastBar",
    "mcp_city_analysis_concentrationRation",
    "mcp_city_analysis_correlation",
    "mcp_city_analysis_monitor",
    "mcp_city_analysis_nationalOrder",
    "mcp_city_analysis_nationalOrderCompareBar",
    "mcp_city_analysis_nationalOrderContrastBar",
    "mcp_city_analysis_nationalOrderPercentage",
    "mcp_city_analysis_oneIndexManyCity24HourMean",
    "mcp_city_analysis_regionCviRadar",
    "mcp_city_analysis_timeCviRadarPie",
    "mcp_city_base_get_child_district_by_parent",
    "mcp_city_base_get_district_by_lnglat",
    "mcp_city_base_get_parent_district_by_child",
    "mcp_city_common_area_city_ranking",
    "mcp_city_common_area_multi_ranking",
    "mcp_city_common_area_province_ranking",
    "mcp_city_common_city_area_ranking",
    "mcp_city_common_factor_area_ranking",
    "mcp_city_common_factor_province_ranking",
    "mcp_city_common_get_air_quality_forecast",
    "mcp_city_common_get_air_quality_realtime_stat",
    "mcp_grid_base_around_grids",
    "mcp_grid_base_grid_by_station_proximity",
    "mcp_grid_base_grid_info_by_code",
    "mcp_grid_base_grid_info_by_codes",
    "mcp_grid_base_grid_info_by_hash",
    "mcp_grid_base_grid_list_by_region",
    "mcp_grid_base_latest_time",
    "mcp_grid_base_region_tree",
    "mcp_grid_common_abnormal_grid_statistics",
    "mcp_grid_common_air_data",
    "mcp_grid_common_alarm_area_count",
    "mcp_grid_common_alarm_data",
    "mcp_grid_common_alarm_grid_count",
    "mcp_grid_common_area_mma_data",
    "mcp_grid_common_area_monitor_data_page",
    "mcp_grid_common_grid_base_data",
    "mcp_grid_common_grid_statistics",
    "mcp_grid_common_grid_type_status",
    "mcp_grid_common_hot_grid_list",
    "mcp_grid_common_hot_grid_ranking_panel",
    "mcp_grid_common_hourly_alarm",
    "mcp_grid_common_left_panel",
    "mcp_grid_common_low_improve_alarm",
    "mcp_grid_common_monitor_data_page",
    "mcp_grid_common_push_grid",
    "mcp_grid_common_regularity_alarm",
    "mcp_grid_common_repeatability_alarm",
    "mcp_station_analysis_anomaly_map",
    "mcp_station_analysis_box_plot",
    "mcp_station_analysis_broadcast_hour",
    "mcp_station_analysis_calendar_day",
    "mcp_station_analysis_calendar_month",
    "mcp_station_analysis_calendar_year",
    "mcp_station_analysis_concentration_ranking",
    "mcp_station_analysis_concentration_ratio",
    "mcp_station_analysis_concentration_table",
    "mcp_station_analysis_correlation",
    "mcp_station_analysis_cvi_radar_pie",
    "mcp_station_analysis_cvi_radar_time",
    "mcp_station_analysis_evaluation_month",
    "mcp_station_analysis_history_pscf",
    "mcp_station_analysis_index_ranking",
    "mcp_station_analysis_index_stations_24h_avg",
    "mcp_station_analysis_integrated_index_bar",
    "mcp_station_analysis_integrated_index_pie",
    "mcp_station_analysis_integrated_index_table",
    "mcp_station_analysis_one_index_multi_station_line",
    "mcp_station_analysis_one_station_multi_index_line",
    "mcp_station_analysis_single_pscf",
    "mcp_station_analysis_trajectory",
    "mcp_station_base_air_station_list",
    "mcp_station_base_get_info_by_code_name",
    "mcp_station_base_lng_lat_near_national_province_station",
    "mcp_station_base_range_site_list",
    "mcp_station_common_air_data",
    "mcp_station_common_air_quality_forecast",
    "mcp_station_common_air_real_data",
    "mcp_station_common_good_days_query",
    "mcp_station_common_lng_lat_scope_air_data",
    "mcp_weather_forecast_daily",
    "mcp_weather_forecast_daily_history",
    "mcp_weather_forecast_hourly",
    "mcp_weather_forecast_hourly_history",
    "mcp_weather_numeric_forecast",
    "mcp_weather_numeric_forecast_history",
    "mcp_weather_nwp_city_forecas",
    "mcp_weather_nwp_city_history",
    "oneIndexManyCityLine",
    "primaryPollutantProportionAnaly",
    "text2sql_city_indexes_month_and_year",
    "text2sql_monthly_and_yearly_city_comparison",
    "text2sql_today_single_city_aqi_route",
    "text2sql_yesterday_single_index_aqi_route",
)


def _ipp(tools: Iterable[str]) -> tuple[BusinessConnection, ...]:
    return (
        BusinessConnection(
            server_key="ipp",
            allowed_tools=frozenset(tools),
        ),
    )


# 溯源业务的窄工具子集：在通用已审核清单之上再排除轨迹/PSCF。
# 这些工具需要额外参数和专门可视化，不属于本阶段受控溯源的数据边界；
# 执行层另有名称拒绝作为第二道防线（见 intelligent_tracing.data_access）。
TRACING_EXCLUDED_TOOLS: tuple[str, ...] = (
    "mcp_station_analysis_trajectory",
    "mcp_station_analysis_single_pscf",
    "mcp_station_analysis_history_pscf",
)
TRACING_APPROVED_QUERY_TOOLS: tuple[str, ...] = tuple(
    tool for tool in IPP_APPROVED_QUERY_TOOLS if tool not in TRACING_EXCLUDED_TOOLS
)


BUSINESSES: dict[str, BusinessDefinition] = {
    "basic-qa": BusinessDefinition(
        code="basic-qa",
        name_zh="基础问答",
        permission="agent:business:basic-qa:use",
        graph="basic-qa",
        connections=_ipp(
            (
                "broadcastHour",
                "cityFactorRankings",
                "helper_get_latest_time",
                "integratedIndexRatio",
                "mcp_city_analysis_calculations",
                "mcp_city_analysis_compliance_prediction",
                "mcp_city_common_get_air_quality_forecast",
                "mcp_city_common_get_air_quality_realtime_stat",
                "mcp_station_base_air_station_list",
                "mcp_station_base_get_info_by_code_name",
                "mcp_station_base_lng_lat_near_national_province_station",
                "mcp_station_base_range_site_list",
                "mcp_station_common_air_data",
                "mcp_station_common_air_quality_forecast",
                "mcp_weather_forecast_daily",
                "mcp_weather_forecast_daily_history",
                "mcp_weather_forecast_hourly",
                "mcp_weather_forecast_hourly_history",
                "oneIndexManyCityLine",
                "text2sql_city_indexes_month_and_year",
                "text2sql_monthly_and_yearly_city_comparison",
            )
        ),
    ),
    "data-analysis": BusinessDefinition(
        code="data-analysis",
        name_zh="数据分析",
        permission="agent:business:data-analysis:use",
        graph="data-analysis",
        # 数据分析是菜单驱动的通用分析业务；其 Skill 可按菜单调用城市/站点/网格/
        # 气象等已审核查询工具。业务范围与连接 approved_query_tools 取交集，
        # 未登记的新工具仍不会自动出现。
        connections=_ipp(IPP_APPROVED_QUERY_TOOLS),
    ),
    "intelligent-tracing": BusinessDefinition(
        code="intelligent-tracing",
        name_zh="智能溯源",
        permission="agent:business:intelligent-tracing:use",
        graph="intelligent-tracing",
        # 溯源只使用窄工具子集：通用已审核清单再去掉轨迹/PSCF；
        # 仍与连接配置 approved_query_tools 取交集，新增工具不会自动出现。
        connections=_ipp(TRACING_APPROVED_QUERY_TOOLS),
    ),
}

BUSINESS_BY_GRAPH: dict[str, BusinessDefinition] = {
    item.graph: item for item in BUSINESSES.values()
}


def business_for_graph(graph_id: str) -> BusinessDefinition | None:
    """按实际执行图解析业务；未知图返回 None（调用方必须拒绝）."""
    return BUSINESS_BY_GRAPH.get(str(graph_id or ""))


def business_for_code(code: str) -> BusinessDefinition | None:
    """按稳定业务码解析；未知码返回 None."""
    return BUSINESSES.get(str(code or ""))


def business_permission(code: str) -> str:
    """稳定业务码 → 精确 IAM 使用权限码（不做前缀猜测）."""
    definition = business_for_code(code)
    if definition is None:
        raise KeyError(f"未登记业务: {code}")
    return definition.permission


def has_business_permission(permissions: Iterable[str], code: str) -> bool:
    """精确匹配业务使用权限；平台管理员/root 没有隐式通配."""
    definition = business_for_code(code)
    if definition is None:
        return False
    return definition.permission in set(permissions or ())


def available_businesses(permissions: Iterable[str]) -> list[BusinessDefinition]:
    """当前有效权限下可用的业务（稳定排序）."""
    granted = set(permissions or ())
    return [
        item for code, item in sorted(BUSINESSES.items()) if item.permission in granted
    ]


def business_allows_tool(code: str, server_key: str, tool_name: str) -> bool:
    """业务是否允许调用该服务上的该工具（双白名单交集语义）."""
    definition = business_for_code(code)
    if definition is None:
        return False
    return definition.allows_tool(server_key, tool_name)


def validate_registry(known_graphs: Iterable[str] | None = None) -> None:
    """启动/测试期校验：登记必须自洽，且与实际公开图一致.

    未登记图不会被放行（business_for_graph 返回 None）；这里额外拒绝
    "登记了但实际不存在" 的映射，避免改名后静默失效。
    """
    seen_graphs: set[str] = set()
    for code, item in BUSINESSES.items():
        if code != item.code:
            raise ValueError(f"业务目录键与编码不一致: {code}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", item.code):
            raise ValueError(f"非法业务编码: {item.code}")
        if item.permission != f"agent:business:{item.code}:use":
            raise ValueError(f"业务权限码必须精确匹配: {item.code}")
        if item.graph in seen_graphs:
            raise ValueError(f"多个业务映射到同一图: {item.graph}")
        seen_graphs.add(item.graph)
        for conn in item.connections:
            if not _SERVER_KEY_PATTERN.fullmatch(conn.server_key):
                raise ValueError(f"非法服务键: {conn.server_key}")
            if not conn.allowed_tools:
                raise ValueError(
                    f"业务 {item.code} 的服务 {conn.server_key} 未登记工具"
                )
            for tool in conn.allowed_tools:
                if not tool or len(tool) > 200:
                    raise ValueError("工具名非法")
    if known_graphs is not None:
        known = set(known_graphs)
        missing = sorted(seen_graphs - known)
        if missing:
            raise ValueError(f"业务目录登记了不存在的图: {missing}")
        # 实际存在但未登记的图默认拒绝；不在这里抛错，以免新图导入过程
        # 直接破坏已有图加载。由目录/受理/原生入口统一拒绝并报告。


def registry_summary() -> list[dict[str, object]]:
    """非秘密目录摘要（供文档/运维核对，不含凭据）."""
    return [
        {
            "code": item.code,
            "name_zh": item.name_zh,
            "policy_version": BUSINESS_POLICY_VERSION,
            "permission": item.permission,
            "graph": item.graph,
            "connections": [
                {
                    "server_key": conn.server_key,
                    "allowed_tool_count": len(conn.allowed_tools),
                }
                for conn in item.connections
            ],
        }
        for item in BUSINESSES.values()
    ]
