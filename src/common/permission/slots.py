"""权限审查前的语义抽取模型与提示词.

LLM 只允许输出 PermissionExtractedSlots，不得输出 PermissionResult。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator
from typing_extensions import Annotated


def _parse_time_span(value: list[str] | str | None) -> list[str] | None:
    """解析时间范围，处理 LLM 返回 JSON 字符串的情况."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = parsed
            else:
                return None
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(value, list):
        return None
    if len(value) != 2:
        return None
    return [str(item) for item in value]


TimeGranularity = Literal[
    "hourly",
    "daily_count",
    "daily",
    "week",
    "month",
    "month_count",
    "year",
    "year_count",
    "other",
]

RegionLevel = Literal["province", "city", "district"]
RegionMode = Literal["single", "auto_fill", "multi_explicit", "collection"]
DataScope = Literal["region", "station"]
RegionSource = Literal[
    "explicit",
    "auto_fill",
    "collection_parent",
    "collection_child",
    "permission_replacement",
]
RegionRole = Literal["query", "collection_parent", "collection_child"]


class PermissionRegionRequest(BaseModel):
    """权限抽取阶段的区域请求项."""

    text: str = Field(description="用户问题中的行政区文本或系统补全区域名称")
    level_hint: RegionLevel | None = Field(
        default=None,
        description="该区域项的层级提示: province/city/district",
    )
    province_hint: str | None = Field(
        default=None,
        description="该区域项的省份消歧上下文",
    )
    city_hint: str | None = Field(
        default=None,
        description="该区域项的城市消歧上下文，主要用于区县解析",
    )
    source: RegionSource = Field(
        default="explicit",
        description="区域来源: explicit/auto_fill/collection_parent/collection_child/permission_replacement",
    )
    role: RegionRole = Field(
        default="query",
        description="区域角色: query/collection_parent/collection_child",
    )
    child_level: RegionLevel | None = Field(
        default=None,
        description="集合/下钻查询的目标下级层级",
    )

    @field_validator("level_hint", "child_level", mode="before")
    @classmethod
    def _normalize_region_level(cls, v: str | None) -> str | None:  # noqa: N805
        return normalize_target_level(v)


class PermissionExtractedSlots(BaseModel):
    """权限审查前的语义抽取结果.

    LLM 禁止输出以下字段：
    - permitted
    - fix_strategy
    - allowed_region
    - legal_time_span
    - exemption
    - correction_text

    这些字段必须由规则引擎生成。
    """

    need_check: bool = Field(
        default=True,
        description="是否需要权限校验。true=数据查询，false=聊天/质疑/知识问答",
    )
    data_scope: DataScope = Field(
        default="region",
        description="查询对象类型: region=行政区数据, station=站点数据",
    )
    region_mode: RegionMode = Field(
        default="auto_fill",
        description="区域请求模式: single/auto_fill/multi_explicit/collection",
    )
    regions: list[PermissionRegionRequest] = Field(
        default_factory=list,
        description="用户请求区域列表；权限链路的权威行政区输入。",
    )
    province: str | None = Field(
        default=None,
        description="Deprecated: 单区域兼容字段。权限链路不得优先读取该字段。",
    )
    city: str | None = Field(
        default=None,
        description="Deprecated: 单区域兼容字段。权限链路不得优先读取该字段。",
    )
    district: str | None = Field(
        default=None,
        description="Deprecated: 单区域兼容字段。权限链路不得优先读取该字段。",
    )
    region_entities: list[str] = Field(
        default_factory=list,
        description="Deprecated: 多区域兼容字段。权限链路不得优先读取该字段。",
    )
    region_collection_intent: str | None = Field(
        default=None,
        description="Deprecated: 集合语义兼容字段。权限链路不得优先读取该字段。",
    )
    target_level: str | None = Field(
        default=None,
        description="Deprecated: 单区域兼容字段。多区域层级以 regions[].level_hint 为准。",
    )
    time_granularity: str = Field(
        default="other",
        description="推断的时间粒度: hourly/daily_count/daily/week/month/month_count/year/year_count/other",
    )
    original_time_span: Annotated[
        list[str] | None,
        BeforeValidator(_parse_time_span),
    ] = Field(
        default=None,
        description="原始请求的时间范围 [start, end]，如 ['2026-05-01', '2026-05-07']",
    )
    metric_names: list[str] = Field(
        default_factory=list,
        description="用户提到的指标名称列表",
    )
    station_names: list[str] = Field(
        default_factory=list,
        description="用户提到的具体站点名称列表",
    )
    station_types: list[str] = Field(
        default_factory=list,
        description="用户提到的站点类型列表，仅作为查询筛选条件",
    )

    @field_validator("time_granularity")
    @classmethod
    def _normalize_granularity(cls, v: str) -> str:  # noqa: N805
        return normalize_time_granularity(v)

    @field_validator("target_level")
    @classmethod
    def _normalize_target_level(cls, v: str | None) -> str | None:  # noqa: N805
        return normalize_target_level(v)

    @field_validator("data_scope", mode="before")
    @classmethod
    def _normalize_data_scope(cls, v: str | None) -> str:  # noqa: N805
        if not v:
            return "region"
        normalized = str(v).strip().lower()
        return "station" if normalized == "station" else "region"

    @field_validator("station_names", "station_types", mode="before")
    @classmethod
    def _normalize_station_list(cls, value: object) -> list[str]:  # noqa: N805
        return normalize_string_list(value)


def normalize_string_list(value: object) -> list[str]:
    """清洗 LLM 输出的字符串列表，过滤空值并保持顺序去重."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = [value]
        value = parsed
    if not isinstance(value, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text in {"", "None", "none", "null", "NULL"}:
            continue
        if text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return cleaned


def normalize_time_granularity(value: str | None) -> str:
    """将时间粒度归一化为标准值."""
    if not value:
        return "other"
    normalized = str(value).strip().lower()
    valid = {
        "hourly",
        "daily_count",
        "daily",
        "week",
        "month",
        "month_count",
        "year",
        "year_count",
        "other",
    }
    if normalized in valid:
        return normalized
    # 常见别名映射
    aliases: dict[str, str] = {
        "hour": "hourly",
        "hours": "hourly",
        "day": "daily",
        "days": "daily",
        "weekly": "week",
        "monthly": "month",
        "yearly": "year",
        "annual": "year",
    }
    if normalized in aliases:
        return aliases[normalized]
    return "other"


def normalize_target_level(value: str | None) -> str | None:
    """将目标层级归一化为标准值."""
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"province", "city", "district"}:
        return normalized
    # 常见别名映射
    aliases: dict[str, str] = {
        "省": "province",
        "省级": "province",
        "市": "city",
        "市级": "city",
        "地市": "city",
        "县": "district",
        "区县": "district",
        "区": "district",
        "县级": "district",
    }
    if normalized in aliases:
        return aliases[normalized]
    return None


PERMISSION_SLOT_EXTRACTION_PROMPT = """你是空气质量智能问数系统的信息抽取助手。你的任务是从用户问题中抽取与权限审查相关的结构化信息。

## 重要约束

1. **你只做信息抽取，不做权限判定。** 不要输出 permitted、fix_strategy、allowed_region、legal_time_span、exemption、correction_text 等权限判定结果字段。
2. 相对时间必须基于任务中提供的"当前北京时间"归一化为具体 original_time_span。
3. 时间计算不需要 CodeInterpreter，但必须输出明确日期范围；无法确定时才设为 null。
4. 行政区识别不需要调用外部工具，只需提取问题中出现的行政区名称文本。
5. 站点类型不是权限类型，只能作为查询筛选条件抽取到 station_types。

## 判断是否需要权限校验

首先判断用户问题是否需要数据查询权限校验：

- **need_check=true**: 用户在问具体数据（时间、地点、指标），如"昨天北京PM2.5是多少"、"近7天空气质量排名"
- **need_check=false**: 用户在聊天、质疑、追问、纠正、问概念，如"你好"、"为什么选北京"、"什么是AQI"、"谢谢"

## 抽取字段

- **need_check**: 是否需要权限校验。true=数据查询，false=聊天/质疑/知识问答
- **data_scope**: 查询对象类型。普通行政区/城市/区县数据填 region；用户明确询问站点、监测站、监测点、国控站、省控站、市控站等站点数据时填 station。
- **region_mode**: 区域请求模式：
  - single: 用户明确请求一个行政区
  - multi_explicit: 用户明确列出多个行政区
  - collection: 用户请求某父级行政区下辖子级列表，如"郑州市下的区县"
  - auto_fill: 用户没有指定行政区，需要系统按用户关联区域自动补全
- **regions**: 用户请求区域列表，是权限链路的权威行政区输入。每项包含：
  - text: 行政区文本，如"郑州市"、"金水区"
  - level_hint: 该项层级提示，province/city/district；未知时为 null
  - province_hint: 该项省份消歧上下文；未提及时为 null
  - city_hint: 该项城市消歧上下文；区县项应尽量填写
  - source: 区域来源，用户明确输入填 explicit；自动补全由系统填 auto_fill；集合父级填 collection_parent
  - role: 普通查询区域填 query；集合父级填 collection_parent
  - child_level: 集合/下钻查询的目标下级层级；非集合项为 null
- **province/city/district/target_level/region_entities/region_collection_intent**: Deprecated 兼容字段。不要依赖这些字段表达最终行政区语义；多区域和混合层级必须使用 regions 列表。
- **time_granularity**: 推断的时间粒度。根据问题中的时间表达选择：
  - hourly: 小时数据，如"每小时"
  - daily_count: 日累计，如"日累计"
  - daily: 日数据，如"每天"
  - week: 周数据，如"本周"
  - month: 月数据，如"本月"
  - month_count: 月累计
  - year: 年数据，如"今年"
  - year_count: 年累计
  - other: 其他/无法确定（默认）
- **original_time_span**: 原始请求的时间范围 [start, end]。例如：["2026-05-01", "2026-05-07"] 或 ["2026-05-01 00:00:00", "2026-05-07 23:59:59"]。如果问题中没有明确时间范围，设为 null。
- **metric_names**: 用户提到的指标名称列表，如 ["PM2.5", "AQI"]。
- **station_names**: 用户明确提到的具体站点名称列表，如 ["水利监测站"]。没有具体站点名时填 []。
- **station_types**: 用户明确提到的站点类型列表，可选值包括标准站、国控站、省控站、市控站、乡镇站、微站、TVOC站、粉尘站、高密度站。站点类型只作为筛选条件，不得影响行政区权限判断。

## 示例

问题："昨天杭州市的PM2.5是多少？"
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "single",
  "regions": [
    {
      "text": "杭州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "daily",
  "original_time_span": ["2026-05-27", "2026-05-27"],
  "metric_names": ["PM2.5"],
  "station_names": [],
  "station_types": []
}
```

问题："查一下郑州市金水区的昨天的空气质量"
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "single",
  "regions": [
    {
      "text": "金水区",
      "level_hint": "district",
      "province_hint": null,
      "city_hint": "郑州市",
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "daily",
  "original_time_span": ["2026-05-27", "2026-05-27"],
  "metric_names": ["空气质量"],
  "station_names": [],
  "station_types": []
}
```

问题："2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？"
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "multi_explicit",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    },
    {
      "text": "平顶山市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "month",
  "original_time_span": ["2026-03-01", "2026-03-31"],
  "metric_names": ["PM2.5"],
  "station_names": [],
  "station_types": []
}
```

问题："2026年3月郑州市下的区县的PM2.5月均值分别是多少？"
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "collection",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "collection_parent",
      "child_level": "district"
    }
  ],
  "time_granularity": "month",
  "original_time_span": ["2026-03-01", "2026-03-31"],
  "metric_names": ["PM2.5"],
  "station_names": [],
  "station_types": []
}
```

问题："郑州市和金水区PM2.5分别是多少？"
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "multi_explicit",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    },
    {
      "text": "金水区",
      "level_hint": "district",
      "province_hint": null,
      "city_hint": "郑州市",
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "other",
  "original_time_span": null,
  "metric_names": ["PM2.5"],
  "station_names": [],
  "station_types": []
}
```

问题："最近7天全国空气质量排名"（假设当前北京时间为 2026-05-28 15:00:00）
```json
{
  "need_check": true,
  "data_scope": "region",
  "region_mode": "single",
  "regions": [
    {
      "text": "全国",
      "level_hint": "province",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "other",
  "original_time_span": ["2026-05-22", "2026-05-28"],
  "metric_names": ["空气质量"],
  "station_names": [],
  "station_types": []
}
```

问题："帮我查询一下郑州市下国控站的 AQI 排名"
```json
{
  "need_check": true,
  "data_scope": "station",
  "region_mode": "single",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "other",
  "original_time_span": null,
  "metric_names": ["AQI"],
  "station_names": [],
  "station_types": ["国控站"]
}
```

问题："郑州市水利监测站今天空气质量"
```json
{
  "need_check": true,
  "data_scope": "station",
  "region_mode": "single",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "daily",
  "original_time_span": ["2026-05-28", "2026-05-28"],
  "metric_names": ["空气质量"],
  "station_names": ["水利监测站"],
  "station_types": []
}
```

问题："郑州市和洛阳市下国控站 AQI 排名"
```json
{
  "need_check": true,
  "data_scope": "station",
  "region_mode": "multi_explicit",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    },
    {
      "text": "洛阳市",
      "level_hint": "city",
      "province_hint": null,
      "city_hint": null,
      "source": "explicit",
      "role": "query",
      "child_level": null
    }
  ],
  "time_granularity": "other",
  "original_time_span": null,
  "metric_names": ["AQI"],
  "station_names": [],
  "station_types": ["国控站"]
}
```

问题："什么是AQI？"
```json
{
  "need_check": false,
  "data_scope": "region",
  "region_mode": "auto_fill",
  "regions": [],
  "time_granularity": "other",
  "original_time_span": null,
  "metric_names": ["AQI"],
  "station_names": [],
  "station_types": []
}
```

问题："我没有问你具体位置，你为什么选择北京城区呢"
```json
{
  "need_check": false,
  "data_scope": "region",
  "region_mode": "auto_fill",
  "regions": [],
  "time_granularity": "other",
  "original_time_span": null,
  "metric_names": [],
  "station_names": [],
  "station_types": []
}
```
""".strip()
