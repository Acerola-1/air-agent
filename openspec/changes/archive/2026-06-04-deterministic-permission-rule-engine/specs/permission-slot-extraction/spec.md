# Permission Slot Extraction Spec

## Purpose

约束 LLM 在新权限方案中的职责：只做自然语言信息抽取，不做权限判定、时间豁免计算或行政区修正。

## ADDED Requirements

### Requirement: LLM 只能抽取权限事实 slots

权限前置 LLM 调用 MUST 只返回 `PermissionExtractedSlots`。

#### Scenario: 抽取时间和行政区

**WHEN** 用户问题为“近30天浙江省PM2.5”
**THEN** LLM 应抽取 `region_text=浙江省`
**AND** `time_granularity=other`
**AND** `original_time_span` 为归一化日期范围
**AND** 不得输出是否允许访问

### Requirement: LLM 不得计算豁免窗口

豁免窗口 MUST 由代码根据北京时间计算。

#### Scenario: 用户查询昨天数据

**WHEN** LLM 识别出查询时间为昨天
**THEN** LLM 只需输出原始时间范围
**AND** 不得输出 `exemption_window`

### Requirement: LLM 不得生成修正策略

修正策略 MUST 由规则引擎根据事实计算。

#### Scenario: 用户查询越权行政区

**WHEN** LLM 抽取到请求行政区
**THEN** LLM 不得输出 `replace_region`
**AND** 不得输出 `allowed_region`
