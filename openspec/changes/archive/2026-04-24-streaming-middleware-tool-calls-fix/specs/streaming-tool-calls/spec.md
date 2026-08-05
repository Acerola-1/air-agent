## 新增需求

### 需求：StreamingMiddleware 正确累积 tool_calls

StreamingMiddleware 在流式输出时 SHALL 累积所有 chunk 的 `tool_calls` 属性，并在最终的 `AIMessage` 中正确设置。

#### 场景：模型返回工具调用时 tool_calls 被正确累积

- **WHEN** 模型通过 `astream()` 返回包含 `tool_calls` 的 chunk
- **THEN** StreamingMiddleware SHALL 累积所有 chunk 的 `tool_calls`
- **AND** 最终返回的 `AIMessage` SHALL 包含完整的 `tool_calls` 列表

#### 场景：模型无工具调用时 tool_calls 为空

- **WHEN** 模型通过 `astream()` 返回不包含 `tool_calls` 的 chunk
- **THEN** 最终返回的 `AIMessage` SHALL 包含空的 `tool_calls` 列表

### 需求：根据工具调用状态分类推送

StreamingMiddleware SHALL 根据是否有工具调用，使用不同的推送类型。

#### 场景：有工具调用时使用 progress 类型推送

- **WHEN** 模型返回包含 `tool_calls` 的响应
- **THEN** StreamingMiddleware SHALL 使用 `type: "progress"` 推送思考过程
- **AND** 前端 SHALL 显示为进度提示，不作为最终消息

#### 场景：无工具调用时使用 message 类型推送

- **WHEN** 模型返回不包含 `tool_calls` 的响应
- **THEN** StreamingMiddleware SHALL 使用 `type: "message"` 推送内容
- **AND** 前端 SHALL 正常显示流式输出

### 需求：Agent 循环正常执行工具调用

修复后，Agent 循环 SHALL 正确检测到工具调用并继续执行。

#### 场景：工具调用被正确执行

- **WHEN** Main Agent 返回包含 `task` 工具调用的响应
- **THEN** Agent 循环 SHALL 检测到 `tool_calls` 不为空
- **AND** SHALL 调用对应的 SubAgent 执行任务
- **AND** SHALL 返回 SubAgent 的执行结果给用户

#### 场景：多轮工具调用正常执行

- **WHEN** SubAgent 返回结果后 Main Agent 需要继续调用工具
- **THEN** Agent 循环 SHALL 继续执行直到没有更多工具调用
