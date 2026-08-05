## ADDED Requirements

### Requirement: Skip resolve_region_scope when no region is specified
When `PermissionExtractedSlots` has no `province`, `city`, or `district` values, the system SHALL skip the `resolve_region_scope` MCP call and proceed with the user's default region.

#### Scenario: Query without region information
- **WHEN** user asks "查一下昨天的空气质量" with no province, city, or district in slots
- **THEN** `_resolve_requested_region` returns `None` without calling `resolve_region_scope`
- **AND** the rule engine uses `default_allowed_region(user_profile)` for region resolution
- **AND** the query proceeds with the user's bound region as default

### Requirement: Auto-filled region generates correction text
When the system automatically fills the region from the user's bound_region, it SHALL generate a correction text informing the user.

#### Scenario: Auto-filled region with correction
- **WHEN** user asks a data query without specifying a region
- **AND** the system auto-fills with the user's bound_region
- **THEN** `auto_filled` is set to `true` in the permission result
- **AND** `correction_text` contains a message like "根据您的关联城市，已自动为您补全查询区域..."

### Requirement: Expose full permission context to main flow
The system SHALL expose `matched_rules`, `debug_context`, and `auto_filled` fields from `PermissionResult` to the main agent flow through the permission context.

#### Scenario: Main flow receives detailed permission context
- **WHEN** permission check completes with any result (permitted or rejected)
- **THEN** the injected `permission_context` includes `matched_rules` and `debug_context`
- **AND** the main flow can access which rules were matched and why

## MODIFIED Requirements

### Requirement: Permission classify middleware handles empty region slots
The `PermissionClassifyMiddleware` SHALL handle the case where all region slots are null by using the user's default region instead of attempting region resolution.

#### Scenario: Empty region slots with bound_region
- **WHEN** `PermissionExtractedSlots` has `province=null`, `city=null`, `district=null`
- **AND** user profile has a valid `bound_region`
- **THEN** the system proceeds with the bound region without calling `resolve_region_scope`
- **AND** marks the result as `auto_filled=true`

#### Scenario: Empty region slots without bound_region
- **WHEN** `PermissionExtractedSlots` has `province=null`, `city=null`, `district=null`
- **AND** user profile has no `bound_region`
- **THEN** the system proceeds with `default_allowed_region(user_profile)`
- **AND** does not generate a correction text
