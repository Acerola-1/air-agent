# 将智能溯源集成到当前 dev 基线

## Why

`feature/intelligent-tracing-pm25` 已经实现了 PM2.5 的受控溯源内核、四专员、证据门禁和 G0/fixture 验收，但它基于 `6ff95dd` 和旧的能力选择/MCP 权限模型；当前 `dev`（`155214a`）已经收敛为三图，并改成 IAM 业务权限 + 业务注册表 + `approved_query_tools` 双白名单。直接把 feature 合进来会出现三类问题：dev 仍是 deepagents 0.7.5，tracing 测试在 0.7.5 下有 8 个失败；生产图默认没有真实数据源和站点目录；旧 `capability_*` 数据路径没有接 dev 的 `execute_mcp_tool`/业务授权。

本 change 以 `dev` 为唯一基线，把 feature 的实现真正重构/合并进来：先升级运行时依赖，再把 tracing 接到 dev 的真实业务授权、真实 MCP 数据和现有 ledger/发布链路，最终直接把 `intelligent-tracing` 替换成新版，不保留旧通用图。

## What Changes

- **BREAKING**：`intelligent-tracing` 从通用自由 DeepAgents 图改为“冻结请求 + 四专员 + 确定性证据/门禁 + 统一发布”的受控图；不保留旧图、不增加并行公开 Graph ID。
- 将 dev 依赖升级到 deepagents 0.7.28 目标锁（含 langchain 1.4.6、langchain-core 1.6.12、langgraph 1.2.16、checkpoint 4.3.0、langsmith 0.14.11、quickjs 0.3.9），收紧 `pyproject.toml` 下限并重生成 `uv.lock`；同步镜像 digest、容器 tag 和当前三图注册。
- 把 feature 的 `TracingRequest`、四专员、证据注册、claim allowlist、`ValidatedReport` 和发布中间件迁移到 dev；授权字段改绑 `business_code`/`business_policy_version`/`business_permission`/实际 `graph_id`，`capability_snapshot_ref` 只保留为技能/工具选择引用。
- 新增真实 `StationCatalogProvider` 和 `BusinessMCPDataSource`：站点目录、目标站/邻站序列、网格、历史气象、NWP 全部经 `execute_mcp_tool`/`resolve_connection`/`ScopedMCPToolFactory` 执行；不接受裸 executor、浏览器地址或自造凭据。
- 给 `intelligent-tracing` 单独的业务 MCP 工具白名单，明确排除 `mcp_station_analysis_trajectory`、`mcp_station_analysis_single_pscf`、`mcp_station_analysis_history_pscf`；同时在 tracing 执行层保留运行时拒绝作为第二道防线。
- 发布前接现有 event ledger：`guard_runtime_call` + `thread_deletion_exists` + 运行/尝试生命周期读取；`attempt_reservation` 增列或新增只读 `fetch_attempt_lifecycle`，失败关闭；不新增公共事件类型。
- 报告/画布使用正式持久化和现有 artifact 发布链路，不再使用默认 `InMemoryReportStore` 或只返回 `artifact_ref` 的占位发布。
- 验收覆盖六项污染物：PM2.5、PM10、O3、NO2、SO2、CO；O3 日最大 8h/跨日与 CO 单位等口径分别验收，不复用单一 PM2.5 用例。
- 计划本身只修改 OpenSpec 文档；代码实施在后续 apply 阶段完成，最终以 merge commit 把集成分支合入 dev。

## Capabilities

### New Capabilities

- `runtime-dependency-baseline`: 0.7.28 目标依赖锁、当前三图导入/注册、镜像与 checkpoint 迁移基线。
- `pollution-tracing-analysis`: 冻结请求、证据、质控、异常、空间、气象、过程解释、六项污染物口径和受控报告。
- `tracing-multiagent-orchestration`: 四专员、真实业务 MCP 数据源、tracing 专用工具白名单、发布前台账复查和统一发布。

### Modified Capabilities

- `agent-service-connections`: 业务可在通用 `approved_query_tools` 之上声明更窄的 MCP 工具子集；tracing 必须排除轨迹/PSCF，且真实调用仍走统一授权执行层。

## Impact

- 依赖/镜像：`pyproject.toml`、`uv.lock`、`Dockerfile`、`docker-compose.yaml`、`langgraph.json`（保持三图路径）。
- 运行时：`src/intelligent_tracing/**`、`src/common/business_registry.py`、`src/common/mcp_scoped.py`、`src/common/event_ledger/store.py`、`src/common/middleware/tenant_runtime_guard_middleware.py`、`src/common/storage/artifact_db.py`。
- 测试：`tests/unit_tests/tracing/**`、`tests/unit_tests/test_business_registry.py`、`tests/unit_tests/test_event_ledger_*`、`tests/integration_tests/**`、真实 MCP/IAM/浏览器验收证据。
- 运行态：正式 Postgres 持久化/回读、checkpoint 4.1.1→4.3.0 存量迁移、真实模型/MCP/IAM 和浏览器；不改变其他两图的公开行为。
