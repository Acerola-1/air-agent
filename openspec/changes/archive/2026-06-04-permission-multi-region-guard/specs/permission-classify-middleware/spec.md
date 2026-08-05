## ADDED Requirements

### Requirement: 中间件区域请求规划

权限预分类与审查中间件 SHALL 在行政区解析前，根据原始用户问题和 slots 结果生成区域请求计划，表达单区域、多区域、集合区域或无区域自动补全形态。

#### Scenario: 多行政区问题生成多区域计划

- **WHEN** 最新用户消息包含多个并列请求行政区，例如 `2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "multi_explicit"` 的区域请求计划
- **AND** 区域请求计划 SHALL 包含 `郑州市` 和 `平顶山市` 两个请求项
- **AND** 中间件 SHALL 分别调用行政区解析能力解析每个请求项

#### Scenario: 集合行政区问题生成展开计划

- **WHEN** 最新用户消息包含下辖区县或各区县集合语义，例如 `2026年3月郑州市下的区县的PM2.5月均值分别是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "collection"` 的区域请求计划
- **AND** 区域请求计划 SHALL 包含父级行政区 `郑州市`
- **AND** 区域请求计划 SHALL 包含目标下级层级 `district`
- **AND** 中间件 SHALL 调用下级行政区展开能力获取区域列表

#### Scenario: 单行政区问题保持单区域计划

- **WHEN** 最新用户消息只包含一个明确行政区，例如 `2026年3月郑州市PM2.5月均值是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "single"` 的区域请求计划
- **AND** 中间件 SHALL 继续执行既有单区域权限上下文预取流程

#### Scenario: 无行政区问题保持自动补全计划

- **WHEN** 最新用户消息未指定行政区，例如 `昨天空气质量如何？`
- **THEN** 中间件 SHALL 生成 `mode = "auto_fill"` 的区域请求计划
- **AND** 中间件 SHALL 继续执行既有用户默认关联区域自动补全流程

### Requirement: 中间件批量权限校验

权限预分类与审查中间件 SHALL 对区域请求计划中的每个实际区域项独立构造权限事实并执行确定性权限校验。

#### Scenario: 显式多区域逐项校验

- **WHEN** 区域请求计划包含 `郑州市` 和 `平顶山市`
- **THEN** 中间件 SHALL 为 `郑州市` 构造一份 `PermissionFacts` 并调用规则引擎
- **AND** 中间件 SHALL 为 `平顶山市` 构造一份 `PermissionFacts` 并调用规则引擎
- **AND** 中间件 SHALL NOT 只校验其中一个城市

#### Scenario: 展开后的区县逐项校验

- **WHEN** 区域请求计划展开出多个下辖区县
- **THEN** 中间件 SHALL 为每个区县分别构造 `PermissionFacts`
- **AND** 中间件 SHALL 为每个区县分别调用规则引擎
- **AND** 中间件 SHALL NOT 使用父级城市权限结果替代区县逐项权限结果

### Requirement: 中间件聚合权限上下文

权限预分类与审查中间件 SHALL 将逐项权限结果聚合为完整 `state["permission_result"]` 和 `state["permission_query_overrides"]`，供主流程继续查询。

#### Scenario: 多区域覆盖参数写入

- **WHEN** 多个区域项存在一个或多个允许查询区域
- **THEN** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** `state["permission_query_overrides"].regions` SHALL 包含实际允许查询的区域列表
- **AND** 中间件 SHALL 写入 `state["permission_result"].allowed_regions`
- **AND** 主流程 SHALL 使用 `regions` 而不是只使用单个 `region`

#### Scenario: 聚合结果包含订正细节

- **WHEN** 一个或多个区域项发生行政区替换、时间截断或拒绝
- **THEN** 中间件 SHALL 将逐项订正写入 `state["permission_result"].region_corrections`
- **AND** 中间件 SHALL 将无法查询的区域写入 `state["permission_result"].rejected_regions`
- **AND** 中间件 SHALL 在 `state["permission_result"].correction_text` 中汇总说明自动拆分、展开和权限订正

#### Scenario: 全部区域不可查询时硬拒绝

- **WHEN** 聚合后不存在任何允许查询区域
- **THEN** 中间件 SHALL 写入硬拒绝权限结果
- **AND** 中间件 SHALL NOT 写入可执行的 `permission_query_overrides.regions`
- **AND** 主流程 SHALL NOT 继续执行数据查询

### Requirement: 中间件向主流程注入多区域指令

权限预分类与审查中间件 SHALL 在 `<permission_context>` 中注入多区域权限上下文，并明确要求后续流程使用批量区域覆盖参数。

#### Scenario: 多区域权限上下文注入

- **WHEN** `state["permission_query_overrides"].regions` 存在
- **THEN** `<permission_context>` SHALL 包含 `permission_query_overrides.regions`
- **AND** `<permission_context>` SHALL 包含 `permission_result.region_mode`
- **AND** `<permission_context>` SHALL 明确指示主流程必须使用 `regions` 作为查询行政区列表
- **AND** `<permission_context>` SHALL 明确指示主流程不得只取第一个区域
