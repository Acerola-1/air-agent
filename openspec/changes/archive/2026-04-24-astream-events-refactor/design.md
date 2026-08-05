## 背景

当前架构使用自定义 `StreamingMiddleware` 拦截 `model.astream()` 实现流式输出。LangChain/LangGraph 提供了官方的 `astream_events()` API，可以获取完整的事件流，包括：

- `on_chat_model_start`：LLM 开始调用
- `on_chat_model_stream`：LLM 流式输出 token
- `on_chat_model_end`：LLM 调用结束
- `on_tool_start`：工具开始执行
- `on_tool_end`：工具执行结束
- `on_chain_start`/`on_chain_end`：Chain 生命周期

```
当前架构：
┌─────────────────────────────────────────────────────────────────┐
│  Java → LangGraph.astream() → StreamingMiddleware → SSE        │
│                                  ↓                              │
│                          手动处理 tool_calls                     │
│                          手动推送 message                        │
└─────────────────────────────────────────────────────────────────┘

目标架构：
┌─────────────────────────────────────────────────────────────────┐
│  Java → LangGraph.astream_events() → 事件转换 → SSE             │
│                  ↓                                              │
│          自动获取完整事件流                                       │
│          tool_calls 自动保留                                     │
│          LangSmith 完美集成                                      │
└─────────────────────────────────────────────────────────────────┘
```

## 目标 / 非目标

**目标：**
- 使用 LangChain 官方 `astream_events()` API
- 支持完整的事件类型（thinking、tool_call、tool_result、answer）
- 移除自定义 `StreamingMiddleware`
- 与 LangSmith tracing 完美集成

**非目标：**
- 不修改 SubAgent 执行逻辑
- 不修改权限校验逻辑
- 不修改问题扩展逻辑

## 决策

### 决策 1：使用 astream_events() 替代 astream()

**选择**：Java 端调用 `graph.astream_events(input, version="v2")`

**原因**：
- 官方 API，自动处理 tool_calls
- 完整的事件类型
- 与 LangSmith 完美集成
- 无需自定义 Middleware

**事件格式**：
```python
{
    "event": "on_chat_model_stream",
    "name": "DeepSeek-V3.2",
    "run_id": "abc123",
    "data": {"chunk": AIMessageChunk(content="你好")},
    "parent_ids": []
}
```

### 决策 2：事件类型映射

**选择**：将 LangChain 事件映射为前端事件类型

| LangChain 事件 | 前端事件类型 | 说明 |
|---------------|-------------|------|
| `on_chat_model_stream` | `thinking` | 思考过程（可折叠） |
| `on_tool_start` | `tool_call` | 工具调用开始 |
| `on_tool_end` | `tool_result` | 工具调用结束 |
| `on_chain_end` (root) | `message` | 最终答案 |
| `custom` (stream_writer) | 保持原样 | 业务自定义事件 |

### 决策 3：保留 custom stream_mode

**选择**：保留 `stream_mode=["updates", "custom"]` 用于业务自定义事件

**原因**：
- `rich_output`：图表数据推送
- `expanded_questions`：推荐追问
- `progress`：进度提示

**实现**：
```python
# 同时使用 astream_events 和 custom stream
async for event in graph.astream(input, stream_mode=["updates", "custom"]):
    if event.get("type") == "custom":
        # 业务自定义事件
        yield event
```

### 决策 4：移除 StreamingMiddleware

**选择**：完全移除 `StreamingMiddleware`

**原因**：
- 不再需要自定义流式处理
- 减少代码复杂度
- 避免与 callback 机制冲突

## 风险 / 权衡

### 风险 1：前端需要同步更新

**风险**：事件格式变化，前端需要同步更新

**缓解**：
- 提供事件格式转换层
- 保持向后兼容的事件类型名称

### 风险 2：astream_events 性能

**风险**：`astream_events` 可能有额外开销

**缓解**：
- LangChain 官方优化
- 测试对比性能

### 风险 3：SubAgent 事件嵌套

**风险**：SubAgent 的事件可能有嵌套层级

**缓解**：
- 使用 `parent_ids` 过滤根级别事件
- 或使用 `subgraphs=True` 获取完整事件

## 迁移计划

1. **阶段 1**：Java 端实现 `astream_events` 调用
2. **阶段 2**：前端更新事件处理逻辑
3. **阶段 3**：移除 `StreamingMiddleware`
4. **阶段 4**：测试验证

**回滚**：保留 `StreamingMiddleware` 代码，可通过配置切换
