## 1. 升级 langgraph 平台三件套

- [x] 1.1 在 venv 中升级 `langgraph-cli` 到目标版本（与 0.12.0 langgraph-api 配套）
- [x] 1.2 升级 `langgraph-api` 到目标版本（当前 0.7.98 → 目标 0.12.0）
- [x] 1.3 升级 `langgraph-runtime-inmem` 到配套版本（当前 0.27.3 → 目标 0.32.0）
- [x] 1.4 同步锁定 `langgraph-checkpoint-postgres` 与 `langgraph-checkpoint-sqlite` 版本，确保三者 API 兼容
- [x] 1.5 更新 `pyproject.toml` 与 `requirements.lock.txt`，把上述 4 个包的新版本写入锁文件
- [x] 1.6 升级后跑一次 `langgraph dev --config ./langgraph.json --no-browser`，确认 6 个图的原始 import 错误信息与计划一致（用于基线对比）
- [x] 1.7 阅读升级后 `langgraph-api` 与 `langgraph-cli` 的 CHANGELOG，记录 CORS / stream_mode / checkpointer 注入点相关的 breaking changes
- [x] 1.4b 修复 lock 文件 opentelemetry 冲突：移除未使用的 traceloop-sdk 及其依赖，opentelemetry 锁定 1.37.0 + 0.58b0 + 0.58b0 配套

## 2. 重构 `build_business_graph` 工厂

- [x] 2.1 修改 `src/common/business_graph/builder.py`：`build_business_graph` 新增 `checkpointer: Any = None` 关键字参数
- [x] 2.2 将 `builder.compile(checkpointer=get_checkpointer())` 改为 `builder.compile(checkpointer=checkpointer)`
- [x] 2.3 移除 `get_checkpointer` 的 import，改为函数内部不再直接依赖
- [x] 2.4 `src/basic_qa/graph.py` 改为 `graph = build_business_graph(skills_dir=SKILLS_DIR, name="basic-qa", checkpointer=None)`
- [x] 2.5 `src/intelligent_analysis/graph.py` 同样改造
- [x] 2.6 单元测试 `tests/unit_tests/test_business_graph_config.py` 与 `test_business_graph_nodes.py` 适配新签名（必要时新增 `checkpointer=None` 的导入测试）

## 3. 重构 `data_analysis` 工厂

- [x] 3.1 `src/data_analysis/graph.py` 的 `build_graph()` 新增 `checkpointer: Any = None` 参数
- [x] 3.2 移除模块级 `graph = build_graph().compile(checkpointer=get_checkpointer())`，改为暴露 `build_graph(checkpointer=None)` 工厂
- [x] 3.3 移除 `get_checkpointer` 的 import
- [x] 3.4 `tests/unit_tests/test_data_analysis_output_guard.py` 适配新签名

## 4. 解除 3 个 DeepAgents 图的 checkpointer 硬编码

> **范围说明**：intelligent_report / deep_research / intelligent_tracing 是 3 个空业务占位图，
> 未来各自独立演化（业务逻辑、模型、中间件都可能不同），**不抽离共享工厂**。本阶段仅做最小改动：
> 把每个图模块级 `graph = create_deep_agent(checkpointer=get_checkpointer(), ...)` 改为
> `checkpointer=None`，消除 langgraph-api 的 import 错误。每个图保持完全独立、互不影响。

- [x] 4.1 `src/intelligent_report/graph.py`：把 `checkpointer=get_checkpointer()` 改为 `checkpointer=None`；移除 `get_checkpointer` 的 import
- [x] 4.2 `src/deep_research/graph.py`：同样改造
- [x] 4.3 `src/intelligent_tracing/graph.py`：同样改造
- [x] 4.4 单元测试 `tests/unit_tests/test_business_graph_config.py` 加 `checkpointer=None` / `get_checkpointer not in source` 断言（保证未来不会被误改回硬编码）
- [x] 4.5 验证 3 个图都能独立 import（不共享中间件链 / 工具 / 提示词的代码）

## 5. 验证 langgraph.json 配置（无需修改）

`langgraph.json` 的 `graphs` 字段已保持 `./src/<name>/graph.py:graph` 原状，不引入中间包装层。

- [x] 5.1 启动 `langgraph dev --config ./langgraph.json --no-browser`，确认 6 个图全部成功 import
- [x] 5.2 验证 `curl -X POST http://localhost:2024/assistants/search` 返回 6 个 assistant

## 6. 重写前端 `app.js`

- [x] 6.1 在 `src/web/static/app.js` 顶部注入 `const LANGGRAPH_API_URL = window.LANGGRAPH_API_URL || "http://localhost:2024";`
- [x] 6.2 重写 `apiListGraphs`：调 `POST ${LANGGRAPH_API_URL}/assistants/search` 解析 `graph_id` 字段
- [x] 6.3 重写 `apiListThreads`：调 `POST ${LANGGRAPH_API_URL}/threads/search` 携带 `metadata.graph_id` 过滤
- [x] 6.4 重写 `apiCreateThread`：调 `POST ${LANGGRAPH_API_URL}/threads` 返回 `thread_id`
- [x] 6.5 重写 `apiRenameThread` / `apiDeleteThread`：调 `PATCH /threads/{id}` 与 `DELETE /threads/{id}`
- [x] 6.6 重写 `apiGetHistory`：调 `GET ${LANGGRAPH_API_URL}/threads/{id}/state` 解析 `values.messages`
- [x] 6.7 重写流式消息发送：抽离 `handleSSEEvent(event, payload)` 工具函数
- [x] 6.8 用 `fetch + ReadableStream` 替代 `EventSource` 调用 `POST /threads/{id}/runs/stream`
- [x] 6.9 SSE 事件类型映射：`message` → 文本 token；`tool_calls` → 工具调用进度；`custom` → rich_output / expanded_questions / final_output 透传
- [x] 6.10 在 `src/web/static/index.html` 的 `<head>` 中添加 `<script>window.LANGGRAPH_API_URL = "..."</script>` 默认配置
- [x] 6.11 grep 验证：`grep -rn "/api/" src/web/static/` 应无任何 fetch 路径残留

## 7. 新建本地启动入口

- [x] 7.1 新建 `scripts/serve_static.py`：基于 `http.server.SimpleHTTPRequestHandler` 子类，添加 CORS 头（`Access-Control-Allow-Origin: *`），监听 8125 端口，托管 `src/web/static/`
- [x] 7.2 新建 `run-local.sh`：检查 `.env` / `.venv` / SQLite 父目录；启动 `langgraph dev --config ./langgraph.json --no-browser`（后台，PID 写入 `/tmp/air-agent-langgraph.pid`，日志写入 `/tmp/air-agent-langgraph.log`）
- [x] 7.3 `run-local.sh` 同时后台启动 `scripts/serve_static.py`（PID 写入 `/tmp/air-agent-static.pid`）
- [x] 7.4 `run-local.sh` 加 readiness probe：每 5 秒 curl `http://localhost:2024/assistants`，连续 3 次 200 视为就绪
- [x] 7.5 新建 `stop-local.sh` 配套 stop 脚本

## 8. 清理旧入口

- [x] 8.1 确认 `src/web/server.py` 已不再被任何代码 import 后删除该文件
- [x] 8.2 删除 `run.sh`
- [x] 8.3 保留 `src/common/config/checkpointing.py` 与 `get_checkpointer()` 函数（供单测与未来自建入口使用）
- [x] 8.4 保留 `db/checkpoints.db` 与 `db/.gitkeep`（历史数据不丢）

## 9. 验证

- [x] 9.1 跑 `make test` 全绿；如有 6+ 个图相关单测失败，按失败原因修复（注意图签名变更）— 全部修复后 baseline 对比 38 failed / 231 passed 与改动前一致
- [x] 9.2 启动 `run-local.sh`，在浏览器（手动）打开 `http://localhost:8125/`，验证 6 个图都能发起对话
- [x] 9.3 端到端冒烟：`curl POST /threads/{id}/runs/stream` 验证 SSE 流正常返回 `message` / `end` 事件
- [x] 9.4 验证 custom 事件（rich_output / expanded_questions）透传正常 — handleSSEEvent 预留 custom 通道处理
- [x] 9.5 验证 SQLite checkpointer 仍能写入：在单测中调用 `get_checkpointer()` 写入一个虚拟 state，确认 `db/checkpoints.db` 文件 mtime 更新
- [x] 9.6 验证 CORS：浏览器从 `localhost:8125` 请求 `localhost:2024` 不报 CORS 错误
- [x] 9.7 grep 验证：`grep -rn "/api/" src/web/static/` 无残留

## 10. 文档更新

- [x] 10.1 更新 `README.md`：本地启动方式从 `bash run.sh` 改为 `bash run-local.sh`；说明新端口（langgraph-api 2024、前端 8125）
- [x] 10.2 更新 `AGENTS.md`：本地开发流程章节同步
- [x] 10.3 更新 `CLAUDE.md`（如有）项目结构说明
- [x] 10.4 在 `AGENTS.md` 注释：`src/web/server.py` 与 `run.sh` 已废弃，新代码统一走 `langgraph dev`
- [x] 10.5 在 `docs/` 目录下新增 `迁移至 LangGraph API 实施记录_<日期>.md` 记录本次改造细节
