## MODIFIED Requirements

### Requirement: 确定性上下文预取

当 `permission_need` 为 `need_check` 或 `uncertain` 且存在 `user_id` 时，中间件 SHALL 在调用权限审查 agent 前预取确定性上下文。

#### Scenario: 预取上下文成功

- **WHEN** MCP 工具可用且行政区解析成功
- **THEN** 中间件 SHALL 直接调用本地 `get_beijing_time`
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 仅在 `PermissionExtractedSlots` 包含 `province`、`city` 或 `district` 时调用 MCP `resolve_region_scope(query, city, province, targetLevel)`
- **AND** 当 `PermissionExtractedSlots` 中所有区域字段均为 null 时，中间件 SHALL 跳过 `resolve_region_scope` 调用
- **AND** 中间件 SHALL 调用 `build_permission_region_context(user_profile, requested_region)` 生成行政区权限上下文

#### Scenario: 无区域信息时使用默认区域

- **WHEN** `PermissionExtractedSlots` 中 `province`、`city`、`district` 均为 null
- **THEN** 中间件 SHALL 跳过 `resolve_region_scope` MCP 调用
- **AND** `requested_region` SHALL 为 `None`
- **AND** 规则引擎 SHALL 使用 `default_allowed_region(user_profile)` 作为默认查询区域

## ADDED Requirements

### Requirement: 权限上下文增强暴露

中间件 SHALL 将 `PermissionResult` 中的 `matched_rules`、`debug_context` 和 `auto_filled` 字段注入到主模型的 `permission_context` 中。

#### Scenario: 主流程接收完整权限上下文

- **WHEN** 权限审查完成并生成 `PermissionResult`
- **THEN** 注入主模型的 `<permission_context>` SHALL 包含 `matched_rules` 列表
- **AND** `<permission_context>` SHALL 包含 `debug_context` 对象（如存在）
- **AND** `<permission_context>` SHALL 包含 `auto_filled` 布尔值
- **AND** 主模型 SHALL 能根据这些信息理解权限审查的详细结果
