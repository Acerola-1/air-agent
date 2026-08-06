# 迁移至 LangGraph API 实施记录

> 实施日期：2026-08-06
> 对应 OpenSpec change: `migrate-to-langgraph-api`

## 目标

让项目在本地开发场景下运行于 langgraph-api 标准协议：
- 6 个业务图可被 `langgraph dev` 平台加载并对外暴露 `/threads` / `/threads/{id}/runs/stream` 等标准端点
- 前端通过 langgraph-api 标准 API 协议调用（替代自建 FastAPI 自定义协议）
- 删除 `src/web/server.py` 与 `run.sh`，统一通过 `run-local.sh` 启动

Docker / Apple Container 部署作为独立后续 change 处理（不在本次范围内）。

## 实施概览

| 阶段 | 任务 | 状态 |
|---|---|---|
| 1 | 升级 langgraph 三件套 (cli/api/runtime-inmem) | 完成 |
| 2 | 重构 `build_business_graph` 接受 `checkpointer` 参数 | 完成 |
| 3 | 重构 `data_analysis.build_graph` 接受 `checkpointer` 参数 | 完成 |
| 4 | 解除 3 个 DeepAgents 图的 checkpointer 硬编码（保持各自独立实现） | 完成 |
| 5 | 启动与验证（run-local.sh 端到端） | 完成 |
| 6 | 重写前端 `app.js` 适配 langgraph-api 标准 API | 完成 |
| 7 | 新建 `run-local.sh` / `stop-local.sh` / `scripts/serve_static.py` | 完成 |
| 8 | 删除 `src/web/server.py` 与 `run.sh` | 完成 |
| 9 | 验证（单测 / 端到端冒烟） | 完成 |

## 关键改动

### 1. 依赖升级

```
langgraph-cli:          0.4.19  → 0.4.31
langgraph-api:          0.7.98  → 0.12.0  (跨 5 个 minor 版本)
langgraph-runtime-inmem: 0.27.3  → 0.32.0
langgraph-checkpoint:    4.1.0   → 4.1.1
langgraph-checkpoint-sqlite: 新增 3.1.1
opentelemetry-* 锁定 1.37.0 + 0.58b0  (与 prometheus 0.58b0 配套)
```

升级过程中遇到的核心矛盾：
- `langgraph-api 0.12.0` 要求 `opentelemetry-exporter-prometheus<0.59`
- `opentelemetry-exporter-prometheus 0.58b0` 锁定 `opentelemetry-sdk<1.38`
- `opentelemetry-instrumentation 0.57b0+`（项目用）需要 `opentelemetry-sdk>=1.38`

**解决方案**：移除未使用的 `traceloop-sdk`（它是 OpenLLMetry 的入口，项目代码未引用），连带清理 38 个 `opentelemetry-instrumentation-*` 孤儿包。opentelemetry 生态全部降到 1.37.0 配套。

### 2. Graph 工厂化（核心改造）

6 个图原本在模块级硬编码 `get_checkpointer()`：

```python
# 改前
graph = build_business_graph(skills_dir=..., name=...)
# build_business_graph 内部: builder.compile(checkpointer=get_checkpointer())
```

改为可注入模式：

```python
# 改后
def build_business_graph(*, skills_dir, name, checkpointer: Any = None):
    return builder.compile(checkpointer=checkpointer)

# basic_qa/graph.py
graph = build_business_graph(skills_dir=..., name="basic-qa", checkpointer=None)
```

`checkpointer=None` 时图不带 checkpointer，langgraph-api 平台在加载时自动注入持久化后端。

### 3. 3 个 DeepAgents 图的 checkpointer 解除（保持独立实现）

> **设计选择**：intelligent_report / deep_research / intelligent_tracing 当前是空业务占位，
> 未来三个图的业务逻辑、模型、中间件很可能完全不同（智能报告 vs 深度研究 vs 污染溯源，
> 各自的 prompt / subagent / 工具都不一样）。**不抽离共享工厂**，让每个图保留独立实现，
> 后续各图能自由生长。本阶段只做最小改动：把 `checkpointer=get_checkpointer()` 改为
> `checkpointer=None`，消除 langgraph-api 的 import 错误。

```python
# src/intelligent_report/graph.py (改后保持原 ~100 行, 仅改最后一行)
graph = create_deep_agent(
    model=ModelRegistry.deepseek_v4_flash,
    tools=[find_skill],
    system_prompt=with_main_agent_tool_use_output_guard(SYSTEM_PROMPT),
    middleware=_build_middleware(_business_tools),
    backend=CompositeBackend(...),
    checkpointer=None,           # ← 仅这一行: 原来是 get_checkpointer()
    name="intelligent-report",
)
```

同理改 `src/deep_research/graph.py` 与 `src/intelligent_tracing/graph.py`，
每个图保留自己的 `SKILLS_DIR`、`SYSTEM_PROMPT`、middleware 链、subagent 配置空间。
### 4. 启动与验证

```bash
$ ./run-local.sh
✓ 就绪: 6 个 assistant 已注册

$ curl -s -X POST http://localhost:2024/assistants/search \
    -H "Content-Type: application/json" -d '{}' \
    | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
6
```

`langgraph.json` 的 `graphs` 配置直接指向 `./src/<name>/graph.py:graph`，
**没有中间包装层**——langgraph-api 平台本身已经做了"业务图 → 平台入口"的桥接，
不需要再叠一层 pass-through 文件。
from basic_qa.graph import graph
```

把"平台加载点"与"业务图实现"分离，未来想替换入口机制不影响业务图本身。

### 5. 前端 API 客户端

| 旧协议 | 新协议 (langgraph-api) |
|---|---|
| `GET /api/graphs` | `POST /assistants/search` |
| `GET /api/threads?graph_id=` | `POST /threads/search` body `{metadata: {graph_id}}` |
| `POST /api/threads` | `POST /threads` body `{metadata: {graph_id, title}}` |
| `PATCH /api/threads/{id}` | `PATCH /threads/{id}` body `{metadata: {title}}` |
| `DELETE /api/threads/{id}` | `DELETE /threads/{id}` |
| `GET /api/threads/{id}/history` | `GET /threads/{id}/state` 解析 `values.messages` |
| `POST /api/threads/{id}/runs/stream` | `POST /threads/{id}/runs/stream` body `{assistant_id, input, stream_mode}` |

SSE 事件格式从自定义 `{type: "text|tool_call|done", content: ...}` 改为 langgraph-api 标准：
- `event: metadata` —— 首块元数据
- `event: values` —— 状态快照
- `event: messages` —— LLM token chunks `[AIMessageChunk, metadata]`
- `event: end` —— 流结束

`grep -rn "/api/" src/web/static/` 验证无残留。

### 6. 本地启动入口

`run-local.sh` 替代 `run.sh`：
- 后台启动 `langgraph dev --config ./langgraph.json --no-browser --port 2024` (PID 写到 `/tmp/air-agent-langgraph.pid`)
- 后台启动 `scripts/serve_static.py --port 8125` 托管 `src/web/static/` + CORS (PID 写到 `/tmp/air-agent-static.pid`)
- 30 秒内 readiness probe：等 `POST /assistants/search` 返回 6 个 assistant

`scripts/serve_static.py` 基于 `http.server.SimpleHTTPRequestHandler` 子类：
- CORS 头：`Access-Control-Allow-Origin: *`（本地 dev 无安全风险）
- 默认托管 `src/web/static/`，可由 `--dir` 覆盖

## 验证结果

### 端到端

```bash
$ ./run-local.sh
✓ 就绪: 6 个 assistant 已注册

$ curl -s -X POST http://localhost:2024/assistants/search \
    -H "Content-Type: application/json" -d '{}' \
    | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
6
```

### 单元测试

| 阶段 | 失败数 | 通过数 |
|---|---|---|
| 改前 baseline | 38 | 231 |
| 改后（含同步更新 `test_business_graph_config` 与 `test_langgraph_business_graphs`） | 38 | 231 |

失败数量与基线一致，**未引入新失败**。剩余 38 个失败均为预先存在（`test_business_graph_nodes` 引用未实现的 `_route_after_permission`，`test_vanna_visualization` 引用不存在的 `vanna_sql_adapter` 等），与本次改造无关。

### 端口分配

| 端口 | 服务 | 进程 |
|---|---|---|
| 2024 | langgraph-api (inmem) | `langgraph dev` |
| 8125 | 静态前端 + CORS | `scripts/serve_static.py` |

## 已知限制

1. **`get_checkpointer()` 仍保留**：作为本地 SQLite 持久化入口（供单测与未来自建入口使用），不删除。
2. **`traceloop-sdk` 被移除**：它原本是 OpenLLMetry 自动埋点入口，但项目代码未直接引用，移除后 OpenLLMetry 自动追踪功能失效。如未来需要追踪 LLM 调用，需手动接入 langsmith SDK（已配置在 `.env`）。
3. **Custom 事件（rich_output / expanded_questions / final_output_delta）暂不透传**：`app.js` 的 `handleSSEEvent` 预留了 custom 通道，但当前 `stream_mode` 未加 `"custom"`。如需透传业务自定义事件，需在 `send()` 的 `stream_mode` 改为 `["messages-tuple", "values", "custom"]` 并实现 custom 块的事件分发。
4. **Docker / Apple Container 部署未在本次实施**：作为独立后续 change。当前 Docker 镜像配置 (`Dockerfile` + `docker-compose-db.yaml`) 仍基于 `langgraph-api 0.12.0`，理论上兼容但未在 Docker 容器中实际验证。

## 文件清单

### 新增

- `scripts/serve_static.py`
- `run-local.sh`
- `stop-local.sh`

### 修改

- `requirements.lock.txt` —— langgraph 平台三件套升级 + opentelemetry 锁定 1.37.0
- `src/common/business_graph/builder.py` —— `build_business_graph(checkpointer=None)`
- `src/basic_qa/graph.py` —— 显式 `checkpointer=None`
- `src/intelligent_analysis/graph.py` —— 显式 `checkpointer=None`
- `src/data_analysis/graph.py` —— `build_graph(checkpointer=None)`
- `src/intelligent_report/graph.py` —— `checkpointer=get_checkpointer()` → `checkpointer=None`（保持独立 ~100 行）
- `src/deep_research/graph.py` —— 同上
- `src/intelligent_tracing/graph.py` —— 同上
- `src/web/static/app.js` —— API 客户端 + SSE 解析切到 langgraph-api 标准协议
- `src/web/static/index.html` —— 注入 `LANGGRAPH_API_URL` 配置
- `tests/unit_tests/test_business_graph_config.py` —— 加 `checkpointer=None` / `get_checkpointer not in source` 断言
- `README.md` —— 启动方式改为 `run-local.sh`

### 删除

- `src/web/server.py` (17KB 自建 FastAPI)
- `run.sh` (旧启动脚本)
- `src/web/__pycache__/server.cpython-313.pyc`
- `traceloop-sdk` 及其 38 个 `opentelemetry-instrumentation-*` 依赖

### 保留

- `src/common/config/checkpointing.py` + `get_checkpointer()` —— 单测与未来入口
- `db/checkpoints.db` —— 历史 SQLite 持久化数据
