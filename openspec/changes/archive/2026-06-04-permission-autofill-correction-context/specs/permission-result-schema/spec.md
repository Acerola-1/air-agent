## ADDED Requirements

### Requirement: PermissionResult distinguishes auto-fill from replacement
The system SHALL represent automatic default-region completion separately from over-permission region replacement.

#### Scenario: Auto-filled default region
- **WHEN** the user did not specify a region and the system uses `bound_region`
- **THEN** `PermissionResult.auto_filled` SHALL be `true`
- **AND** `PermissionResult.fix_strategy` SHALL NOT be `replace_region` solely because of the auto-fill
- **AND** `PermissionResult.correction_text` SHALL describe the auto-filled query region

#### Scenario: Over-permission replacement
- **WHEN** the user specified a region outside their permission scope and the system replaces it
- **THEN** `PermissionResult.fix_strategy` SHALL be `replace_region`
- **AND** `PermissionResult.auto_filled` SHALL be `false`

### Requirement: PermissionResult exposes diagnostics for main flow
The system SHALL expose rule matching and debug information in `PermissionResult` so the main flow can explain or troubleshoot permission corrections without rerunning permission checks.

#### Scenario: Rule engine returns diagnostics
- **WHEN** permission check completes
- **THEN** `PermissionResult.matched_rules` SHALL contain the matched deterministic rule identifiers
- **AND** `PermissionResult.debug_context` SHALL contain bounded structured context for troubleshooting when available

### Requirement: PermissionResult Schema Definition
The `PermissionResult` schema SHALL include fields required to represent permission decisions, corrections, automatic default-region completion, and diagnostics.

#### Scenario: Schema contains correction and auto-fill fields
- **WHEN** a `PermissionResult` is serialized
- **THEN** it SHALL include `permitted`, `exemption`, `fix_strategy`, `correction_text`, `original_time_span`, `time_granularity`, `rule_version`, `matched_rules`, and `auto_filled`
- **AND** it SHALL include `allowed_region` when an executable region override is needed

#### Scenario: allowed_region used for executable region override
- **WHEN** permission check produces a region that downstream query execution must use
- **THEN** `allowed_region` SHALL contain `name`, `level`, and `data_type`
- **AND** `allowed_region` MAY represent either an auto-filled default region or an over-permission replacement region
- **AND** callers SHALL use `auto_filled` and `fix_strategy` to distinguish those two cases
