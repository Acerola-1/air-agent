## 1. 升级 langgraph 平台三件套

- [ ] 1.1 在 venv 中升级 `langgraph-cli` 到目标版本（与 0.12.0 langgraph-api 配套）
- [ ] 1.2 升级 `langgraph-api` 到目标版本（当前 0.7.98 → 目标 0.12.0）
- [ ] 1.3 升级 `langgraph-runtime-inmem` 到配套版本（当前 0.27.3 → 目标 0.32.0）
- [ ] 1.4 同步锁定 `langgraph-checkpoint-postgres` 与 `langgraph-checkpoint-sqlite` 版本，确保三者 API 兼容
- [ ] 1.5 更新 `pyproject.toml` 与 `requirements.lock.txt`，把上述 4 个包的新版本写入锁文件
- [ ] 1.6 升级后跑一次 `langgraph dev --config ./langgraph.json --no-browser`，确认 6 个图的原始 import 错误信息与计划一致（用于基线对比）
- [ ] 1.7 阅读升级后 `langgraph-api` 与 `langgraph-cli` 的 CHANGELOG，记录 CORS / stream_mode / checkpointer 注入点相关的 breaking changes

## 2. 重构 `build_business_graph` 工厂

- [ ] 2.1 修改 `src/common/business_graph/builder.py`：`build_business_graph` 新增 `checkpointer: Any = None` 关键字参数
- [ ] 2.2 将 `builder.compile(checkpointer=get_checkpointer())` 改为 `builder.compile(checkpointer=checkpointer)`
- [ ] 2.3 移除 `get_checkpointer` 的 import，改为函数内部不再直接依赖
- [ ] 2.4 `src/basic_qa/graph.py` 改为 `graph = build_business_graph(skills_dir=SKILLS_DIR, name="basic-qa", checkpointer=None)`
- [ ] 2.5 `src/intelligent_analysis/graph.py` 同样改造
- [ ] 2.6 单元测试 `tests/unit_tests/test_business_graph_config.py` 与 `test_business_graph_nodes.py` 适配新签名（必要时新增 `checkpointer=None` 的导入测试）

## 3. 重构 `data_analysis` 工厂

- [ ] 3.1 `src/data_analysis/graph.py` 的 `build_graph()` 新增 `checkpointer: Any = None` 参数
- [ ] 3.2 移除模块级 `graph = build_graph().compile(checkpointer=get_checkpointer())`，改为暴露 `build_graph(checkpointer=None)` 工厂
- [ ] 3.3 移除 `get_checkpointer` 的 import
- [ ] 3.4 `tests/unit_tests/test_data_analysis_output_guard.py` 适配新签名

## 4. 抽离 DeepAgents 业务图工厂

- [ ] 4.1 在 `src/common/deep_business_graph/` 新建 `factory.py`，实现 `create_deep_business_graph(*, skills_dir, name, checkpointer=None)` 工厂
- [ ] 4.2 工厂内部把现有 3 个 DeepAgents 图的 `_build_middleware` / `_business_tool_names` / `_mcp_tool_names` 等辅助函数下沉到 `factory.py`，消除 3 个图的复制粘贴
- [ ] 4.3 验证 `create_deep_business_graph(checkpointer=None)` 不抛 TypeError；写单测 `tests/unit_tests/test_deep_business_graph_factory.py` 覆盖
- [ ] 4.4 `src/intelligent_report/graph.py` 改为 `graph = create_deep_business_graph(skills_dir=SKILLS_DIR, name="intelligent-report", checkpointer=None)`
- [ ] 4.5 `src/deep_research/graph.py` 同样改造
- [ ] 4.6 `src/intelligent_tracing/graph.py` 同样改造
- [ ] 4.7 单元测试 3 个图相关 case 适配新结构（如有）

## 5. 统一 langgraph-api 加载入口

- [ ] 5.1 新建 `src/langgraph_entry/basic_qa.py`、`intelligent_analysis.py`、`data_analysis.py`、`intelligent_report.py`、`deep_research.py`、`intelligent_tracing.py`（6 个文件）
- [ ] 5.2 每个入口文件仅做一行 `from src.<name>.graph import graph`（或调用工厂返回 graph 变量）
- [ ] 5.3 `langgraph.json` 的 `graphs` 路径从 `./src/<name>/graph.py:graph` 改为 `./src/langgraph_entry/<name>.py:graph`
- [ ] 5.4 启动 `langgraph dev --config ./langgraph.json --no-browser`，确认 6 个图全部成功 import
- [ ] 5.5 验证 `curl http://localhost:2024/assistants` 返回 6 个 assistant

## 6. 重写前端 `app.js`

- [ ] 6.1 在 `src/web/static/app.js` 顶部注入 `const LANGGRAPH_API_URL = window.LANGGRAPH_API_URL || "http://localhost:2024";`
- [ ] 6.2 重写 `apiListGraphs`：调 `POST ${LANGGRAPH_API_URL}/assistants/search` 解析 `graph_id` 字段
- [ ] 6.3 重写 `apiListThreads`：调 `POST ${LANGGRAPH_API_URL}/threads/search` 携带 `metadata.graph_id` 过滤
- [ ] 6.4 重写 `apiCreateThread`：调 `POST ${LANGGRAPH_API_URL}/threads` 返回 `thread_id`
- [ ] 6.5 重写 `apiRenameThread` / `apiDeleteThread`：调 `PATCH /threads/{id}` 与 `DELETE /threads/{id}`
- [ ] 6.6 重写 `apiGetHistory`：调 `GET ${LANGGRAPH_API_URL}/threads/{id}/state` 解析 `values.messages`
- [ ] 6.7 重写流式消息发送：抽离 `parseSSEStream(readableStream, handlers)` 工具函数
- [ ] 6.8 用 `fetch + ReadableStream` 替代 `EventSource` 调用 `POST /threads/{id}/runs/stream`
- [ ] 6.9 SSE 事件类型映射：`message` → 文本 token；`tool_calls` → 工具调用进度；`custom` → rich_output / expanded_questions / final_output 透传
- [ ] 6.10 在 `src/web/static/index.html` 的 `<head>` 中添加 `<script>window.LANGGRAPH_API_URL = "..."</script>` 默认配置
- [ ] 6.11 grep 验证：`grep -rn "/api/" src/web/static/` 应无任何 fetch 路径残留

## 7. 新建本地启动入口

- [ ] 7.1 新建 `scripts/serve_static.py`：基于 `http.server.SimpleHTTPRequestHandler` 子类，添加 CORS 头（`Access-Control-Allow-Origin: *`），监听 8125 端口，托管 `src/web/static/`
- [ ] 7.2 新建 `run-local.sh`：检查 `.env` / `.venv` / SQLite 父目录；启动 `langgraph dev --config ./langgraph.json --no-browser`（后台，PID 写入 `/tmp/air-agent-langgraph.pid`，日志写入 `/tmp/air-agent-langgraph.log`）
- [ ] 7.3 `run-local.sh` 同时后台启动 `scripts/serve_static.py`（PID 写入 `/tmp/air-agent-static.pid`）
- [ ] 7.4 `run-local.sh` 加 readiness probe：每 5 秒 curl `http://localhost:2024/assistants`，连续 3 次 200 视为就绪
- [ ] 7.5 新建 `stop-local.sh` 配套 stop 脚本

## 8. 清理旧入口

- [ ] 8.1 确认 `src/web/server.py` 已不再被任何代码 import 后删除该文件
- [ ] 8.2 删除 `run.sh`
- [ ] 8.3 保留 `src/common/config/checkpointing.py` 与 `get_checkpointer()` 函数（供单测与未来自建入口使用）
- [ ] 8.4 保留 `db/checkpoints.db` 与 `db/.gitkeep`（历史数据不丢）

## 9. 验证

- [ ] 9.1 跑 `make test` 全绿；如有 6+ 个图相关单测失败，按失败原因修复（注意图签名变更）
- [ ] 9.2 启动 `run-local.sh`，在浏览器（手动）打开 `http://localhost:8125/`，验证 6 个图都能发起对话
- [ ] 9.3 端到端冒烟：`curl POST /threads/{id}/runs/stream` 验证 SSE 流正常返回 `message` / `end` 事件
- [ ] 9.4 验证 custom 事件（rich_output / expanded_questions）透传正常
- [ ] 9.5 验证 SQLite checkpointer 仍能写入：在单测中调用 `get_checkpointer()` 写入一个虚拟 state，确认 `db/checkpoints.db` 文件 mtime 更新
- [ ] 9.6 验证 CORS：浏览器从 `localhost:8125` 请求 `localhost:2024` 不报 CORS 错误
- [ ] 9.7 grep 验证：`grep -rn "/api/" src/web/static/` 无残留

## 10. 文档更新

- [ ] 10.1 更新 `README.md`：本地启动方式从 `bash run.sh` 改为 `bash run-local.sh`；说明新端口（langgraph-api 2024、前端 8125）
- [ ] 10.2 更新 `AGENTS.md`：本地开发流程章节同步
- [ ] 10.3 更新 `CLAUDE.md`（如有）项目结构说明
- [ ] 10.4 在 `AGENTS.md` 注释：`src/web/server.py` 与 `run.sh` 已废弃，新代码统一走 `langgraph dev`
- [ ] 10.5 在 `docs/` 目录下新增 `迁移至 LangGraph API 实施记录_<日期>.md` 记录本次改造细节
