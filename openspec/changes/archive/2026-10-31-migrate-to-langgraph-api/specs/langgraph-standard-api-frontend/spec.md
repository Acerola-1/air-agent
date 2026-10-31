## ADDED Requirements

### Requirement: 前端必须使用 langgraph 标准 API 端点

系统 SHALL 让前端（`src/web/static/app.js`）通过 langgraph-api 标准 HTTP 端点访问 6 个业务图，包括但不限于：`POST /assistants/search` 列出 assistant、`POST /threads` 创建线程、`GET /threads/{id}/state` 读取状态、`POST /threads/{id}/runs/stream` 流式对话、`GET /threads/{id}/history` 获取历史、`DELETE /threads/{thread_id}` 删除线程。请求基地址 SHALL 通过 `window.LANGGRAPH_API_URL`（默认 `http://localhost:2024`）配置。

#### Scenario: 前端列出可用 assistant

- **WHEN** 前端初始化时调用 assistant 列表接口
- **THEN** SHALL 调用 `POST ${LANGGRAPH_API_URL}/assistants/search` 并解析返回的 assistant 列表
- **AND** SHALL 从列表中提取 6 个图对应的 assistant（graph_id 匹配 basic-qa / intelligent-analysis / data-analysis / intelligent-report / deep-research / intelligent-tracing）
- **AND** SHALL 用解析结果填充 UI 侧边栏的图选择器

#### Scenario: 前端创建新线程

- **WHEN** 用户点击"新建对话"
- **THEN** SHALL 调用 `POST ${LANGGRAPH_API_URL}/threads` body 携带 `{ "graph_id": "<selected-graph>" }`
- **AND** SHALL 从响应中提取 `thread_id` 并切换到新对话
- **AND** SHALL 自动调用 `POST /threads/{id}/runs/wait` 写入初始空状态（避免首次 stream 报错）

#### Scenario: 前端发送流式消息

- **WHEN** 用户在输入框提交消息
- **THEN** SHALL 调用 `POST ${LANGGRAPH_API_URL}/threads/{thread_id}/runs/stream`
- **AND** 请求体 SHALL 包含 `assistant_id`、`input: { messages: [{ type: "human", content: ... }] }`、`stream_mode: ["messages-tuple", "values"]`
- **AND** SHALL 通过 `fetch` + `ReadableStream` 解析 SSE 事件

#### Scenario: 前端读取线程历史

- **WHEN** 用户切换到一个已存在的 thread
- **THEN** SHALL 调用 `GET ${LANGGRAPH_API_URL}/threads/{thread_id}/state`
- **AND** SHALL 从响应的 `values.messages` 字段解析历史消息列表
- **AND** SHALL 用解析结果填充 UI 消息区

#### Scenario: 前端删除线程

- **WHEN** 用户点击删除按钮
- **THEN** SHALL 调用 `DELETE ${LANGGRAPH_API_URL}/threads/{thread_id}`
- **AND** SHALL 在响应成功后刷新侧边栏线程列表

### Requirement: 前端 SSE 解析必须遵循标准事件格式

前端 SHALL 解析 langgraph-api 标准 SSE 事件类型，包括但不限于：`message`（LLM token chunk）、`tool_call`（工具调用发起）、`tool_result`（工具执行结果）、`values`（完整状态快照）、`end`（流结束）、`error`（错误）。自定义事件（rich_output、expanded_questions、final_output）SHALL 通过 `stream_mode: "custom"` 通道推送，前端 SHALL 透传并按现有 UI 格式渲染。

#### Scenario: 解析 LLM 流式 token

- **WHEN** SSE 事件类型为 `messages/partial` 或包含 `AIMessageChunk`
- **THEN** SHALL 提取 chunk.content 字段并追加到当前 AI 消息气泡的文本中
- **AND** SHALL 触发 UI 增量渲染

#### Scenario: 解析工具调用

- **WHEN** SSE 事件类型为 `tool_calls` 或包含 `tool_call_chunks`
- **THEN** SHALL 提取 tool_call 的 name / args / id
- **AND** SHALL 在 UI 中显示工具调用进度（与现有 `tool_call` 事件类型渲染一致）

#### Scenario: 解析自定义事件

- **WHEN** SSE 事件类型为 `rich_output` / `expanded_questions` / `final_output_delta` / `final_output_done`
- **THEN** SHALL 透传事件内容到现有 UI 渲染函数（与旧 `web/server.py` 推送的事件保持视觉一致）

#### Scenario: 流结束

- **WHEN** SSE 事件类型为 `end` 或连接关闭
- **THEN** SHALL 关闭 ReadableStream 锁，停止 loading 指示
- **AND** SHALL 调用 `touch_thread_meta`（如保留）或刷新侧边栏时间戳

#### Scenario: 流错误

- **WHEN** SSE 事件类型为 `error` 或连接断开
- **THEN** SHALL 在 UI 中显示错误信息
- **AND** SHALL 不丢失已收到的 token 增量

### Requirement: 前端不再依赖自建 FastAPI 协议

系统 SHALL 完全删除前端对 `/api/threads`、`/api/threads/{id}/runs/stream` 等自定义端点的调用。前端 SHALL NOT 在 `fetch` 调用中包含 `/api/` 路径；构建期 SHALL 通过简单 grep 检查（`grep -rn '/api/' src/web/static/`）确保无残留。

#### Scenario: 前端 fetch 调用检查

- **WHEN** 开发者对 `src/web/static/app.js` 执行 `grep "/api/"`
- **THEN** SHALL NOT 匹配到任何 `fetch(...)` / `XMLHttpRequest` 调用中的 `/api/` 路径
- **AND** 全部 fetch SHALL 使用 `LANGGRAPH_API_URL` 拼接的标准端点

#### Scenario: 静态文件直接托管

- **WHEN** 浏览器访问 `http://localhost:8125/`（或新的静态文件端口）
- **THEN** SHALL 返回 `src/web/static/index.html` 并加载新版本 `app.js`
- **AND** `app.js` SHALL 在加载时通过 `window.LANGGRAPH_API_URL` 指向 langgraph-api 服务地址
