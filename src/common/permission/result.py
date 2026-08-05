"""权限审查结构化结果模型."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AllowedRegion(BaseModel):
    """修正后的允许行政区."""

    name: str = Field(description="行政区名称")
    level: str = Field(description="行政区层级: province/city/district")
    data_type: str = Field(description="数据类型: summary/detail")


class RegionPermissionResult(BaseModel):
    """单个请求区域的权限审查结果."""

    requested: dict[str, Any] = Field(
        default_factory=dict,
        description="请求区域项，包含 text/source/level_hint 等信息",
    )
    resolved: dict[str, Any] | None = Field(
        default=None,
        description="resolve_region_scope 标准化后的区域",
    )
    status: str = Field(
        description="逐项状态: allowed/replaced/rejected/time_truncated/auto_filled",
    )
    query_region: dict[str, Any] | None = Field(
        default=None,
        description="该项最终实际查询区域；拒绝时为空",
    )
    legal_time_span: list[str] | None = Field(
        default=None,
        description="该项时间截断后的合法时间范围",
    )
    correction_text: str = Field(
        default="",
        description="该项面向用户的修正说明",
    )
    reason: str = Field(
        default="",
        description="该项权限结果原因",
    )
    permission_result: dict[str, Any] = Field(
        default_factory=dict,
        description="单项规则引擎结果的有界摘要",
    )


class StationOverride(BaseModel):
    """后续查询必须使用的站点覆盖参数."""

    station_name: str = Field(description="标准站点名称")
    station_type: str = Field(default="", description="站点类型")
    region: dict[str, Any] | None = Field(
        default=None,
        description="站点归属行政区摘要",
    )
    source_region: dict[str, Any] | None = Field(
        default=None,
        description="来源查询区域摘要",
    )


class PermissionResult(BaseModel):
    """权限审查结果."""

    permitted: bool = Field(
        description="是否允许直接执行查询（完全豁免或无越权时为 True）"
    )
    exemption: str = Field(
        default="",
        description="豁免类型: full_window | normal | 空",
    )
    exemption_window: list[str] | None = Field(
        default=None,
        description="触发的豁免时间窗口 [start, end]，用于日志",
    )
    fix_strategy: str = Field(
        default="",
        description="修正策略: truncate_time | replace_region | 空",
    )
    legal_time_span: list[str] | None = Field(
        default=None,
        description="修正后的合法时间范围 [start, end]",
    )
    allowed_region: AllowedRegion | None = Field(
        default=None,
        description="修正后的允许行政区（仅在行政区越权时填充）",
    )
    region_mode: str = Field(
        default="",
        description="区域请求模式: single/multi_explicit/collection/auto_fill",
    )
    requested_regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="用户请求的区域列表",
    )
    allowed_regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="实际允许查询的区域列表",
    )
    corrected_regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="发生行政区替换或时间截断的区域列表",
    )
    rejected_regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="无法查询的区域列表",
    )
    region_corrections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="逐项区域订正详情",
    )
    region_results: list[RegionPermissionResult] = Field(
        default_factory=list,
        description="每个请求区域的解析、权限校验和最终查询结果",
    )
    accessible_station_count: int | None = Field(
        default=None,
        description="站点数据部分越权时，可访问的站点数量",
    )
    total_station_count: int | None = Field(
        default=None,
        description="站点数据权限审查时的候选站点总数",
    )
    unavailable_station_count: int | None = Field(
        default=None,
        description="站点归属缺失或无效导致不可用的站点数量",
    )
    station_overrides: list[StationOverride] = Field(
        default_factory=list,
        description="权限审查后的可执行站点集合",
    )
    accessible_grid_count: int | None = Field(
        default=None,
        description="网格数据部分越权时，可访问的网格数量",
    )
    reason: str = Field(
        default="",
        description="修正原因描述（面向用户的说明）",
    )
    correction_text: str = Field(
        default="",
        description="面向用户的修正说明文案（按文案模板生成）",
    )
    original_time_span: list[str] | None = Field(
        default=None,
        description="原始请求的时间范围 [start, end]",
    )
    time_granularity: str = Field(
        default="",
        description=(
            "推断的时间粒度: hourly/daily_count/daily/week/month/"
            "month_count/year/year_count/other"
        ),
    )
    matched_rules: list[str] = Field(
        default_factory=list,
        description="命中的规则列表，用于日志和测试",
    )
    debug_context: dict[str, Any] | None = Field(
        default=None,
        description="调试上下文，用于排障",
    )
    auto_filled: bool = Field(
        default=False,
        description="是否自动补全用户关联行政区",
    )
    rule_version: str = Field(
        default="V2.3",
        description="当前规则版本号",
    )
