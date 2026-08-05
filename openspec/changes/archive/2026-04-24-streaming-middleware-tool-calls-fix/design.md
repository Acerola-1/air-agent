## 背景

当前 `StreamingMiddleware` 在 `awrap_model_call` 中使用 `model.astream()` 流式输出，但存在以下问题：

1. **tool_calls 未累积**：`astream()` 返回的每个 chunk 包含 `tool_calls` 属性，但代码只累积了 `content`
2. **AIMessage 缺少 tool_calls**：返回的 `AIMessage` 没有设置 `tool_calls`，导致 agent 循环判断没有工具调用而提前退出
3. **思考过程直接推送**：LLM 输出的工具调用文本被当作普通 message 推送给用户

```
当前代码流程：
model.astream() → chunk.content → writer({"type": "message"}) → 用户看到思考过程
                 ↓
                 chunk.tool_calls → 丢失！
                 ↓
AIMessage(content=..., tool_calls=[]) → agent 判断无工具调用 → 退出循环
```

## 目标 / 非目标

**目标：**
- 修复 StreamingMiddleware 正确累积 `tool_calls`
- 根据是否有工具调用，使用不同推送类型（`progress` vs `message`）
- 确保 agent 循环正常执行工具调用

**非目标：**
- 不修改前端代码（前端已有 `progress` 类型处理）
- 不修改其他 middleware
- 不改变 SubAgent 执行逻辑

## 决策

### 决策 1：累积 tool_calls

**选择**：在 `astream()` 循环中累积 `chunk.tool_calls`

**原因**：
- `langchain_openai.ChatOpenAI.astream()` 返回的 `AIMessageChunk` 包含 `tool_calls` 属性
- 需要将所有 chunk 的 `tool_calls` 合并到最终的 `AIMessage` 中

**代码**：
```python
tool_calls = []
async for chunk in model.astream(messages):
    if chunk.content:
        full_content += chunk.content
    if chunk.tool_calls:
        tool_calls.extend(chunk.tool_calls)
```

### 决策 2：分类推送类型

**选择**：根据是否有 tool_calls 使用不同推送类型

| 场景 | 推送类型 | 前端显示 |
|------|---------|---------|
| 有工具调用（思考阶段） | `progress` | 进度提示，不作为最终消息 |
| 无工具调用（直接回答） | `message` | 正常流式输出 |

**原因**：
- 前端已有 `progress` 类型处理，显示为进度提示
- 思考过程不应作为最终消息显示给用户
- 保持向后兼容，不修改前端代码

**代码**：
```python
if tool_calls:
    # 有工具调用，推送为 progress
    writer({
        "node": "model",
        "type": "progress",
        "message": chunk.content,
    })
else:
    # 无工具调用，正常流式推送
    writer({
        "node": "model",
        "type": "message",
        "name": "assistant",
        "message": chunk_content,
    })
```

### 决策 3：动态判断推送类型

**选择**：在流式输出过程中动态判断是否有 tool_calls

**原因**：
- `astream()` 的 tool_calls 可能在任意 chunk 中出现
- DeepSeek-V3.2 通常在第一个 chunk 就包含 tool_calls 信息
- 一旦检测到 tool_calls，后续 chunk 都使用 `progress` 类型

## 风险 / 权衡

### 风险 1：tool_calls 在流式输出中分散

**风险**：某些模型的 tool_calls 可能在多个 chunk 中分散返回

**缓解**：累积所有 chunk 的 tool_calls，确保完整性

### 风险 2：思考过程推送延迟

**风险**：需要等待第一个 chunk 判断是否有 tool_calls

**缓解**：DeepSeek-V3.2 在第一个 chunk 就包含 tool_calls 信息，延迟可忽略

### 风险 3：向后兼容性

**风险**：修改推送类型可能影响现有前端

**缓解**：前端已有 `progress` 类型处理，无需修改
