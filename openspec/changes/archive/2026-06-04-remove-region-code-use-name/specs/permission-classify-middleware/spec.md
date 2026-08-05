## MODIFIED Requirements

### Requirement: 确定性上下文预取

中间件 SHALL 在调用权限审查 agent 前预取确定性上下文。

#### Scenario: 预取上下文成功

- **WHEN** MCP 工具可用且行政区解析成功
- **THEN** 中间件 SHALL 直接调用本地 `get_beijing_time`
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 调用 MCP `resolve_region_scope(query, targetLevel, city, province)`，其中 `city` 和 `province` 为中文名
- **AND** 中间件 SHALL 调用 `build_permission_region_context(user_profile, requested_region)` 生成行政区权限上下文

#### Scenario: 部分预取失败

- **WHEN** MCP 工具缺失、调用失败或返回无法解析
- **THEN** 中间件 SHALL 记录日志
- **AND** 中间件 SHALL 将缺失字段以 `null` 写入预取上下文
- **AND** 中间件 SHALL 继续由权限审查 agent 按提示词处理

### Requirement: 权限审查 agent 任务描述

中间件 SHALL 将预取结果序列化为 JSON，并放入权限审查 agent 的 human message 的 `<prefetched_context>` 块中。

#### Scenario: 注入预取上下文

- **WHEN** 中间件调用权限审查 agent
- **THEN** human message SHALL 包含用户原始问题和 `userId`
- **AND** human message SHALL 包含 `<prefetched_context>` 块
- **AND** `<prefetched_context>` SHALL 包含 `beijing_time`、`user_profile`、`requested_region` 和 `permission_region_context`
- **AND** `user_profile` 和 `requested_region` SHALL 不包含 `code` 字段

## REMOVED Requirements

### Requirement: 基于 code 的行政区比较

**Reason**: `code` 字段已删除，所有比较改为基于 `name`
**Migration**: 使用 `name` 替代 `code` 进行同省/同市判断

### Requirement: context_city_code / context_province_code 参数

**Reason**: 消歧参数改为中文名 `city` / `province`
**Migration**: 调用 `resolve_region_scope` 时传入 `city` 和 `province` 中文名替代
