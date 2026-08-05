## 1. prompts.py 提示词精简

- [x] 1.1 删除 `TOOL_FAILURE_SILENCE_PROTOCOL` 常量（设为空字符串或从 `__all__` 和拼接逻辑中移除）
- [x] 1.2 精简 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD`：删除"用户可见输出约束"段落中的"严禁泄露内部信息""严禁过程化自述""严禁进度提示""如有不确定……一律不要展示"
- [x] 1.3 精简 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD`：删除"工具调用静默协议"段落（"只能发起 tool_calls""AIMessage.content 必须为空""工具返回后保持静默"等全部删除）
- [x] 1.4 精简 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD`：删除"最终正文要求"中的"不得提及前面执行过哪些工具或内部步骤""不得使用第一人称解释执行过程；不要写'我正在/我需要/我先/我再/我已'等过程语句"
- [x] 1.5 精简 `DATA_ANALYSIS_OUTPUT_GUARD`：删除"用户可见输出约束"段落中的"不得输出推理步骤、草稿、工具选择理由、参数推导过程""不得输出或提及 skill、工具、route、函数名称、参数、JSON、SQL、路径、ID……"
- [x] 1.6 精简 `DATA_ANALYSIS_OUTPUT_GUARD`：删除"工具调用静默协议"段落（"只能发起 tool_calls""保持静默"等全部删除）
- [x] 1.7 保留"内部机制追问处理"段落不变；保留"输出格式要求""通用规范要求""数据真实性约束"等业务规范段落不变

## 2. 中间件清理提示词补全

- [x] 2.1 在 `build_final_output_cleanup_prompt` 的"必须删除的内容"中增加显式条目："工具错误消息、状态码（如 502、500）、超时提示（如 timeout）、失败原因（如 API 返回错误）等不可原样输出或转述"

## 3. 权限上下文强化

- [x] 3.1 更新 `PermissionClassifyMiddleware._permission_context()`，明确 `permission_result.reason` 和 `permission_result.correction_text` 都是最终答复的生成依据
- [x] 3.2 调整权限上下文注入文案，要求最终答复结合 `reason` 与 `correction_text` 用自然语言说明权限修正原因和实际查询范围
- [x] 3.3 删除权限上下文注入文案中的"不得以字段名、JSON 或内部权限对象形式暴露 reason 与 correction_text"等"不暴露内部"类约束

## 4. 测试与验证

- [x] 4.1 更新提示词相关单元测试，确认 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 不包含已删除的段落文本
- [x] 4.2 更新最终输出清理单元测试，确认清理提示词覆盖工具错误消息、502/500、timeout、API 返回错误等内容，并保留权限修正说明
- [x] 4.3 更新权限上下文单元测试，确认注入文本要求以 correction_text 为主、reason 为辅生成最终披露说明
- [x] 4.4 运行 targeted 测试（`test_permission_classify_middleware.py`、`test_final_output_cleanup_middleware.py`、提示词相关测试）和 Ruff 检查
