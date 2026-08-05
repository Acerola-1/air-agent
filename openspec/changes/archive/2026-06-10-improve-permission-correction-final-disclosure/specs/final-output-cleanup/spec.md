## MODIFIED Requirements

### Requirement: 最终答案流式清理输出
系统 SHALL 支持 finalizer 模型流式输出清理后的最终正文，调用方 SHALL 以 `final_output_delta` / `final_output_done` 事件作为用户可见聊天正文来源。

#### Scenario: 前端只渲染最终清理正文
- **WHEN** 业务 Agent 执行过程中产生中间 `AIMessage.content`、`tool_calls`、`ToolMessage` 或 LangGraph 更新事件
- **THEN** 调用方 MUST NOT 将这些中间消息作为用户可见聊天正文渲染
- **AND** 调用方 SHALL 使用 `FinalOutputCleanupMiddleware` 推送的 `final_output_delta` / `final_output_done` 事件作为最终用户可见正文

### Requirement: 只清理过程信息且保持正文保真
系统 SHALL 限制 finalizer 只删除过程性和内部实现信息，不得重构、扩写、压缩或改变 Skill 产出的业务正文；同时 SHALL 显式删除工具错误消息、状态码、超时和失败原因，并保留权限修正说明。

#### Scenario: 删除工具错误消息
- **WHEN** 待清理正文包含工具错误消息、状态码（如 502、500）、超时提示（如 timeout）或失败原因（如 API 返回错误）
- **THEN** finalizer 输出 MUST NOT 原样输出或转述这些工具错误信息
- **AND** finalizer 输出 SHALL 保留可用的业务结论、依据、关键数据、风险提示和建议

#### Scenario: 保留权限修正说明
- **WHEN** 待清理正文包含权限修正说明（如因权限限制调整查询区域、截断时间范围、过滤站点或部分拒绝区域）
- **THEN** finalizer 输出 MUST 保留该权限修正说明
- **AND** finalizer 输出 MUST NOT 将权限修正说明当作内部过程信息删除
