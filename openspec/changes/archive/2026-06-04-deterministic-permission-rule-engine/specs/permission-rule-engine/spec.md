# Permission Rule Engine Spec

## Purpose

提供确定性权限规则引擎，替代 LLM Agent 直接做权限判定。规则引擎接收 `PermissionFacts`，输出唯一的 `PermissionResult`，负责时间豁免、基础行政区权限、修正策略和拒绝策略。

## ADDED Requirements

### Requirement: 规则引擎必须是 PermissionResult 的唯一生产者

系统 MUST 由代码规则引擎生成权限判定结果，LLM 不得直接生成 `PermissionResult`。

#### Scenario: LLM 返回 slots 后由规则引擎判定

**WHEN** LLM 抽取出 `time_granularity`、`original_time_span`、`region_text`
**AND** 中间件已获取用户画像和行政区解析结果
**THEN** 系统必须构造 `PermissionFacts`
**AND** 调用 `check_permission(facts)`
**AND** 使用规则引擎返回的 `PermissionResult` 写入 state

#### Scenario: LLM 输出权限字段被忽略

**WHEN** LLM 输出中包含 `permitted` 或 `fix_strategy`
**THEN** 系统不得使用这些字段作为权限结果
**AND** 必须仅使用 slots 字段构造 facts

### Requirement: 规则执行顺序必须固定

规则引擎 MUST 按固定顺序执行权限判定，不允许调用方或 LLM 改变优先级。

#### Scenario: 部分时间豁免优先于行政区修正

**WHEN** 用户请求时间范围与豁免窗口存在部分交集
**AND** 请求行政区超出基础行政区权限
**THEN** 规则引擎必须返回 `fix_strategy=truncate_time`
**AND** 不得返回 `replace_region`

#### Scenario: 无时间豁免时执行行政区权限

**WHEN** 用户请求时间范围与豁免窗口无交集
**THEN** 规则引擎必须执行基础行政区权限判断
**AND** 根据行政区矩阵放行、替换或拒绝

### Requirement: 时间豁免窗口必须由代码计算

豁免窗口 MUST 由确定性函数根据北京时间和时间粒度计算。

#### Scenario: 最近七天完全豁免

**WHEN** `time_granularity` 为 `daily`
**AND** 原始时间范围完全落在最近七天窗口内
**THEN** 规则引擎必须返回 `permitted=true`
**AND** `exemption=full_window`
**AND** 不得检查行政区越权

#### Scenario: 近两月完全豁免

**WHEN** `time_granularity` 为 `month`
**AND** 原始月份位于当前月或上一自然月
**THEN** 规则引擎必须返回 `permitted=true`
**AND** `exemption=full_window`

#### Scenario: 近两年完全豁免

**WHEN** `time_granularity` 为 `year`
**AND** 原始年份为当前年或上一自然年
**THEN** 规则引擎必须返回 `permitted=true`
**AND** `exemption=full_window`

### Requirement: 行政区权限必须复用确定性行政区上下文

基础行政区权限 MUST 使用用户画像、请求行政区和 `build_permission_region_context()` 的确定性结果。

#### Scenario: 行政区权限通过

**WHEN** `region_allowed_without_time_exemption=true`
**THEN** 规则引擎必须返回 `permitted=true`
**AND** `exemption=normal`
**AND** `fix_strategy` 为空字符串

#### Scenario: 行政区权限不通过但存在替代行政区

**WHEN** `region_allowed_without_time_exemption=false`
**AND** `replacement_region` 不为空
**THEN** 规则引擎必须返回 `permitted=false`
**AND** `fix_strategy=replace_region`
**AND** `allowed_region` 等于替代行政区

### Requirement: 排除项不得享受时间豁免

命中适用范围排除项时，规则引擎 MUST 跳过时间豁免，回退基础行政区权限。

#### Scenario: 历史补查不享受豁免

**WHEN** 查询时间范围命中历史补查排除项
**THEN** 规则引擎不得返回 `exemption=full_window`
**AND** 必须继续执行基础行政区权限判断

### Requirement: 规则结果必须可解释

每个 `PermissionResult` MUST 包含足够解释信息。

#### Scenario: 返回修正结果

**WHEN** 规则引擎返回 `truncate_time` 或 `replace_region`
**THEN** `reason` 必须说明修正原因
**AND** `correction_text` 必须为用户可读文案
**AND** `rule_version` 必须等于当前规则版本
