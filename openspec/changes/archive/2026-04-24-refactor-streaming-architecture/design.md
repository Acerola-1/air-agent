## 背景

### 当前架构问题

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     当前有问题的架构                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Python 端 (DeepAgent):                                                    │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ StreamingMiddleware:                                                 │   │
│   │   async for chunk in model.astream(messages):  # 绕过 handler!      │   │
│   │       writer({...})                                                  │   │
│   │       # 手动构造 AIMessage，丢失 tool_calls                          │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   问题链：                                                                   │
│   1. model.astream() 绕过了 DeepAgents 的 handler 链                       │
│   2. tool_calls 在流式 chunk 中是增量聚合的，手动处理容易丢失               │
│   3. 框架无法执行工具调用，直接返回空响应                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 官方推荐架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     官方推荐的流式输出架构                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Python 端:                                                                │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ DeepAgent.astream() → LangGraph Server                              │   │
│   │   stream_mode: ["messages", "updates"]                              │   │
│   │                                                                     │   │
│   │ Middleware 职责:                                                    │   │
│   │   - PermissionMiddleware: 权限校验                                  │   │
│   │   - RichOutputMiddleware: chart_data 推送 (custom 事件)             │   │
│   │   - 不负责 LLM 流式输出!                                            │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   Java 端:                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ WebClient.astream() → SSE 事件流                                    │   │
│   │                                                                     │   │
│   │ 事件类型:                                                           │   │
│   │   - messages: LLM 流式 chunk (AIMessageChunk)                       │   │
│   │   - updates: 节点更新、中断事件                                      │   │
│   │   - custom: 业务自定义事件 (chart_data, progress 等)                 │   │
│   │   - metadata: run_id 等                                             │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 目标 / 非目标

**目标：**
1. 恢复 DeepAgents 工具调用链路，SubAgent 能正常被调用
2. LLM 流式输出由 LangGraph 原生 `messages` 事件驱动，稳定可靠
3. 保持前端 SSE 事件格式兼容，无需前端修改
4. `custom` 事件仅用于业务自定义输出（chart_data、progress 等）

**非目标：**
1. 不修改前端代码
2. 不修改 SubAgent 配置
3. 不修改 MCP 工具定义
4. 不处理 `values` 事件类型（当前未使用）

## 决策

### 决策 1：移除 StreamingMiddleware

**选择**: 完全删除 `StreamingMiddleware`，而非重构

**理由**:
- 官方明确不建议在 middleware 中接管 `model.astream()`
- DeepAgents 原生支持 `stream_mode: ["messages"]` 输出 LLM chunk
- 保留 middleware 只会增加维护负担

**替代方案**:
- ❌ 修复 `tool_calls` 聚合逻辑 - 仍绕过 handler，风险高
- ❌ 改为监听框架事件 - DeepAgents 无此 API
- ✅ 删除 middleware，使用原生 `messages` 流

### 决策 2：Java 端处理 messages 事件

**选择**: 在 `LangGraphService.dispatchV2Event()` 中新增 `messages` 分支

**理由**:
- `messages` 事件包含 `(AIMessageChunk, metadata)` 元组
- Java 端已有完善的事件分发框架，扩展简单
- 前端 SSE 格式无需修改

**事件格式**:
```json
// LangGraph messages 事件
{
  "event": "messages",
  "data": [
    {"type": "ai", "content": "你好", "id": "msg-xxx"},
    {"langgraph_node": "agent", "langgraph_step": 1}
  ]
}

// 转换为前端 SSE
{
  "type": "message",
  "name": "assistant",
  "output": "你好"
}
```

### 决策 3：保留 RichOutputMiddleware

**选择**: 保留 `RichOutputMiddleware`，继续使用 `custom` 事件推送 `chart_data`

**理由**:
- `chart_data` 是业务自定义输出，不是 LLM 输出
- `custom` 事件正是为此场景设计
- Java 端已有 `handleCustomEvent()` 处理逻辑

### 决策 4：stream_mode 配置

**选择**: `["messages", "updates"]`（移除 `custom`）

**理由**:
- `messages`: LLM 流式输出（核心需求）
- `updates`: 中断事件、节点状态
- `custom`: 由 `stream_writer` 触发，不需要在 stream_mode 中声明

**注意**: 经验证，`custom` 事件由 `stream_writer()` 触发，无需在 `stream_mode` 中声明也能接收。但为了明确性，可选择保留或移除。

## 风险 / 权衡

### 风险 1：messages 事件格式解析错误
- **风险**: LangGraph `messages` 事件格式可能与预期不同
- **缓解**: 先打印原始事件日志，确认格式后再实现解析逻辑

### 风险 2：tool_calls 仍可能有问题
- **风险**: 如果 DeepAgents 版本有 bug，工具调用仍可能失败
- **缓解**: 升级到最新版 DeepAgents，查看官方测试用例

### 风险 3：流式输出延迟
- **风险**: `messages` 模式可能比 `custom` 模式有额外延迟
- **缓解**: 实测对比，如有问题可调整 LangGraph 配置

## 迁移计划

### 阶段 1：Python 端改造
1. 从 `agent.py` 移除 `StreamingMiddleware`
2. 删除 `streaming_middleware.py` 文件
3. 更新 `middleware/__init__.py` 导出

### 阶段 2：Java 端改造
1. 修改 `streamChat()` 中 `stream_mode` 为 `["messages", "updates"]`
2. 新增 `handleMessagesEvent()` 方法
3. 更新 `dispatchV2Event()` 增加 `messages` 分支
4. 添加调试日志确认事件格式

### 阶段 3：测试验证
1. 单元测试：验证 `messages` 事件解析
2. 集成测试：验证完整对话流程
3. 端到端测试：验证前端显示正常

### 回滚策略
- 保留 `StreamingMiddleware` 代码（注释掉），如需回滚可快速恢复
- Java 端通过配置项控制 `stream_mode`，便于切换

## 待解决问题

1. **Q: `custom` 事件是否需要在 `stream_mode` 中声明？**
   - A: 根据官方文档和实测，`stream_writer()` 推送的事件不需要在 `stream_mode` 中声明也能接收。但建议保留 `custom` 以明确意图。

2. **Q: `messages` 事件中的 `AIMessageChunk` 是否包含 `tool_calls`？**
   - A: 是的，流式 chunk 中会包含 `tool_call_chunks` 或完整的 `tool_calls`。Java 端无需处理，工具执行在 Python 端完成。

3. **Q: 是否需要处理 `values` 事件？**
   - A: 当前不需要。`values` 事件返回完整状态，用于特定场景（如获取最终结果）。`messages` + `updates` 已满足需求。
