## ADDED Requirements

### Requirement: 权限上下文保留细分修正文案
权限预分类与审查中间件 SHALL 将规则引擎生成的细分 `correction_text` 原样写入权限上下文，并要求后续主流程在最终回复中体现该文案。

#### Scenario: 注入行政区矩阵文案
- **WHEN** 规则引擎返回包含行政区矩阵化说明的 `PermissionResult.correction_text`
- **THEN** 中间件 SHALL 将该文案写入 `<permission_context>`
- **AND** 中间件 SHALL NOT 将其替换为通用“因权限限制已调整查询区域”文案
- **AND** 主模型系统提示 SHALL 要求最终回复包含该修正说明

#### Scenario: 注入时间截断文案
- **WHEN** 规则引擎返回 `fix_strategy = "truncate_time"` 且 `correction_text` 包含合法时间范围
- **THEN** 中间件 SHALL 将 `permission_query_overrides.time_span` 和该 `correction_text` 一并注入权限上下文
- **AND** 主流程 SHALL 使用截断后的时间范围继续查询

#### Scenario: 硬拒绝返回细分文案
- **WHEN** 权限结果为硬拒绝且 `PermissionResult.correction_text` 非空
- **THEN** 中间件的拒绝回复 SHALL 使用该 `correction_text`
- **AND** 中间件 SHALL NOT 回退到默认“因数据权限限制，您无权访问该数据。”

### Requirement: 多区域聚合保留逐项修正文案
当多区域或集合区域查询产生多个逐项权限结果时，中间件 SHALL 保留每个区域项的细分修正文案，并在聚合文案中包含必要的逐项说明。

#### Scenario: 多区域部分替换
- **WHEN** 多区域查询中某个区域项发生行政区替换
- **THEN** 对应 `region_results` 项 SHALL 保留该区域项的细分 `correction_text`
- **AND** 聚合后的 `PermissionResult.correction_text` SHALL 包含该替换说明
- **AND** 聚合文案 SHALL NOT 只保留“部分区域未查询”这类泛化说明

#### Scenario: 集合区域部分拒绝
- **WHEN** 集合区域展开后的部分子区域无法查询
- **THEN** 对应 `region_results` 项 SHALL 保留拒绝或修正文案
- **AND** 允许区域 SHALL 继续写入 `permission_query_overrides.regions`
- **AND** 聚合文案 SHALL 明确哪些区域未查询或被替换
