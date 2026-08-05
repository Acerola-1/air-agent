## 背景

DeepAgent 重构后，`model_node` 使用 `model.ainvoke()` 非流式调用，导致 LLM 回答内容无法通过 `stream_writer` 推送到前端。前端只收到 `progress`、`rich_output`、`expanded_questions` 事件，但缺少核心的 `message` 事件，用户看不到 AI 的回答内容。

## 变更内容

- 新增 `StreamingMiddleware` 中间件，实现 `awrap_model_call` 钩子
- 将 `model.ainvoke()` 改为 `model.astream()` 流式调用
- 每个 token chunk 通过 `stream_writer` 推送 `type: "message"` 事件
- 保持原有 `ModelResponse` 返回结构不变

## 能力

### 新增能力

- `streaming-output`: LLM 流式输出能力，通过 `stream_writer` 实时推送 token 到前端

### 修改能力

无（新增能力，不影响现有 spec）

## 影响

- **新增文件**: `src/agent/middleware/streaming_middleware.py`
- **修改文件**: `src/agent/agent.py` - 在 middleware 列表中添加 `StreamingMiddleware`
- **前端**: 无需修改，已支持 `type: "message"` 事件处理
- **Java**: 无需修改，已支持 `type: "message"` 事件转发
