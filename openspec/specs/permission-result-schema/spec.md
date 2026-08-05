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
## Requirements
### Requirement: PermissionResult distinguishes auto-fill from replacement

The system SHALL represent automatic default-region completion separately from over-permission region replacement, including in per-region results.

#### Scenario: Auto-filled default region

- **WHEN** the user did not specify a region and the system uses `bound_region`
- **THEN** `PermissionResult.auto_filled` SHALL be `true`
- **AND** `PermissionResult.fix_strategy` SHALL NOT be `replace_region` solely because of the auto-fill
- **AND** `PermissionResult.region_results` SHALL contain an item with `requested.source = "auto_fill"` or equivalent source marker
- **AND** `PermissionResult.correction_text` SHALL describe the auto-filled query region

#### Scenario: Over-permission replacement

- **WHEN** the user specified a region outside their permission scope and the system replaces it
- **THEN** the corresponding `region_results` item SHALL contain `status = "replaced"`
- **AND** `PermissionResult.fix_strategy` SHALL be `replace_region` only for single-region compatibility or aggregate summary when appropriate
- **AND** `PermissionResult.auto_filled` SHALL be `false`

### Requirement: PermissionResult exposes diagnostics for main flow

The system SHALL expose rule matching, debug information, and per-region permission details in `PermissionResult` so the main flow can explain or troubleshoot permission corrections without rerunning permission checks.

#### Scenario: Rule engine returns diagnostics

- **WHEN** permission check completes
- **THEN** `PermissionResult.matched_rules` SHALL contain the matched deterministic rule identifiers
- **AND** `PermissionResult.debug_context` SHALL contain bounded structured context for troubleshooting when available
- **AND** `PermissionResult.region_results` SHALL contain bounded per-region status details for user-facing explanation and audit

### Requirement: PermissionResult Schema Definition

The `PermissionResult` schema SHALL include fields required to represent permission decisions, corrections, automatic default-region completion, diagnostics, and per-region permission results.

#### Scenario: Schema contains correction and auto-fill fields

- **WHEN** a `PermissionResult` is serialized
- **THEN** it SHALL include `permitted`, `exemption`, `fix_strategy`, `correction_text`, `original_time_span`, `time_granularity`, `rule_version`, `matched_rules`, and `auto_filled`
- **AND** it SHALL include `region_mode`, `region_results`, `allowed_regions`, `corrected_regions`, and `rejected_regions`
- **AND** it SHALL include `allowed_region` only as a single-region compatibility field when an executable single-region override is needed

#### Scenario: regions used for executable region override

- **WHEN** permission check produces one or more regions that downstream query execution must use
- **THEN** `permission_query_overrides.regions` SHALL contain executable region objects with `name`, `level`, and `data_type`
- **AND** `PermissionResult.allowed_regions` SHALL mirror the executable allowed region list
- **AND** callers SHALL use `region_results`, `auto_filled`, and `fix_strategy` to distinguish auto-fill, replacement, rejection, and time truncation cases
- **AND** callers SHALL NOT depend on `allowed_region` when `permission_query_overrides.regions` exists

### Requirement: PermissionResult exposes per-region permission results

权限结果 SHALL 使用 `region_results` 记录每个请求区域从原始请求、行政区解析、规则校验到最终查询区域的逐项结果。

#### Scenario: 多区域全部允许

- **WHEN** 用户请求 `郑州市` 和 `平顶山市` 且两个区域均允许查询
- **THEN** `PermissionResult.region_results` SHALL 包含两个结果项
- **AND** 每个结果项 SHALL 包含 `requested`、`resolved`、`status` 和 `query_region`
- **AND** 两个结果项的 `status` SHALL 为 `allowed`
- **AND** `PermissionResult.allowed_regions` SHALL 包含两个实际查询区域

#### Scenario: 多区域部分替换

- **WHEN** 多个请求区域中某个区域因越权被替换
- **THEN** 对应 `region_results` 项 SHALL 包含 `status = "replaced"`
- **AND** 该项 SHALL 包含原始请求区域
- **AND** 该项 SHALL 包含替换后的 `query_region`
- **AND** `PermissionResult.correction_text` SHALL 说明替换关系

#### Scenario: 多区域部分拒绝

- **WHEN** 多个请求区域中某个区域无法解析或无权访问且无可用修正策略
- **THEN** 对应 `region_results` 项 SHALL 包含 `status = "rejected"`
- **AND** 该项 SHALL 包含拒绝原因
- **AND** `PermissionResult.rejected_regions` SHALL 包含该区域
- **AND** 允许区域 SHALL 继续写入 `PermissionResult.allowed_regions`

#### Scenario: 多区域时间截断

- **WHEN** 某个区域项触发时间截断
- **THEN** 对应 `region_results` 项 SHALL 包含 `status = "time_truncated"` 或等价可区分状态
- **AND** 该项 SHALL 包含该区域的合法时间范围
- **AND** 系统 SHALL NOT 丢弃逐项合法时间范围

### Requirement: 权限结果使用矩阵化修正文案

`PermissionResult.correction_text` SHALL 使用确定性模板表达权限修正原因和修正结果，并按照用户层级、越权场景和时间粒度生成细分文案。

#### Scenario: 省级用户区县上卷至地市

- **WHEN** 省级用户请求区县明细数据且规则引擎将查询区域上卷至所属地市
- **THEN** `PermissionResult.correction_text` SHALL 说明省级用户无法查看区县明细数据
- **AND** 文案 SHALL 说明已展示该区县所属地市的汇总数据

#### Scenario: 省级用户外省地市或区县上卷至省份

- **WHEN** 省级用户请求外省地市或外省区县数据且规则引擎将查询区域上卷至所属省份
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外省地市或区县数据
- **AND** 文案 SHALL 说明已展示请求区域所属省份的省级汇总数据
- **AND** 文案 SHALL 提示如需查看明细可查询最近 7 天数据

#### Scenario: 地市用户本省其他区县替换

- **WHEN** 地市用户请求本省其他地市的区县数据且规则引擎替换为本市常访问区县或关联区域
- **THEN** `PermissionResult.correction_text` SHALL 说明地市用户无法查看其他市区县数据
- **AND** 文案 SHALL 说明已展示用户所在城市的可访问区域数据

#### Scenario: 地市用户外省替换

- **WHEN** 地市用户请求外省任意层级数据且规则引擎替换为用户关联地市
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外省数据
- **AND** 文案 SHALL 说明已展示用户所在城市数据

#### Scenario: 区县用户省级汇总替换

- **WHEN** 区县用户请求省级汇总数据且规则引擎替换为用户关联区县
- **THEN** `PermissionResult.correction_text` SHALL 说明区县用户无法查看省级汇总数据
- **AND** 文案 SHALL 说明已展示用户所在区县明细数据
- **AND** 文案 SHALL 提示如需查看省级汇总可查询最近 7 天数据

#### Scenario: 区县用户外市替换

- **WHEN** 区县用户请求外市任意层级数据且规则引擎替换为用户关联区县
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外市数据
- **AND** 文案 SHALL 说明已展示用户所在区县数据

### Requirement: 月年粒度越权使用专门文案

当月粒度或年粒度查询发生行政区替换时，`PermissionResult.correction_text` SHALL 使用月年统计专门模板，明确月年统计数据不支持跨行政区查看。

#### Scenario: 月粒度越权替换

- **WHEN** `time_granularity` 为 `month` 或 `month_count` 且规则引擎返回 `fix_strategy = "replace_region"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“月/年统计数据不支持跨行政区查看”或等价说明
- **AND** 文案 SHALL 说明已切换至有权限的替代区域数据

#### Scenario: 年粒度越权替换

- **WHEN** `time_granularity` 为 `year` 或 `year_count` 且规则引擎返回 `fix_strategy = "replace_region"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“月/年统计数据不支持跨行政区查看”或等价说明
- **AND** 文案 SHALL 说明已切换至有权限的替代区域数据

### Requirement: 时间截断使用豁免窗口文案

当规则引擎返回 `fix_strategy = "truncate_time"` 时，`PermissionResult.correction_text` SHALL 说明原查询日期范围部分超出豁免窗口，并说明已截断至合法时间范围。

#### Scenario: 时间段部分超出豁免窗口

- **WHEN** 用户请求的时间段与豁免窗口部分相交
- **THEN** `PermissionResult.fix_strategy` SHALL 为 `truncate_time`
- **AND** `PermissionResult.correction_text` SHALL 说明查询日期范围部分超出豁免窗口
- **AND** 文案 SHALL 包含 `legal_time_span` 表示的合法时间范围
- **AND** 文案 SHALL NOT 表示行政区被替换

### Requirement: 站点修正文案保持专用模板

站点权限裁剪和站点归属缺失 SHALL 继续使用站点专用文案，不得被行政区通用模板覆盖。

#### Scenario: 站点部分越权

- **WHEN** 站点权限审查返回 `fix_strategy = "filter_stations"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“仅展示您可访问的”站点数据提示
- **AND** 文案 SHALL 提示如需查看其他区域站点可查询最近 7 天数据

#### Scenario: 站点归属缺失

- **WHEN** 站点归属缺失或无效导致数据不可用
- **THEN** `PermissionResult.correction_text` SHALL 包含“该数据暂不可用”

### Requirement: PermissionResult 表达站点权限裁剪结果

`PermissionResult` SHALL 表达站点权限审查后的可执行站点集合、候选站点总数和可访问站点数量，以便主流程和业务 Skill 使用裁剪后的站点范围执行查询。

#### Scenario: 多站点部分可访问

- **WHEN** 站点权限审查发现候选站点中仅部分站点可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 表示站点范围裁剪
- **AND** `PermissionResult.accessible_station_count` SHALL 等于可访问站点数量
- **AND** `PermissionResult` SHALL 包含可执行站点集合
- **AND** `PermissionResult.correction_text` SHALL 包含“仅展示您可访问的”站点数据提示

#### Scenario: 全部站点可访问

- **WHEN** 站点权限审查发现所有候选站点均可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `true`
- **AND** `PermissionResult.exemption` SHALL 为 `normal` 或 `full_window`
- **AND** `PermissionResult` MAY 包含原始候选站点集合用于后续查询覆盖

#### Scenario: 全部站点不可访问

- **WHEN** 站点权限审查发现所有候选站点均不可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 为 `reject`
- **AND** `PermissionResult.correction_text` SHALL 告知用户无权访问该站点数据或该数据暂不可用

### Requirement: permission_query_overrides 支持站点覆盖

系统 SHALL 从 `PermissionResult` 提取站点覆盖参数，并通过 `permission_query_overrides` 传递给后续查询流程。

#### Scenario: 站点裁剪覆盖参数

- **WHEN** `PermissionResult` 包含可执行站点集合
- **THEN** `permission_query_overrides` SHALL 包含该站点集合
- **AND** `permission_query_overrides` SHALL 保留既有 `regions`、`region`、`region_mode` 覆盖参数
- **AND** 后续 Skill SHALL 使用该集合执行站点数据查询
- **AND** 后续 Skill SHALL NOT 使用原始未裁剪的区域站点集合执行查询

#### Scenario: 时间截断与站点覆盖同时存在

- **WHEN** 站点查询同时产生合法时间范围和可执行站点集合
- **THEN** `permission_query_overrides` SHALL 同时包含 `time_span`、站点集合和已允许的行政区集合
- **AND** 后续 Skill SHALL 同时应用时间覆盖和站点覆盖

### Requirement: 站点归属缺失结果表达

`PermissionResult` SHALL 能表达站点归属行政区缺失或无效导致的数据不可用状态。

#### Scenario: 单站点归属缺失

- **WHEN** 用户查询具体站点且该站点缺少有效行政区归属
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 为 `reject`
- **AND** `PermissionResult.correction_text` SHALL 包含“该数据暂不可用”

#### Scenario: 部分站点归属缺失

- **WHEN** 多站点查询中部分站点缺少有效行政区归属
- **THEN** 系统 SHALL 从可执行站点集合中排除这些站点
- **AND** `PermissionResult.debug_context` SHALL 记录被排除站点的数量或名称摘要
