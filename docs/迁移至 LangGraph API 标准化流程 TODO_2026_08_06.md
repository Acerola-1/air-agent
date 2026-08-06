# 迁移至 LangGraph API 标准化流程 TODO

> 创建日期：2026-08-06
> 状态：待实施

---

## 一、背景

### 1.1 当前架构

项目目前有**两套并行的 Web 服务方案**：

| 方案 | 入口 | 用途 | 持久化 |
|------|------|------|--------|
| 自建 FastAPI | `uvicorn web.server:app --port 8125` | 本地开发 + 裸机部署 | SQLite（AsyncSqliteSaver） |
| langgraph-api Docker 镜像 | `langchain/langgraph-api:3.13` | Docker 容器部署 | PostgreSQL（平台管理） |

两套方案的 API 协议不一致：
- 自建 FastAPI 使用自定义 API 格式（`/api/threads`、`/api/threads/{id}/runs/stream`）
- langgraph-api 使用官方标准 API 格式（`/threads`、`/threads/{id}/runs/stream`）

前端（`app.js`）目前对接的是自建 FastAPI 的自定义格式。

### 1.2 为什么想统一到 langgraph-api

1. **获得 LangGraph Studio 可视化调试能力**——可以实时查看图的节点执行、状态变化、工具调用
2. **获得 SDK 兼容性**——Python SDK / JS SDK 开箱即用，无需手写 HTTP 客户端
3. **获得运行时治理能力**——并发控制、中断恢复（Human-in-the-loop）、Cron 定时任务
4. **本地开发与生产部署行为一致**——同一套 API 协议，只是存储后端不同
5. **不再自维护 400+ 行的 `web/server.py`**——线程管理、消息序列化、SSE 推送等全部由平台处理

### 1.3 langgraph-api 的三种运行模式

| 模式 | 启动方式 | 存储 | 需 Docker | 适用场景 |
|------|---------|------|----------|---------|
| 内存模式 | `langgraph dev` | 内存 SQLite | 否 | 本地开发调试 |
| PostgreSQL 模式 | `langgraph-api` Docker 镜像 | PostgreSQL | 是 | 生产部署 |
| 云托管模式 | `langgraph deploy` | 托管 PG + Redis | 否 | 无需运维 |

三种模式的 **API 协议完全一致**，前端只需对接一套。

---

## 二、当前阻塞问题

### 2.1 核心问题：graph 编译时硬编码了 checkpointer

`langgraph-api` 要求 **graph 不能自带 checkpointer**，持久化由平台自动管理。但当前所有 6 个 graph 在编译时都传了 `checkpointer=get_checkpointer()`：

| 图 | 文件 | 编译方式 |
|---|------|---------|
| basic-qa | `src/basic_qa/graph.py` | `build_business_graph()` → 内部 `builder.compile(checkpointer=get_checkpointer())` |
| intelligent-analysis | `src/intelligent_analysis/graph.py` | `build_business_graph()` → 同上 |
| data-analysis | `src/data_analysis/graph.py` | `build_graph().compile(checkpointer=get_checkpointer())` |
| intelligent-report | `src/intelligent_report/graph.py` | `create_deep_agent(checkpointer=get_checkpointer(), ...)` |
| deep-research | `src/deep_research/graph.py` | `create_deep_agent(checkpointer=get_checkpointer(), ...)` |
| intelligent-tracing | `src/intelligent_tracing/graph.py` | `create_deep_agent(checkpointer=get_checkpointer(), ...)` |

**涉及 3 个编译入口**：
1. `common/business_graph/builder.py` 的 `build_business_graph()` —— 被 basic-qa、intelligent-analysis 使用
2. `data_analysis/graph.py` 的 `build_graph().compile()` —— 被 data-analysis 使用
3. `intelligent_report/graph.py` / `deep_research/graph.py` / `intelligent_tracing/graph.py` 的 `create_deep_agent()` —— 被 3 个旧版 DeepAgents 图使用

### 2.2 启动验证结果

```
langgraph dev --config ./langgraph.json
```

报错：

```
ValueError: Your graph 'graph' from './src/basic_qa/graph.py' includes a custom 
checkpointer (type AsyncSqliteSaver). With LangGraph API, persistence is handled 
automatically by the platform, so providing a custom checkpointer here isn't 
necessary and will be ignored when deployed.
```

服务启动失败，所有 graph 无法加载。

### 2.3 版本过旧

当前 `langgraph-api==0.7.98` 已被官方标记为 **End of Life**，最新版本为 `0.12.0`。需要升级。

---

## 三、实施 TODO

### 阶段一：解除 checkpointer 硬编码（前置条件）

- [ ] **3.1** 重构 `build_business_graph()` —— 增加 `checkpointer` 参数，默认 `None`
  - 文件：`src/common/business_graph/builder.py`
  - 改动：`builder.compile(checkpointer=checkpointer)` 替代 `builder.compile(checkpointer=get_checkpointer())`
  - 当 `checkpointer=None` 时，编译出的 graph 不带 checkpointer，兼容 `langgraph-api`

- [ ] **3.2** 重构 `data_analysis/graph.py` —— 增加 `checkpointer` 参数
  - 文件：`src/data_analysis/graph.py`
  - 改动：`build_graph().compile(checkpointer=checkpointer)` 替代 `build_graph().compile(checkpointer=get_checkpointer())`

- [ ] **3.3** 重构 3 个 DeepAgents 图 —— 增加 `checkpointer` 参数
  - 文件：`src/intelligent_report/graph.py`、`src/deep_research/graph.py`、`src/intelligent_tracing/graph.py`
  - 改动：`create_deep_agent(checkpointer=checkpointer, ...)` 替代 `create_deep_agent(checkpointer=get_checkpointer(), ...)`

- [ ] **3.4** 添加环境变量或配置开关控制 checkpointer 行为
  - 方案 A：通过环境变量 `LANGGRAPH_API_MODE=true` 判断，为 True 时不传 checkpointer
  - 方案 B：各 graph 的 `checkpointer` 参数默认取 `get_checkpointer()`，但 `langgraph-api` 模式下显式传 `None`
  - 推荐方案 B，更显式、更可控

- [ ] **3.5** 确保 `web/server.py` 仍能正常工作
  - 在 `web/server.py` 的 lifespan 中，加载 graph 时显式传 `checkpointer=get_checkpointer()`
  - 验证自建 FastAPI 方案不受影响

### 阶段二：升级 langgraph-api 及相关依赖

- [ ] **3.6** 升级 `langgraph-api` 到最新版
  ```bash
  pip install -U "langgraph-cli[inmem]"
  ```
  当前 `0.7.98`（EOL）→ 目标 `0.12.0`

- [ ] **3.7** 升级 `langgraph-runtime-inmem` 到配套版本
  当前 `0.27.3` → 目标 `0.32.0`

- [ ] **3.8** 验证升级后 `langgraph dev` 能正常启动并加载 6 个图

### 阶段三：验证 langgraph-api 标准化流程

- [ ] **3.9** 启动 `langgraph dev`，验证 6 个图全部加载成功
  ```bash
  langgraph dev --config ./langgraph.json --no-browser
  ```

- [ ] **3.10** 通过 LangGraph Studio 测试基本对话流程
  - 打开 Studio UI
  - 选择 basic-qa 图
  - 发送测试消息，验证流式输出正常
  - 验证工具调用正常（find_skill、业务工具）

- [ ] **3.11** 通过标准 API 测试核心端点
  - `POST /assistants` —— 创建 assistant
  - `POST /threads` —— 创建线程
  - `POST /threads/{id}/runs/stream` —— 流式对话
  - `GET /threads/{id}/state` —— 获取状态

- [ ] **3.12** 验证 MCP 工具在 langgraph-api 模式下正常工作
  - MCP 连接是否正常建立
  - MCP 工具调用是否正常返回

### 阶段四：前端适配标准 API（可选，按需实施）

- [ ] **3.13** 改造 `app.js` 对接标准 API
  - 请求路径从 `/api/` 改为 `http://localhost:2024/`
  - 请求体格式适配标准 API（`assistant_id`、`input`、`stream_mode`）
  - SSE 事件解析适配标准格式（`messages/partial`、`messages/complete`、`end`）

- [ ] **3.14** 静态文件托管方案
  - 方案 A：`langgraph dev` 不托管前端，前端单独用 nginx / `python -m http.server` 托管
  - 方案 B：保留 `web/server.py` 仅做静态文件托管 + 反向代理到 langgraph-api

- [ ] **3.15** 决定 `web/server.py` 的去留
  - 如果前端完全适配标准 API → `web/server.py` 可以废弃
  - 如果需要保留自定义格式 → `web/server.py` 作为 BFF 层保留

### 阶段五：Docker 部署更新

- [ ] **3.16** 更新 Dockerfile，确保 graph 不带 checkpointer
  - `langgraph-api` 镜像会自动注入 PostgreSQL checkpointer
  - 需要确保 `langgraph.json` 中的 graph 在 Docker 环境下不传 checkpointer

- [ ] **3.17** 更新 `docker-compose-db.yaml`，确保 PostgreSQL 配置正确
  - 验证 `DATABASE_URI` 环境变量
  - 验证 checkpoint 持久化正常

---

## 四、风险与注意事项

1. **`web/server.py` 的兼容性**：改造后必须确保自建 FastAPI 方案仍能正常工作（传 checkpointer），同时 langgraph-api 模式也能正常工作（不传 checkpointer）。两者不能互相破坏。

2. **DeepAgents 图的 checkpointer**：`create_deep_agent()` 的 `checkpointer` 参数可能比 `StateGraph.compile()` 的更复杂，需要验证 DeepAgents 框架在 `checkpointer=None` 时的行为。

3. **`thread_meta` 表**：当前 `web/server.py` 使用自定义的 `thread_meta` 表管理线程元数据。`langgraph-api` 有自己的线程管理机制，迁移后 `thread_meta` 的数据需要考虑迁移或兼容。

4. **版本升级风险**：`langgraph-api` 从 `0.7.98` 升级到 `0.12.0` 跨越了多个小版本，API 可能有变化，需要仔细阅读 changelog。

5. **MCP 连接生命周期**：`langgraph-api` 模式下 graph 的加载和卸载由平台管理，MCP 连接的初始化时机可能不同，需要验证 MCP 工具在冷启动时是否正常。
