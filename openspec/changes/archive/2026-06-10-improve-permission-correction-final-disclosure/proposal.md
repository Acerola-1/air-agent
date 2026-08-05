## Why

权限修正后的最终答复可能遗漏权限修正说明——用户不知道自己的请求被改了。同时，主流程提示词中存在大量与 `FinalOutputCleanupMiddleware` 重复的最终输出限制，例如工具调用静默、禁止泄露内部信息、禁止过程化自述、禁止暴露工具错误等。这些限制会挤占业务提示词注意力，并与"必须说明权限修正"存在语义冲突。

当前前端用户可见正文只消费 `FinalOutputCleanupMiddleware` 推送的 `final_output_delta` / `final_output_done` 事件，不直接渲染主流程中间 `AIMessage.content`、`tool_calls`、`ToolMessage` 或 LangGraph 更新事件。因此，主流程提示词不需要继续承担最终可见输出清理职责；该职责应集中到 `FinalOutputCleanupMiddleware`。

## What Changes

- **移除工具调用绝对静默约束**：删除 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中的"工具调用静默协议"段落（"只能发起 tool_calls""AIMessage.content 必须为空""工具返回后保持静默"等）
- **移除所有"不暴露内部"类提示词约束**：删除"用户可见输出约束"中的"严禁泄露内部信息""严禁过程化自述""严禁进度提示"等段落，删除"最终正文要求"中的"不得提及工具或内部步骤""不得使用第一人称解释执行过程"，删除 `TOOL_FAILURE_SILENCE_PROTOCOL` 整段。这些职责统一由 `FinalOutputCleanupMiddleware` 承担
- **补强最终输出清理职责**：更新 `FinalOutputCleanupMiddleware` 的清理提示词，明确清理工具错误消息、状态码、超时和失败原因，且保留权限修正说明
- **保留业务规范和内部机制追问处理**：保留输出格式、国标单位、日期格式等业务规范段落；保留"内部机制追问处理"段落（用户追问内部机制时转换为业务语言）
- **强化权限上下文披露指令**：更新权限上下文注入说明，要求最终答复以 `permission_result.correction_text` 为主要用户可见依据，并结合 `permission_result.reason` 用自然语言说明权限修正原因和实际查询范围

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `silent-agent-output`: 主流程共享提示词不再强制工具调用轮 `AIMessage.content` 为空；最终用户可见正文由 `FinalOutputCleanupMiddleware` 事件承担
- `final-output-cleanup`: 强化最终输出清理契约，覆盖工具错误消息、状态码、超时和失败原因，并要求保留权限修正说明
- `permission-classify-middleware`: 强化权限上下文注入契约，要求以 `correction_text` 为主、`reason` 为辅生成最终用户可见的权限修正说明

## Impact

- `src/common/prompts.py`：删除 `TOOL_FAILURE_SILENCE_PROTOCOL`、精简 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD`
- `src/common/middleware/final_output_cleanup_middleware.py`：补强清理提示词的工具错误覆盖
- `src/common/middleware/permission_classify_middleware.py`：调整 `<permission_context>` 注入说明
- 相关单元测试中提示词文本断言需同步更新
