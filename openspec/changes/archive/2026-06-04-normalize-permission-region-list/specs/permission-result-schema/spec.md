## ADDED Requirements

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

## MODIFIED Requirements

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
