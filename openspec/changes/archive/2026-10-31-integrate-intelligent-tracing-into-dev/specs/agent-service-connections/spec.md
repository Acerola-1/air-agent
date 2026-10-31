# Spec Delta

## ADDED Requirements

### Requirement: 业务可声明更窄的 MCP 工具子集

系统 SHALL 允许业务在通用 `approved_query_tools` 之外声明更窄的工具子集；实际可调用工具 MUST 同时满足业务子集、连接 `approved_query_tools` 和本次已受理选择。子集只能缩小范围，MUST NOT 扩大其他业务或连接的能力。

#### Scenario: 溯源排除轨迹工具
- **WHEN** 通用连接清单包含轨迹/PSCF，但 `intelligent-tracing` 业务子集排除它们
- **THEN** tracing 发现和执行均不包含这三个工具
- **AND** 其他业务仍按各自子集和连接清单工作

#### Scenario: 子集与连接交集
- **WHEN** 工具在业务子集中但不在连接 `approved_query_tools` 中
- **THEN** 仍不可调用，网络请求数为 0
- **AND** 反向情况同样拒绝

#### Scenario: 新增工具不自动开放
- **WHEN** 连接新增一个未在业务子集登记的工具
- **THEN** 不出现在目录，也不能被模型或子任务调用
