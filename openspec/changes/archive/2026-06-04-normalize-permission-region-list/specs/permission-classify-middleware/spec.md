## MODIFIED Requirements

### Requirement: 确定性上下文预取

当 `permission_need` 为 `need_check` 或 `uncertain` 且存在 `user_id` 时，中间件 SHALL 在运行确定性规则引擎前预取确定性上下文，并使用 slots 中的统一区域请求列表逐项解析行政区。

#### Scenario: 预取上下文成功且用户指定单个行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "single"` 且 `regions` 包含一个有效区域项
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 使用该区域项的 `text`、`level_hint`、`city_hint` 和 `province_hint` 调用 MCP `resolve_region_scope`
- **AND** 中间件 SHALL 将 `user_profile`、解析后的单个 `requested_region`、slots 和当前时间传入确定性规则引擎

#### Scenario: 预取上下文成功且用户指定多个行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "multi_explicit"` 且 `regions` 包含多个区域项
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 对 `regions` 中每个区域项分别调用 MCP `resolve_region_scope`
- **AND** 中间件 SHALL NOT 只解析第一个区域项
- **AND** 中间件 SHALL 为每个解析后的标准区域分别构造权限事实

#### Scenario: 预取上下文成功且用户未指定行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "auto_fill"` 且 `regions` 为空
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL NOT 调用 `resolve_region_scope`
- **AND** 中间件 SHALL 使用 `user_profile.bound_region` 构造来源为 `auto_fill` 的区域项
- **AND** 中间件 SHALL 将自动补全区域、slots 和当前时间传入确定性规则引擎或等价权限事实构造流程

#### Scenario: 部分预取失败

- **WHEN** MCP 工具缺失、调用失败或返回无法解析
- **THEN** 中间件 SHALL 记录日志
- **AND** 中间件 SHALL 将失败的区域项写入逐项权限结果
- **AND** 中间件 SHALL 对仍可解析的区域项继续执行权限校验
- **AND** 中间件 SHALL 按 fail-closed 配置或权限事实不足策略处理全部失败场景

### Requirement: 权限结果写入与提示词注入

中间件 SHALL 从确定性规则引擎返回值中提取 `PermissionResult`，并把聚合后的 `PermissionResult` 序列化写入 `state["permission_result"]`。当结果包含可执行区域、可执行时间截断或自动补全区域时，中间件 SHALL 额外写入 `state["permission_query_overrides"]`，供后续 skill 查找、参数抽取和工具调用使用。

#### Scenario: 结构化结果存在

- **WHEN** 规则引擎返回 `PermissionResult` 或等价 dict
- **THEN** 中间件 SHALL 写入 `state["permission_result"]`
- **AND** 主模型调用前 SHALL 注入 `<permission_context>` 到 system prompt

#### Scenario: 自动补全区域继续查询

- **WHEN** 用户未指定行政区且中间件使用 `bound_region` 自动补全区域
- **THEN** 中间件 SHALL 写入 `state["permission_result"].auto_filled = true`
- **AND** 中间件 SHALL 写入 `state["permission_result"].region_results`
- **AND** 中间件 SHALL 写入 `state["permission_result"].correction_text`
- **AND** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.regions` 作为查询行政区列表

#### Scenario: 多区域逐项权限校验继续查询

- **WHEN** slots 中 `regions` 包含多个区域项且至少一个区域项权限校验后可查询
- **THEN** 中间件 SHALL 写入 `state["permission_result"].region_results`
- **AND** 每个 `region_results` 项 SHALL 包含请求区域、解析区域、权限状态和实际查询区域
- **AND** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行允许区域的数据查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL NOT 只使用第一个区域

#### Scenario: 行政区修正继续查询

- **WHEN** 任一区域项规则引擎返回 `permitted = false` 且 `fix_strategy = "replace_region"`
- **THEN** 中间件 SHALL 在对应 `region_results` 项中记录原请求区域和替换后的查询区域
- **AND** 中间件 SHALL 将替换后的查询区域写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.regions` 替代原问题中的越权行政区

#### Scenario: 时间截断继续查询

- **WHEN** 任一区域项规则引擎返回 `permitted = false` 且 `fix_strategy = "truncate_time"`
- **THEN** 中间件 SHALL 在对应 `region_results` 项中记录合法时间范围
- **AND** 若所有可查询区域的合法时间范围一致，中间件 SHALL 写入 `state["permission_query_overrides"].time_span`
- **AND** 若合法时间范围不一致，中间件 SHALL 在逐项结果中保留各自合法时间范围，不得静默扩大为全局时间范围

#### Scenario: 硬拒绝短路

- **WHEN** 所有区域项均无法解析、无权访问或无可用修正策略
- **THEN** 中间件 SHALL 写入整体拒绝的 `permission_result`
- **AND** 中间件 SHALL NOT 写入可执行的 `permission_query_overrides.regions`
- **AND** 主模型 SHALL NOT 继续执行 skill 查找或工具调用

#### Scenario: 无需权限上下文

- **WHEN** `permission_need = "no_check"` 或不存在 `permission_result`
- **THEN** 中间件 SHALL NOT 注入 `<permission_context>`

#### Scenario: 权限上下文包含审查细节

- **WHEN** 中间件注入 `<permission_context>`
- **THEN** `<permission_context>` SHALL 包含完整 `permission_result`
- **AND** `<permission_context>` SHALL 包含 `permission_result.region_results`
- **AND** `<permission_context>` SHALL 包含 `permission_result.matched_rules`
- **AND** `<permission_context>` SHALL 包含 `permission_result.debug_context`
- **AND** `<permission_context>` SHALL 包含 `permission_query_overrides`（如果存在）
- **AND** `<permission_context>` SHALL 明确指示主流程在 `permission_query_overrides.regions` 存在时必须使用完整区域列表
