## MODIFIED Requirements

### Requirement: 确定性上下文预取

当 `permission_need` 为 `need_check` 或 `uncertain` 且存在 `user_id` 时，中间件 SHALL 在运行确定性规则引擎前预取确定性上下文。

#### Scenario: 预取上下文成功且用户指定行政区

- **WHEN** MCP 工具可用且 slots 中存在有效行政区字段
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 使用 slots 中最细粒度行政区调用 MCP `resolve_region_scope(query, city, province, target_level)`
- **AND** 中间件 SHALL 将 `user_profile`、`requested_region`、slots 和当前时间传入确定性规则引擎

#### Scenario: 预取上下文成功且用户未指定行政区

- **WHEN** MCP 工具可用且 slots 中 `province`、`city`、`district` 均为空
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL NOT 调用 `resolve_region_scope`
- **AND** 中间件 SHALL 使用 `user_profile.bound_region` 构造自动补全查询区域
- **AND** 中间件 SHALL 将自动补全区域、slots 和当前时间传入确定性规则引擎或等价权限事实构造流程

#### Scenario: 部分预取失败

- **WHEN** MCP 工具缺失、调用失败或返回无法解析
- **THEN** 中间件 SHALL 记录日志
- **AND** 中间件 SHALL 将缺失字段以 `null` 写入预取上下文
- **AND** 中间件 SHALL 按 fail-closed 配置或权限事实不足策略处理

### Requirement: 权限结果写入与提示词注入

中间件 SHALL 从确定性规则引擎返回值中提取 `PermissionResult`，并把 `PermissionResult` 序列化后写入 `state["permission_result"]`。当结果包含可执行修正策略或自动补全区域时，中间件 SHALL 额外写入 `state["permission_query_overrides"]`，供后续 skill 查找、参数抽取和工具调用使用。

#### Scenario: 结构化结果存在

- **WHEN** 规则引擎返回 `PermissionResult` 或等价 dict
- **THEN** 中间件 SHALL 写入 `state["permission_result"]`
- **AND** 主模型调用前 SHALL 注入 `<permission_context>` 到 system prompt

#### Scenario: 自动补全区域继续查询

- **WHEN** 用户未指定行政区且中间件使用 `bound_region` 自动补全区域
- **THEN** 中间件 SHALL 写入 `state["permission_result"].auto_filled = true`
- **AND** 中间件 SHALL 写入 `state["permission_result"].correction_text`
- **AND** 中间件 SHALL 写入 `state["permission_query_overrides"].region`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.region` 作为查询行政区

#### Scenario: 行政区修正继续查询

- **WHEN** 规则引擎返回 `permitted = false` 且 `fix_strategy = "replace_region"`
- **THEN** 中间件 SHALL 写入 `state["permission_query_overrides"].region`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.region` 替代原问题中的越权行政区

#### Scenario: 时间截断继续查询

- **WHEN** 规则引擎返回 `permitted = false` 且 `fix_strategy = "truncate_time"`
- **THEN** 中间件 SHALL 写入 `state["permission_query_overrides"].time_span`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.time_span` 替代原问题中的越权时间范围

#### Scenario: 硬拒绝短路

- **WHEN** 规则引擎返回 `permitted = false` 且 `fix_strategy = "reject"` 或无可用修正策略
- **THEN** 中间件 SHALL 直接返回拒绝答复
- **AND** 主模型 SHALL NOT 继续执行 skill 查找或工具调用

#### Scenario: 无需权限上下文

- **WHEN** `permission_need = "no_check"` 或不存在 `permission_result`
- **THEN** 中间件 SHALL NOT 注入 `<permission_context>`

#### Scenario: 权限上下文包含审查细节

- **WHEN** 中间件注入 `<permission_context>`
- **THEN** `<permission_context>` SHALL 包含完整 `permission_result`
- **AND** `<permission_context>` SHALL 包含 `permission_result.matched_rules`
- **AND** `<permission_context>` SHALL 包含 `permission_result.debug_context`
- **AND** `<permission_context>` SHALL 包含 `permission_query_overrides`（如果存在）
- **AND** `<permission_context>` SHALL 明确指示主流程在 `permission_query_overrides` 存在时必须使用覆盖参数
