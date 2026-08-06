## Why

项目当前存在两套并行的 Web 服务方案（自建 FastAPI + langgraph-api 镜像），两套方案的 API 协议不一致，前端被迫只对接其中一套。本次变更要做的是**在本地开发场景下完成 langgraph-api 标准化**：让 6 个业务图可以在 `langgraph dev` 模式下启动并对外暴露标准 API，前端切换到标准 SDK 调用，从而获得 LangGraph Studio 可视化能力、官方 SDK 兼容性、运行时治理能力（并发、中断恢复、Cron）。**Docker / Apple Container 部署规划不在本次变更范围内**，将作为后续独立 change 实施。

## What Changes

- **重构 6 个业务图的编译入口**：将 `checkpointer=get_checkpointer()` 硬编码替换为可配置参数（默认 `None`），消除 `langgraph-api` 在 import 阶段抛出的 `ValueError`
  - `src/common/business_graph/builder.py` 的 `build_business_graph()` 增加 `checkpointer` 参数
  - `src/data_analysis/graph.py` 的 `build_graph()` 暴露 `checkpointer` 入参
  - 3 个 DeepAgents 图（intelligent-report / deep-research / intelligent-tracing）**保持独立实现**，仅把各自 `create_deep_agent(checkpointer=get_checkpointer(), ...)` 改为 `checkpointer=None`（不抽离共享工厂——这 3 个图是空业务占位，未来各自演化）
- **保留 `langgraph.json` 的 `graphs` 入口**：维持 `./src/<name>/graph.py:graph` 原状，无中间包装
- **升级 langgraph 平台相关包**：`langgraph-cli` / `langgraph-api` / `langgraph-runtime-inmem` 升到配套版本；同步锁定 `langgraph-checkpoint-postgres` 与 `langgraph-checkpoint-sqlite` 版本
- **重写前端 `app.js` 适配标准 API**：请求路径、请求体、SSE 事件解析全部切换到 langgraph-api 标准协议；对接 `POST /threads/{id}/runs/stream` 等官方端点
- **删除 `src/web/server.py` 与 `run.sh`**：**BREAKING** —— 自建 FastAPI 路径废弃；静态文件改由独立简易 HTTP server 托管（仅用于本地前端调试）
- **保留 `langgraph.json` 的 `graphs` 入口**：维持 `./src/<name>/graph.py:graph` 原状（无需修改）
- **新增 `run-local.sh` 替代 `run.sh`**：包装 `langgraph dev --config ./langgraph.json --no-browser` 启动命令，统一本地开发入口
- **保留 SQLite 作为 langgraph dev 的 in-memory checkpointer 后端**：不引入 PostgreSQL，避免本地开发依赖外部服务

## Capabilities

### New Capabilities

- `langgraph-api-graph-compat`: 6 个业务图必须能在 `langgraph dev`（inmem 模式）下被 langgraph-api 平台加载，不在 import 阶段抛 `ValueError`；每个图在编译时 `checkpointer=None`，由平台在加载时注入持久化后端
- `langgraph-standard-api-frontend`: 前端必须使用 langgraph-api 标准 API 协议（`/threads`、`/threads/{id}/runs/stream` 等）发起对话请求并解析流式响应
- `local-dev-runner`: 本地开发流程必须有统一的启动入口（`run-local.sh` + `langgraph dev`），提供 6 个图、静态前端托管、SSE 端点等完整本地闭环

### Modified Capabilities

无（本次变更不动现有 spec 下的业务行为；只把入口换成标准 API 协议）

## Impact

- **受影响的代码**：
  - `src/basic_qa/graph.py`、`src/intelligent_analysis/graph.py`、`src/data_analysis/graph.py`、`src/intelligent_report/graph.py`、`src/deep_research/graph.py`、`src/intelligent_tracing/graph.py`（6 个图入口重写或参数化）
  - `src/common/business_graph/builder.py`（`build_business_graph` 签名变更）
  - `src/web/static/app.js`（前端 SSE/请求协议全改）
  - `pyproject.toml` / `requirements.lock.txt`（langgraph-cli/api/runtime-inmem 版本升级）
  - `run.sh` → `run-local.sh`（本地启动入口替换）
  - `src/web/server.py`（**删除**）
  - `src/common/config/checkpointing.py`（保留，作为本地 SQLite 持久化后端供非平台模式使用）
  - `db/checkpoints.db`（保留，本地持久化数据不丢）
- **受影响的 API 协议**（**BREAKING**）：前端不再调用 `/api/threads/...`、自定义 SSE 事件类型，统一走 langgraph-api 标准协议
- **受影响的测试**：`tests/unit_tests/test_langgraph_business_graphs.py`、`test_checkpointing.py`、`test_business_graph_config.py` 等 6+ 个图相关单测需重写或更新断言
- **不在本次变更范围内**（明确排除）：
  - Docker / Apple Container 部署（独立 change）
  - 引入 PostgreSQL 作为持久化后端（仅 `langgraph dev` 模式 + in-memory SQLite）
  - langgraph Studio 之外的额外 UI（仍使用现有 `src/web/static/index.html` + `app.js`）
  - 后端业务逻辑重构（仅做平台适配，不动中间件链 / 业务工具）
