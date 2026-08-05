# Permission Facts Schema Spec

## Purpose

定义权限规则引擎输入事实模型，明确 LLM 抽取结果、MCP 可信上下文和原始问题之间的边界。

## ADDED Requirements

### Requirement: 系统必须定义 PermissionExtractedSlots

系统 MUST 定义用于 LLM 结构化抽取的 slots 模型。

#### Scenario: slots 只包含语义事实

**WHEN** LLM 完成权限前置抽取
**THEN** 输出必须符合 `PermissionExtractedSlots`
**AND** 只能包含行政区文本、目标层级、时间粒度、时间范围、数据类型和指标名称

#### Scenario: slots 不包含权限结果

**WHEN** LLM 输出包含权限判定字段
**THEN** 系统必须忽略这些字段
**AND** 不得将其写入 `PermissionResult`

### Requirement: 系统必须定义 PermissionFacts

系统 MUST 定义 `PermissionFacts` 作为 `check_permission()` 唯一输入。

#### Scenario: 构造完整事实

**WHEN** 中间件已获得用户问题、用户 ID、北京时间、用户画像、请求行政区和 slots
**THEN** 系统必须构造 `PermissionFacts`
**AND** 将其传递给 `check_permission()`

#### Scenario: 行政区缺失

**WHEN** slots 未抽取到 `region_text`
**THEN** `PermissionFacts.requested_region` 可以为空
**AND** 规则引擎必须按安全默认策略处理

### Requirement: facts 不得包含最终判定

`PermissionFacts` MUST NOT 包含 `permitted`、`fix_strategy`、`allowed_region`、`legal_time_span` 等最终判定字段。

#### Scenario: 外部输入污染 facts

**WHEN** 构造 facts 的原始输入中包含最终判定字段
**THEN** 系统必须忽略这些字段
**AND** 最终结果必须仍由规则引擎计算
