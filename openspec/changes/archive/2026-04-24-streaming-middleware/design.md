## 背景

DeepAgent 使用 `langchain.agents.create_agent` 构建图，其 `_execute_model_async` 函数调用 `model.ainvoke()` 返回完整的 `AIMessage`，而非流式输出。这导致前端无法实时看到 LLM 的回答内容。

现有数据流：
```
Python (DeepAgent) → LangGraph Server (SSE) → Java (handleCustomEvent) → HTML (handleSSEEvent)
```

Java 和前端已正确处理 `type: "message"` 事件，问题在于 Python 端从未发送该事件。

## 目标 / 非目标

**目标：**
- 实现 `StreamingMiddleware`，拦截模型调用并改为流式输出
- 每个 token chunk 通过 `stream_writer` 推送到前端
- 保持 `ModelResponse` 返回结构不变，确保后续中间件正常工作

**非目标：**
- 不修改 `langchain-agents` 源码
- 不修改 Java 端代码
- 不修改前端代码
- 不实现思考过程分层展示（后续迭代）

## 决策

### 1. 使用 `awrap_model_call` 钩子

**选择**: 实现 `awrap_model_call` 而非 `awrap_tool_call`

**理由**:
- `awrap_model_call` 拦截模型调用，是流式输出的正确位置
- `awrap_tool_call` 仅拦截工具调用，无法获取 LLM 输出

### 2. 使用 `model.astream()` 而非 `model.ainvoke()`

**选择**: 流式调用 `model.astream(messages)`

**理由**:
- `astream()` 返回异步迭代器，可逐 token 获取输出
- `ainvoke()` 返回完整 `AIMessage`，无法流式推送

### 3. 累积完整响应后返回 `ModelResponse`

**选择**: 累积所有 chunk 后构造完整的 `AIMessage` 返回

**理由**:
- 后续中间件（如 `RichOutputMiddleware`）依赖完整的 `ModelResponse`
- `ModelResponse.result` 需要包含完整的消息列表

## 风险 / 权衡

**风险：流式输出可能增加延迟感知**
→ 缓解：流式输出反而让用户感知更快（实时看到内容）

**风险：中间件顺序敏感**
→ 缓解：`StreamingMiddleware` 需放在 middleware 列表末尾（在 `AnthropicPromptCachingMiddleware` 之前）

**权衡：需要累积完整响应**
→ 接受：内存开销可忽略（单次对话响应通常 < 10KB）
