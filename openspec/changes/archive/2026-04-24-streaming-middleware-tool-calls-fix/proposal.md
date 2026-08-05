## 背景

当前 `StreamingMiddleware` 使用 `model.astream()` 流式输出时，存在三个关键问题：

1. **tool_calls 丢失**：只累积了 `content`，没有累积 `tool_calls`，导致返回的 `AIMessage` 缺少工具调用信息
2. **思考过程暴露**：LLM 输出的工具调用文本（如 `task(...)`）被当作普通 message 推送给用户
3. **Agent 循环提前终止**：由于 `tool_calls` 丢失，agent 判断没有工具调用，直接退出进入 `aafter_agent`

这导致用户看到的是"我来为您查询...task(...)"这样的中间过程，而不是最终结果。

## 变更内容

- **修复 StreamingMiddleware**：累积 `chunk.tool_calls` 并正确设置到 `AIMessage` 中
- **分类推送**：根据是否有 tool_calls，使用不同的推送类型
  - 有工具调用时：使用 `type: "progress"` 推送思考过程（前端已有处理）
  - 无工具调用时：使用 `type: "message"` 正常流式推送
- **确保 Agent 循环正常**：`AIMessage.tool_calls` 正确设置后，agent 会继续执行工具调用

## 能力

### 新增能力

- `streaming-tool-calls`: StreamingMiddleware 正确处理 tool_calls，支持分类推送

### 修改能力

无

## 影响

- **代码影响**：`src/agent/middleware/streaming_middleware.py`
- **行为变化**：
  - 有工具调用时，思考过程推送为 `progress` 类型，前端显示为进度提示
  - 无工具调用时，正常流式推送为 `message` 类型
- **兼容性**：完全向后兼容，不影响现有前端逻辑
