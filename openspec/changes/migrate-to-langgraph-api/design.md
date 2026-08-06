## Context

项目当前对外提供两套 Web 服务：
- **自建 FastAPI**（`src/web/server.py`，17KB，400+ 行）：自定义协议 `/api/threads`、`/api/threads/{id}/runs/stream`，自维护 `thread_meta` 表与 SQLite checkpointer
- **langgraph-api Docker 镜像**（基于 `langchain/langgraph-api:3.13`）：标准协议 `/threads`、`/threads/{id}/runs/stream`

实际启动 `langgraph dev` 验证时 6 个图全部在 import 阶段失败，根因是 6 个 graph 模块在模块级调用 `builder.compile(checkpointer=get_checkpointer())`，而 `langgraph-api 0.7.98` 校验禁止这种用法。

代码现状关键点：
- 6 个图分布在 3 个编译入口：`build_business_graph()`（2 个图）、`build_graph()`（1 个图）、`create_deep_agent()`（3 个图）
- 3 个 DeepAgents 图是近似复制粘贴结构（仅 `name=` 和 `SKILLS_DIR` 不同）
- `web/server.py` 用 `astream_events(version="v3")` 推送自定义 SSE 事件类型 `{type: text|tool_call|done|error}`
- 静态前端 `src/web/static/app.js` 48KB，所有 fetch 走 `/api/...` 自定义路径
- `_langgraph_api/` 目录里已存在今天的 inmem runtime checkpoint 产物（4 个 pckl 文件），证明 `langgraph dev` 已经被尝试启动过

外部约束：
- `langgraph-api 0.7.98` 已 EOL，需升级到 `0.12.0`
- `langgraph-cli 0.4.19` / `langgraph-runtime-inmem 0.27.3` 需配套升级
- 锁文件 `requirements.lock.txt` 是 Docker 部署时的真理来源；本地 venv 与之必须保持一致
- 不引入 PostgreSQL / Redis 等外部服务（仅本地开发）

## Goals / Non-Goals

**Goals:**
- 让 6 个图能在 `langgraph dev`（in-memory 模式）下被平台成功 import，6 个图对外暴露为标准 assistant
- 前端通过 langgraph-api 标准协议（`/threads`、`/threads/{id}/runs/stream` 等）完成对话
- 本地 SQLite checkpointer 仍然作为非平台模式（未来 web/server.py 继任入口、或单测）下的持久化后端可用
- 静态前端由独立进程托管，跨域请求经 CORS 配置打通
- 保留 `db/checkpoints.db` 已有数据不丢
- 测试套件改造后必须全绿

**Non-Goals:**
- Docker / Apple Container 部署（独立 change）
- 引入 PostgreSQL 作为持久化后端（仅 `langgraph dev` + in-memory SQLite）
- Studio UI 之外的额外 UI
- 业务图逻辑（中间件链、工具、提示词）的任何修改
- 升级 langchain / deepagents / langchain-mcp-adapters 等其他依赖（仅升级 langgraph 平台三件套）
- 把 `web/server.py` 业务能力（rich_output / expanded_questions / finalizer）改造为 standard API 之外的额外机制——这些事件透传为 custom stream_mode

## Decisions

### 决策 1：graph 模块级 `graph` 变量默认不绑定 checkpointer

**选择**：6 个图入口的 `graph` 变量在模块级编译时 `checkpointer=None`。本地持久化由独立入口在运行时显式注入。

**理由**：
- langgraph-api 平台 import 阶段无法介入"传参"——必须在模块级决定
- 唯一可控点是让模块级图不带 checkpointer，平台用自己的 inmem checkpointer 注入
- 旧的自建路径（`web/server.py`）可以在自己的进程里 re-compile 并注入 SQLite checkpointer

**备选**：
- *A. 平台路径用环境变量判断*：用 `os.environ.get("LANGGRAPH_API_MODE")` 在模块 import 时分支。问题：环境变量在 import 时已固化，无法在运行时切换模式；测试时难以模拟。
- *B. 完全抽离编译到工厂函数*：模块不暴露 `graph`，只暴露 `build_graph(checkpointer=None)`。`langgraph.json` 入口指向一个调用工厂的小模块。问题：增加一层间接；与 langgraph-api 加载模型不完全契合（平台期望 `graph` 变量直接可调用）。

### 决策 2：新建 `src/langgraph_entry/` 目录统一暴露 6 个入口

**选择**：6 个 `src/langgraph_entry/<name>.py` 文件，每个只做一行 `from src.<name>.graph import graph`（或等价调用），由 `langgraph.json` 指向。

**理由**：
- 把"平台入口"与"业务图实现"分离，未来想替换入口机制不影响业务图本身
- 业务图 `src/<name>/graph.py` 改造为只暴露 `build_xxx_graph(checkpointer=None)` 工厂；不再有模块级 `graph = ...`
- 统一了"哪里是平台加载点"的认知，README/调试都清晰

**注意**：DeepAgents 的 3 个图（intelligent-report / deep-research / intelligent-tracing）改造时需要新增一个 `create_deep_business_graph(skills_dir, name, checkpointer=None)` 工厂，**替代**原来的"模块级 `graph = create_deep_agent(...)`"。

### 决策 3：本地 SQLite 持久化通过 `langgraph dev` 内置机制（inmem）+ 工厂函数 fallback 实现

**选择**：
- `langgraph dev` 模式：平台自带的 in-memory checkpointer（数据存到 `.langgraph_api/.langgraph_checkpoint.*.pckl` 文件），不依赖外部 SQLite
- 显式持久化场景：调用 `get_checkpointer()` 返回的 AsyncSqliteSaver 实例仍保留作为单测 / 继任入口使用

**理由**：
- 避免在 `langgraph dev` 模式下做"平台 checkpointer 与 SQLite 双写"——容易数据不一致
- `get_checkpointer()` 函数不删除；它继续被 `src/common/config/checkpointing.py` 单测、`tests/unit_tests/test_checkpointing.py` 使用
- 已有 `db/checkpoints.db` 数据保留作为历史兜底（如果某天有脚本需要导入）

**替代**：
- *A. 把 SQLite 嵌入 langgraph dev 启动*：通过 `langgraph.json` 的 `checkpointer` 字段注入。需要 langgraph-api 0.7.98+ 的 experimental 字段支持。保留为后续优化，不在本次 change 实施。

### 决策 4：前端用 `fetch + ReadableStream` 而不是 `EventSource`

**选择**：前端用 `fetch().then(r => r.body.getReader())` 解析 SSE，**不**用 `EventSource`。

**理由**：
- langgraph-api 的 `POST /threads/{id}/runs/stream` 是 POST 端点，`EventSource` 只支持 GET
- `fetch + ReadableStream` 支持自定义 headers、POST body、流控
- 已有 `web/server.py` 的流式响应也是 POST，与新方案一致

**注意**：SSE 解析逻辑需自己写——`ReadableStream` → `TextDecoder` → 按 `\n\n` 切分事件 → 按 `event: xxx\ndata: xxx` 解析字段。

### 决策 5：保留 `astream(stream_mode=["messages-tuple", "values", "custom"])` 作为标准流式协议

**选择**：让前端用 `stream_mode: ["messages-tuple", "values", "custom"]` 三种模式并行，平台会自动推送 LLM chunk、状态快照、自定义事件。

**理由**：
- `messages-tuple` 模式推 `(AIMessageChunk, metadata)` 元组，与旧 `astream_events(version="v3")` 的 `stream.messages.text` 行为类似，但协议标准化
- `values` 模式推完整 state snapshot，前端可基于此构建历史回放
- `custom` 模式透传中间件通过 `runtime.stream_writer` 推送的自定义事件（rich_output / expanded_questions / final_output_delta 等保持不变）

**替代**：
- *A. 只用 `events` 模式*：和旧 `astream_events` 行为最接近，但 langgraph-api 0.12 仍在演进该模式，不建议作为主路径

### 决策 6：静态前端由 Python 内置 http.server 托管

**选择**：用一个极简 Python 脚本（`scripts/serve_static.py`）启动 `http.server` 在 8125 端口，托管 `src/web/static/`。

**理由**：
- 不引入额外依赖（uvicorn / nginx / caddy）
- `run-local.sh` 同时启动 langgraph dev + 静态服务器，两者通过 `nohup` 后台运行
- 静态服务器可单独 kill 而不影响 langgraph-api 进程

**替代**：
- *A. `python -m http.server`*：可用，但 CORS 头不支持。改用 `http.server.SimpleHTTPRequestHandler` 子类加 CORS headers 简单
- *B. 嵌入 langgraph-api 的 custom route*：langgraph-api 0.12 支持 mount custom FastAPI app，但配置复杂且与平台版本耦合

### 决策 7：CORS 通过 langgraph-api 启动参数配置

**选择**：`run-local.sh` 调用 `langgraph dev` 时通过环境变量 `LANGSERVE_ALLOW_ORIGINS=http://localhost:8125`（或新版对应字段）开启 CORS。

**理由**：
- langgraph-api 0.12 提供 `--cors-allow-origins` CLI 参数（具体字段以新版文档为准）
- 不需要改任何业务代码

**风险**：CORS 配置字段名可能因版本不同而变化。实施时核对 langgraph-api 0.12 文档。

## Risks / Trade-offs

### 风险 1：`create_deep_agent(checkpointer=None)` 的未知行为

`create_deep_agent` 内部可能依赖 checkpointer 存在（如 `thread_id` 处理、`MemorySaver` 默认）。**缓解**：
- 实施时先读 `deepagents==0.6.10` 源码的 `create_deep_agent` 实现，确认 `checkpointer=None` 时的行为路径
- 准备一个 `test_deep_agent_no_checkpointer.py` 单测覆盖"以 None 调用、invoke 一轮对话、断言无 MemorySaver 副作用"

### 风险 2：前端 SSE 解析的边界情况

`fetch + ReadableStream` 解析 SSE 需要处理：
- chunk 跨多个网络包（半个事件 / 半个 data 行）
- UTF-8 字符跨 chunk 边界
- 连接异常断开 vs 服务端主动 end
- 心跳注释行（`:ping`）

**缓解**：在 `app.js` 抽出 `parseSSEStream(readableStream, handlers)` 工具函数，单元测试覆盖以上场景。

### 风险 3：langgraph-api 0.12 的 CORS / SSE 配置字段名变化

文档可能不完整或字段重命名。**缓解**：
- 升级后第一件事查 `langgraph dev --help` 输出的 flag 列表
- 准备 fallback：用反向代理（Python 3 行代码）做 CORS 兜底

### 风险 4：升级后 `langgraph dev` 启动时间变长

新版本可能引入额外初始化（如 LLM 凭据校验、embedding 模型预热）。**缓解**：
- 启动脚本加 30 秒 readiness probe（每 5 秒 curl `/assistants`，连续 3 次 200 视为就绪）
- 失败时打印 `langgraph-api` 启动日志尾部 50 行帮助排错

### 风险 5：现有 thread_meta 数据无法直接迁移

旧的 SQLite 里 `thread_meta` 表记录了 thread_id 与图关系。langgraph-api 不会自动识别。**缓解**：
- 不主动迁移——本地开发数据非生产
- `db/checkpoints.db` 文件保留作为历史兜底

### 风险 6：6 个图各自的 _build_middleware 行为差异

3 个 DeepAgents 图虽然复制粘贴，但 middleware 顺序、参数、tool filter 配置应保持原样。重构时**只**改 checkpointer 注入点，**不动** middleware 链。**缓解**：
- 提交前对每个图执行一次 `langgraph dev` 端到端冒烟测试
- 实施 diff 必须通过 `git diff --stat src/intelligent_report/graph.py src/deep_research/graph.py src/intelligent_tracing/graph.py` 审查改动面只涉及 checkpointer 相关行

### 风险 7：`_langgraph_api/` 目录的 gitignore 处理

升级后目录结构可能变化。**缓解**：
- 保留 `.langgraph_api/` 在 `.gitignore` 中
- 实施前清空旧文件，避免脏数据干扰

## Migration Path

按顺序执行：

1. **阶段 0（前置）**：升级 langgraph-cli/api/runtime-inmem 到目标版本；运行 `langgraph dev` 确认 6 个图原始 import 错误信息与计划一致
2. **阶段 1（graph 重构）**：
   - 改 `build_business_graph` 接受 `checkpointer` 参数（默认 None）
   - 改 `data_analysis/graph.py` 暴露 `build_graph(checkpointer=None)` 工厂
   - 抽 `create_deep_business_graph` 工厂函数；3 个 DeepAgents 图改为 `graph = create_deep_business_graph(skills_dir=..., name=..., checkpointer=None)`
3. **阶段 2（入口统一）**：
   - 新建 `src/langgraph_entry/<name>.py`（6 个）
   - `langgraph.json` 改 graphs 路径指向新入口
4. **阶段 3（前端改造）**：
   - 重写 `app.js` 流式与请求逻辑
   - 提供 `scripts/serve_static.py` + `run-local.sh`
5. **阶段 4（清理）**：
   - 删除 `src/web/server.py` 和 `run.sh`
   - 保留 `get_checkpointer()` 工厂供单测使用
6. **阶段 5（验证）**：
   - `make test` 全绿
   - 端到端冒烟：6 个图各跑一轮对话，SSE 流正常
   - SQLite 数据写入路径验证（`db/checkpoints.db` 在显式调用 `get_checkpointer()` 时仍写入）

## Open Questions

- **Q1**：升级到 `langgraph-api 0.12.0` 后 CORS 配置的具体 CLI 字段名是什么？（实施时通过 `langgraph dev --help` 确认）
- **Q2**：`create_deep_agent(checkpointer=None)` 在 deepagents 0.6.10 中是否会注入默认 `MemorySaver`？（实施前通过源码 + 单测确认）
- **Q3**：是否保留 `db/checkpoints.db` 旧数据？（建议保留——非生产数据无成本）
