# Permission MCP Context Spec

## Purpose

明确两个权限 MCP 工具在确定性规则引擎方案中的职责边界和调用方式。

## MODIFIED Requirements

### Requirement: get_user_profile 必须作为可信用户画像来源

`get_user_profile` MUST 继续保留，并由中间件主动调用。

#### Scenario: 用户画像获取成功

**WHEN** 中间件调用 `get_user_profile(user_id)` 成功
**THEN** 返回结果必须用于构造 `PermissionFacts.user_profile`
**AND** 规则引擎必须基于该画像判断用户层级和绑定行政区

#### Scenario: 用户画像获取失败

**WHEN** `get_user_profile` 返回 `found=false` 或调用异常
**THEN** 系统必须按安全降级策略处理
**AND** 不得让 LLM 猜测用户权限画像

### Requirement: resolve_region_scope 必须作为可信请求行政区来源

`resolve_region_scope` MUST 继续保留，并由中间件基于 slots 主动调用。

#### Scenario: 使用 region_text 调用行政区解析

**WHEN** slots 中存在 `region_text`
**THEN** `resolve_region_scope.query` 必须使用该行政区文本
**AND** 不应优先使用完整用户问题

#### Scenario: 使用用户画像消歧

**WHEN** 用户画像包含 city 和 province
**THEN** 中间件必须将其 code 作为消歧上下文传给 `resolve_region_scope`

#### Scenario: 行政区解析多候选

**WHEN** `resolve_region_scope` 返回 `ambiguous=true`
**THEN** candidates 必须保留进入规则上下文
**AND** 规则引擎必须按安全策略处理，不得自行猜测唯一行政区

### Requirement: MCP 工具不得承担最终权限判定

MCP 工具 MUST 只提供事实，不返回最终权限结果。

#### Scenario: 工具返回行政区事实

**WHEN** `resolve_region_scope` 返回标准行政区对象
**THEN** 规则引擎必须使用该对象进行权限判断
**AND** MCP 工具不得返回 `permitted` 或 `fix_strategy` 作为最终结果
