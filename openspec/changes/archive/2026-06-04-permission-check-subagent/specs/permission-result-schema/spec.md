# PermissionResult Schema Spec

## Purpose

定义权限审查 SubAgent 的结构化输出格式，作为 SubAgent 的 `response_format` 参数和跨 Agent 传递的契约。

## Definition

```python
from pydantic import BaseModel, Field
from typing import Optional


class AllowedRegion(BaseModel):
    """修正后的允许行政区."""
    code: str = Field(description="行政区代码")
    name: str = Field(description="行政区名称")
    level: str = Field(description="行政区层级: province/city/district")
    data_type: str = Field(description="数据类型: summary/detail")


class PermissionResult(BaseModel):
    """权限审查结果."""
    permitted: bool = Field(
        description="是否允许直接执行查询（完全豁免或无越权时为True）"
    )
    exemption: str = Field(
        default="",
        description="豁免类型: full_window | normal | 空",
    )
    exemption_window: Optional[list[str]] = Field(
        default=None,
        description="触发的豁免时间窗口 [start, end]，用于日志",
    )
    fix_strategy: str = Field(
        default="",
        description="修正策略: truncate_time | replace_region | 空",
    )
    legal_time_span: Optional[list[str]] = Field(
        default=None,
        description="修正后的合法时间范围 [start, end]",
    )
    allowed_region: Optional[AllowedRegion] = Field(
        default=None,
        description="修正后的允许行政区（仅在行政区越权时填充）",
    )
    data_type: str = Field(
        default="region",
        description="数据类型: region | station | grid。用于区分行政区/站点/网格查询的权限规则",
    )
    accessible_station_count: Optional[int] = Field(
        default=None,
        description="站点数据部分越权时，可访问的站点数量",
    )
    accessible_grid_count: Optional[int] = Field(
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
    original_time_span: Optional[list[str]] = Field(
        default=None,
        description="原始请求的时间范围 [start, end]",
    )
    time_granularity: str = Field(
        default="",
        description="推断的时间粒度: hourly/daily_count/daily/week/month/month_count/year/year_count/other",
    )
    rule_version: str = Field(
        default="V2.3",
        description="当前规则版本号",
    )
```

## Field Notes

- `exemption_window` 和 `legal_time_span` 使用 `list[str]`（长度为 2 的列表 [start, end]）而非 `tuple`，因为 Pydantic JSON 序列化对 tuple 的支持不如 list 稳定
- `allowed_region` 仅在行政区越权时由 SubAgent 填充；完全豁免时为 None
- `correction_text` 是面向用户的最终文案，主模型在回复中应原样使用或融入回答
- `time_granularity` 回传给主模型，供后续 MCP 工具调用参考
- `fix_strategy` 为空字符串表示无修正；主模型据此决定是否调整 MCP 调用参数

## SubAgent Response Flow

```
SubAgent 返回 PermissionResult
  → DeepAgents 自动序列化为 JSON
  → 通过 ToolMessage.content 传递给主模型
  → 主模型解析 JSON，读取修正参数
  → 主模型按修正参数调整 MCP 工具调用
  → 主模型在回复中包含 correction_text
```

## File Location

`src/common/permission_result.py`
