## MODIFIED Requirements

### Requirement: 权限上下文注入契约
`PermissionClassifyMiddleware` SHALL 在 `<permission_context>` 注入说明中明确：发生权限修正、拒绝、截断、过滤、自动补全或部分区域处理时，`permission_result.correction_text` 是最终答复中用户可见权限披露的主要依据，`permission_result.reason` 是解释原请求为什么被拦截或修正的辅助依据。最终答复 MUST 在这些需要披露的场景中，以自然语言向用户说明发生了什么变化，以及实际查询范围是什么。

主流程 MUST NOT 要求按字段名、JSON 或内部权限对象形式输出 `reason` 与 `correction_text`；`reason` 只用于补充生成用户可理解的原因说明。

#### Scenario: 权限修正后最终答复包含修正说明
- **WHEN** 权限中间件修正了用户查询区域（如"洛阳市"→"郑州市"），主流程完成查询后输出最终答复
- **THEN** 最终答复 MUST 基于 `correction_text` 包含自然语言的权限修正说明，解释为什么查询的是修正后的区域
- **AND** 最终答复 MAY 结合 `reason` 补充用户可理解的原因说明

#### Scenario: 替换场景下说明实际查询区域
- **WHEN** 权限结果为区域替换
- **THEN** 最终答复 MUST 说明实际查询的区域范围

#### Scenario: 部分拒绝场景下说明可用和不可用范围
- **WHEN** 权限结果为多区域部分拒绝
- **THEN** 最终答复 MUST 说明哪些区域可用、哪些区域不可用

#### Scenario: 自动补全场景下说明补全内容
- **WHEN** 权限结果为时间或区域自动补全
- **THEN** 最终答复 MUST 说明实际查询的时间或区域范围

#### Scenario: 普通允许查询不要求权限披露
- **WHEN** 权限结果为允许访问且没有修正、拒绝、截断、过滤、自动补全或部分区域处理
- **THEN** `<permission_context>` MUST NOT 要求最终答复解释原请求为什么被拦截或修正
- **AND** 最终答复 MAY 直接回答用户业务问题

### Requirement: 权限上下文注入不承担通用内部信息清理
`<permission_context>` 注入说明 MUST NOT 承担通用内部信息清理职责；通用内部字段、JSON、对象结构等最终输出清理 SHALL 由 `FinalOutputCleanupMiddleware` 承担。权限上下文仅需说明 `reason` 与 `correction_text` 的生成用途，不应重复主流程通用"不暴露内部"约束。

#### Scenario: 权限上下文不包含通用内部信息禁止
- **WHEN** 读取 `<permission_context>` 注入文本
- **THEN** MUST NOT 包含通用的"严禁泄露内部信息""不得输出工具、SQL、路径、源码"等最终输出清理约束
