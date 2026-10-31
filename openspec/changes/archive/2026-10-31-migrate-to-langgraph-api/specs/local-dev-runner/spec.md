## ADDED Requirements

### Requirement: 提供统一本地启动脚本

系统 SHALL 提供 `run-local.sh` 脚本作为本地开发唯一入口，脚本 SHALL 自动激活 `.venv`、加载 `.env`、检查 SQLite checkpointer 父目录存在性、然后通过 `langgraph dev --config ./langgraph.json --no-browser` 启动 langgraph-api 平台。`run.sh` SHALL 被删除（迁移完成后），不再作为启动入口存在。

#### Scenario: 开发人员运行本地启动脚本

- **WHEN** 开发人员执行 `./run-local.sh`
- **THEN** SHALL 检查 `.env` 存在（缺失时给出明确错误并退出码非 0）
- **THEN** SHALL 检查 `.venv/bin/langgraph` 存在（缺失时提示运行 `uv venv && uv pip install -r requirements.lock.txt`）
- **THEN** SHALL 检查 SQLite 父目录可写（缺失时自动创建）
- **AND** SHALL 启动 `langgraph dev --config ./langgraph.json --no-browser` 在后台，并将 PID 写入 `/tmp/air-agent-langgraph.pid`
- **AND** SHALL 将 langgraph-api 输出重定向到 `/tmp/air-agent-langgraph.log`

#### Scenario: 旧 run.sh 脚本被删除

- **WHEN** 变更完成
- **THEN** `run.sh` SHALL NOT 存在
- **AND** `README.md` / `AGENTS.md` 中提及的"启动本地服务"步骤 SHALL 指向 `./run-local.sh`

### Requirement: 静态前端文件独立托管

系统 SHALL 让 `src/web/static/` 下的静态文件（`index.html` / `app.js` / `style.css` / `libs/`）由独立 HTTP 服务器托管（不嵌入 langgraph-api 进程），端口 SHALL 默认为 `8125`。前端 SHALL 通过 `LANGGRAPH_API_URL` 环境变量或运行时配置指向 langgraph-api 地址（默认 `http://localhost:2024`）。

#### Scenario: 访问前端首页

- **WHEN** 浏览器访问 `http://localhost:8125/`
- **THEN** SHALL 返回 `src/web/static/index.html`
- **AND** `index.html` SHALL 加载新版 `app.js` 并初始化连接到 langgraph-api 的 SSE 流

#### Scenario: 启动静态文件服务器

- **WHEN** 开发人员通过 `run-local.sh` 启动本地服务
- **THEN** 脚本 SHALL 同时启动 langgraph-api 进程和静态文件服务器（两个独立进程）
- **AND** SHALL 保证两者启动顺序——langgraph-api 优先（前端可重试连接）

#### Scenario: 跨域 CORS 配置

- **WHEN** 浏览器从 `http://localhost:8125` 向 `http://localhost:2024` 发起 fetch
- **THEN** langgraph-api SHALL 在响应头中允许 `Access-Control-Allow-Origin: http://localhost:8125`
- **AND** SHALL 允许 `Content-Type` / `Authorization` 头
- **AND** 跨域 SSE SHALL 通过 `EventSource` 或 `fetch + ReadableStream` 正常工作

### Requirement: 平台加载 6 个图并暴露标准端点

`langgraph.json` SHALL 包含 6 个图入口配置，平台 SHALL 在启动后暴露完整标准 API 端点（`/assistants`、`/threads`、`/threads/{id}/runs/stream` 等）。`/assistants/search` SHALL 至少返回 6 个 assistant（每个图一个），其 `graph_id` SHALL 与 `langgraph.json` 配置一致。

#### Scenario: 启动后调用 assistants 端点

- **WHEN** langgraph-api 完全启动
- **THEN** `GET http://localhost:2024/assistants` SHALL 返回 200
- **AND** `POST http://localhost:2024/assistants/search` body `{}` SHALL 返回至少 6 个 assistant
- **AND** 每个 assistant SHALL 包含 `assistant_id`、`graph_id`、`name`、`created_at` 字段

#### Scenario: 启动后创建线程

- **WHEN** `POST http://localhost:2024/threads` body `{}`
- **THEN** SHALL 返回 200 与 `{ "thread_id": "<uuid>", "created_at": "<ts>" }`
- **AND** 后续 `GET /threads/{id}/state` SHALL 正常返回空状态

#### Scenario: 启动后调用流式端点

- **WHEN** `POST http://localhost:2024/threads/{id}/runs/stream` body 携带 `assistant_id` 和 `input`
- **THEN** SHALL 返回 200 + `Content-Type: text/event-stream`
- **AND** SSE 事件 SHALL 符合 langgraph-api 标准协议（`event: message/values/end/...`）
