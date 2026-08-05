## ADDED Requirements

### Requirement: PermissionResult 表达站点权限裁剪结果
`PermissionResult` SHALL 表达站点权限审查后的可执行站点集合、候选站点总数和可访问站点数量，以便主流程和业务 Skill 使用裁剪后的站点范围执行查询。

#### Scenario: 多站点部分可访问
- **WHEN** 站点权限审查发现候选站点中仅部分站点可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 表示站点范围裁剪
- **AND** `PermissionResult.accessible_station_count` SHALL 等于可访问站点数量
- **AND** `PermissionResult` SHALL 包含可执行站点集合
- **AND** `PermissionResult.correction_text` SHALL 包含“仅展示您可访问的”站点数据提示

#### Scenario: 全部站点可访问
- **WHEN** 站点权限审查发现所有候选站点均可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `true`
- **AND** `PermissionResult.exemption` SHALL 为 `normal` 或 `full_window`
- **AND** `PermissionResult` MAY 包含原始候选站点集合用于后续查询覆盖

#### Scenario: 全部站点不可访问
- **WHEN** 站点权限审查发现所有候选站点均不可访问
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 为 `reject`
- **AND** `PermissionResult.correction_text` SHALL 告知用户无权访问该站点数据或该数据暂不可用

### Requirement: permission_query_overrides 支持站点覆盖
系统 SHALL 从 `PermissionResult` 提取站点覆盖参数，并通过 `permission_query_overrides` 传递给后续查询流程。

#### Scenario: 站点裁剪覆盖参数
- **WHEN** `PermissionResult` 包含可执行站点集合
- **THEN** `permission_query_overrides` SHALL 包含该站点集合
- **AND** `permission_query_overrides` SHALL 保留既有 `regions`、`region`、`region_mode` 覆盖参数
- **AND** 后续 Skill SHALL 使用该集合执行站点数据查询
- **AND** 后续 Skill SHALL NOT 使用原始未裁剪的区域站点集合执行查询

#### Scenario: 时间截断与站点覆盖同时存在
- **WHEN** 站点查询同时产生合法时间范围和可执行站点集合
- **THEN** `permission_query_overrides` SHALL 同时包含 `time_span`、站点集合和已允许的行政区集合
- **AND** 后续 Skill SHALL 同时应用时间覆盖和站点覆盖

### Requirement: 站点归属缺失结果表达
`PermissionResult` SHALL 能表达站点归属行政区缺失或无效导致的数据不可用状态。

#### Scenario: 单站点归属缺失
- **WHEN** 用户查询具体站点且该站点缺少有效行政区归属
- **THEN** `PermissionResult.permitted` SHALL 为 `false`
- **AND** `PermissionResult.fix_strategy` SHALL 为 `reject`
- **AND** `PermissionResult.correction_text` SHALL 包含“该数据暂不可用”

#### Scenario: 部分站点归属缺失
- **WHEN** 多站点查询中部分站点缺少有效行政区归属
- **THEN** 系统 SHALL 从可执行站点集合中排除这些站点
- **AND** `PermissionResult.debug_context` SHALL 记录被排除站点的数量或名称摘要
