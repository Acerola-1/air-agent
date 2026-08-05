# PermissionClassifyMiddleware Deterministic Engine Spec

## Purpose

改造权限中间件，使其从“调用权限审查 Agent 返回 PermissionResult”变为“调用 slots 抽取器 + MCP 可信上下文 + 确定性规则引擎”。

## MODIFIED Requirements

### Requirement: 中间件必须保留规则预分类

中间件 MUST 继续使用 `classify_permission_need()` 判断是否需要权限审查。

#### Scenario: 知识问答不触发权限审查

**WHEN** 用户问题为知识问答或闲聊
**THEN** `permission_need` 必须为 `no_check`
**AND** 中间件不得调用 slots 抽取器
**AND** 不得调用权限规则引擎

#### Scenario: 数据查询触发权限审查

**WHEN** 用户问题包含数据查询意图并涉及时间或行政区
**THEN** `permission_need` 必须为 `need_check` 或 `uncertain`
**AND** 中间件必须进入权限审查流程

### Requirement: 中间件必须主动调用 MCP 工具

中间件 MUST 主动调用 `get_user_profile` 和 `resolve_region_scope` 获取可信事实，而不是让权限判定 Agent 自主调用。

#### Scenario: 获取用户画像

**WHEN** `permission_need` 不为 `no_check`
**AND** `user_id` 存在
**THEN** 中间件必须调用 `get_user_profile(user_id)`
**AND** 将返回结果用于构造 `PermissionFacts.user_profile`

#### Scenario: 解析请求行政区

**WHEN** slots 中存在 `region_text`
**THEN** 中间件必须调用 `resolve_region_scope`
**AND** `query` 必须优先使用 `region_text`
**AND** `contextCityCode` 必须来自用户画像中的 city code
**AND** `contextProvinceCode` 必须来自用户画像中的 province code

### Requirement: 中间件必须调用规则引擎

中间件 MUST 将抽取结果和 MCP 结果构造成 `PermissionFacts`，再调用规则引擎。

#### Scenario: 权限规则引擎返回修正结果

**WHEN** `check_permission(facts)` 返回 `fix_strategy=truncate_time`
**THEN** 中间件必须将 `legal_time_span` 写入 `permission_query_overrides.time_span`

#### Scenario: 权限规则引擎返回行政区替换

**WHEN** `check_permission(facts)` 返回 `fix_strategy=replace_region`
**THEN** 中间件必须将 `allowed_region` 写入 `permission_query_overrides.region`

### Requirement: 主模型上下文协议必须保持兼容

中间件注入主模型的 `<permission_context>` 协议 MUST 保持兼容，避免影响后续 skill 查找和工具调用。

#### Scenario: 存在权限修正

**WHEN** `permission_query_overrides` 存在
**THEN** `<permission_context>` 必须包含该对象
**AND** 系统提示必须要求后续查询参数以该对象为准

#### Scenario: 硬拒绝

**WHEN** `PermissionResult.permitted=false`
**AND** `fix_strategy` 为空或为 `reject`
**THEN** 中间件必须短路主模型调用
**AND** 直接返回 `correction_text` 或 `reason`
