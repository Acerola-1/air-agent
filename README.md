# Air Agent — 空气质量智能助手

LangGraph API（标准化 REST）+ Next.js 原生前端。6 个公开图，对话持久化到 PostgreSQL 容器。

## 6 个业务图

| Graph ID | 类型 | 用途 |
|---|---|---|
| `basic-qa` | 业务图节点流 | 基础问答 |
| `intelligent-analysis` | 业务图节点流 | 智能分析 |
| `data-analysis` | 业务图节点流 | 数据分析 |
| `intelligent-report` | DeepAgents | 智能报告 |
| `deep-research` | DeepAgents | 深度研究 |
| `intelligent-tracing` | DeepAgents | 智能污染溯源 |

## 架构

```
浏览器 (:3000, Next.js 原生前端)
   └─ /api/* 同源代理 (Next Route Handler, 注入 x-api-key)
        └─ langgraph-api (:2024, 官方镜像容器)
             ├─ PostgreSQL 容器 (命名卷 pgdata)  ← 线程/消息/画布持久化
             ├─ Redis 容器 (迁移锁 + 队列)
             └─ 本地代码挂载 ./src:/deps/air_agent/src  ← 改代码重启即生效
```

- **后端**: 官方 `langchain/langgraph-api:3.13` 镜像 + 项目代码（Dockerfile 构建）。
- **前端**: Next.js 原生 LangGraph SDK（无 assistant-ui 线程层），线程列表按 `metadata.{graph_id, user_id}` 过滤，对话走 `runs.stream`（SSE）。
- **存储**: PostgreSQL 容器（`-p` 端口转发在 Apple Container 1.2.2 已验证可用）。本地 `db/checkpoints.db`（SQLite）已弃用。

## 本地开发（容器化模式）

**前置条件**：
- Apple Container 系统已启动：`container system start`
- `.env` 存在（`cp .env.example .env` 后填入真实配置，含 `AUTH_SECRET`、`MCP_SERVER_URL`、各 API Key）

**一键启动**：
```bash
./run-local.sh
# 浏览器打开 http://localhost:3000
```

`run-local.sh` 做两件事：
1. `container-compose up -d --profile prod` —— 拉起 postgres + redis + langgraph-api 容器（:2024）
2. 后台启动 Next.js 前端（:3000）

**改后端代码后更新（无需重建镜像）**：
```bash
container-compose up -d --profile prod
# 或直接重跑 ./run-local.sh
```
本地 `src/` 已挂载进容器（`./src:/deps/air_agent/src`），重启容器即加载新代码。

**停止/管理**：
```bash
container-compose down          # 停容器（pgdata 卷保留）
container logs -f air-agent-langgraph-api
```

**注意**：容器为 `profiles: [prod]` 门控；`container restart` 插件不可用，统一用 `container-compose up -d --profile prod` 重启。旧 venv `langgraph dev`（内存持久化）已废弃。

## 登录系统（简易本地账号密码）

- 手写 HMAC session cookie（`frontend/lib/session-token.ts`）+ bcrypt 账号存储（`frontend/lib/user-store.ts`，`data/users.json`，gitignored）。
- 未登录访问任意页面 → middleware 重定向 `/login`；注册/登录后前端把账号 id 写入 `localStorage.air_agent_user_id`，线程 `metadata.user_id` 按账号隔离历史。
- 密钥：`.env` 的 `AUTH_SECRET`（必填，随机长串）。

## 用户隔离与持久化

- 线程创建：`threads.create({ threadId, metadata: { graph_id, user_id, title } })`（容器版只认 metadata，顶层 graphId 被忽略）。
- 历史加载：`threads.search({ metadata: { graph_id, user_id } })` → 每个浏览器/账号只看到自己的线程。
- 画布（HTML 报告）：`create_artifact` 工具把 `artifact_ref` 写入 ToolMessage，随线程由 PG checkpointer 持久化；前端按 threadId 隔离恢复。

## 容器化部署（staging）

```bash
container build -t air-agent/langgraph-api:latest .   # 需重新构建时（改依赖/新增文件）
container-compose up -d --profile prod
```

## 端到端冒烟

```bash
curl -s -X POST http://localhost:2024/assistants/search \
  -H "Content-Type: application/json" -d '{}' \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)), 'assistants')"
# 应输出: 6 assistants
```

## 测试

```bash
make test                 # 单元测试
make integration_tests    # 集成测试
```
