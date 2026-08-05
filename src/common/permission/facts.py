"""权限规则引擎输入事实模型.

PermissionFacts 是规则引擎的唯一输入，不包含判定结果。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.permission.slots import (
    normalize_string_list,
    normalize_target_level,
    normalize_time_granularity,
)


class StationPermissionCandidate(BaseModel):
    """规则引擎使用的标准站点候选事实."""

    station_name: str = Field(description="标准站点名称")
    station_type: str = Field(default="", description="站点类型，仅作为筛选条件")
    region: dict[str, Any] | None = Field(
        default=None,
        description="站点归属行政区，包含 name/level/city/province",
    )
    source_region: dict[str, Any] | None = Field(
        default=None,
        description="触发本次站点解析的来源查询区域",
    )
    source_item: dict[str, Any] | None = Field(
        default=None,
        description="来源 RegionRequestItem 摘要",
    )
    raw: dict[str, Any] | None = Field(
        default=None,
        description="站点工具原始站点项，用于调试",
    )


class PermissionFacts(BaseModel):
    """权限规则引擎输入事实.

    不包含判定结果。所有 dict 输入在构造时做基础校验和归一化。
    """

    model_config = ConfigDict(extra="ignore")

    question: str = Field(description="用户原始问题")
    user_id: str = Field(description="用户ID")
    beijing_time: str = Field(description="当前北京时间字符串")
    user_profile: dict[str, Any] = Field(description="get_user_profile MCP 返回结果")
    requested_region: dict[str, Any] | None = Field(
        default=None,
        description="resolve_region_scope MCP 返回结果",
    )
    original_time_span: list[str] | None = Field(
        default=None, description="原始时间范围"
    )
    time_granularity: str = Field(default="other", description="时间粒度")
    extraction_confidence: float | None = Field(
        default=None, description="slots 抽取置信度"
    )
    data_scope: str = Field(default="region", description="查询对象类型")
    station_names: list[str] = Field(
        default_factory=list,
        description="用户请求的具体站点名称筛选条件",
    )
    station_types: list[str] = Field(
        default_factory=list,
        description="用户请求的站点类型筛选条件",
    )
    station_candidates: list[StationPermissionCandidate] = Field(
        default_factory=list,
        description="站点归属工具返回的候选站点事实",
    )
    unavailable_stations: list[StationPermissionCandidate] = Field(
        default_factory=list,
        description="归属缺失或无效导致不可用的站点事实",
    )
    station_region_mode: str = Field(
        default="",
        description="站点查询对应的原始区域计划模式",
    )
    raw_slots: dict[str, Any] | None = Field(
        default=None, description="原始 slots 数据"
    )
    raw_context: dict[str, Any] | None = Field(
        default=None, description="原始上下文数据"
    )

    @field_validator("original_time_span", mode="before")
    @classmethod
    def _validate_time_span(cls, value: Any) -> list[str] | None:  # noqa: ANN401
        """校验时间范围只能为空或两个元素."""
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 2:
            return None
        return [str(item) for item in value]

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """模型初始化后归一化字段."""
        self.time_granularity = normalize_time_granularity(self.time_granularity)
        self.data_scope = "station" if self.data_scope == "station" else "region"
        self.station_names = normalize_string_list(self.station_names)
        self.station_types = normalize_string_list(self.station_types)
        if self.requested_region and not isinstance(self.requested_region, dict):
            self.requested_region = None


def build_permission_facts(
    *,
    question: str,
    user_id: str,
    beijing_time: str,
    user_profile: dict[str, Any],
    requested_region: dict[str, Any] | None = None,
    slots: dict[str, Any] | None = None,
    station_candidates: list[dict[str, Any] | StationPermissionCandidate] | None = None,
    unavailable_stations: (
        list[dict[str, Any] | StationPermissionCandidate] | None
    ) = None,
    station_region_mode: str = "",
    raw_context: dict[str, Any] | None = None,
) -> PermissionFacts:
    """统一从 slots、MCP 结果和问题构造 PermissionFacts.

    Args:
        question: 用户原始问题
        user_id: 用户ID
        beijing_time: 当前北京时间字符串
        user_profile: get_user_profile MCP 返回结果
        requested_region: resolve_region_scope MCP 返回结果
        slots: PermissionExtractedSlots 的 dict 形式
        raw_context: 原始上下文数据

    Returns:
        PermissionFacts 实例
    """
    slots = slots or {}
    time_granularity = normalize_time_granularity(slots.get("time_granularity"))
    target_level = normalize_target_level(slots.get("target_level"))
    data_scope = "station" if slots.get("data_scope") == "station" else "region"

    # 如果 slots 中有 district/city/province 但 requested_region 为空，保留 slots 信息
    district = slots.get("district")
    city = slots.get("city")
    province = slots.get("province")

    # 过滤 LLM 输出的 "None" 字符串
    def _clean_slot(value):
        if isinstance(value, str) and value.strip() in ("None", "null", "NULL", ""):
            return None
        return value

    district = _clean_slot(district)
    city = _clean_slot(city)
    province = _clean_slot(province)
    if (district or city or province) and requested_region is None:
        requested_region = {
            "found": False,
            "ambiguous": False,
            "query": district or city or province,
            "target_level": target_level,
        }

    original_time_span = slots.get("original_time_span")

    return PermissionFacts(
        question=question,
        user_id=user_id,
        beijing_time=beijing_time,
        user_profile=user_profile,
        requested_region=requested_region,
        original_time_span=original_time_span,
        time_granularity=time_granularity,
        data_scope=data_scope,
        station_names=normalize_string_list(slots.get("station_names")),
        station_types=normalize_string_list(slots.get("station_types")),
        station_candidates=[
            item
            if isinstance(item, StationPermissionCandidate)
            else StationPermissionCandidate.model_validate(item)
            for item in (station_candidates or [])
        ],
        unavailable_stations=[
            item
            if isinstance(item, StationPermissionCandidate)
            else StationPermissionCandidate.model_validate(item)
            for item in (unavailable_stations or [])
        ],
        station_region_mode=station_region_mode,
        raw_slots=slots,
        raw_context=raw_context,
    )
