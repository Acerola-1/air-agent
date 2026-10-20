# 智能溯源集成基线（2026-10-20）

本目录是 `openspec/changes/integrate-intelligent-tracing-into-dev` 的验收证据根目录。
本轮只记录 `dev` 基线、依赖升级、tracing 集成与后续 G0/G1/G2 结果；不得覆盖
`docs/reviews/evidence/tracing-pm25-20261019/`、`takeover-03-05/` 等旧报告。

## 1. 分支与提交

| 名称 | 值 |
|---|---|
| 工作分支 | `dev` |
| `dev` HEAD | `baa1b243eb6ff32f7db0536f954bceb03cb53479`（仅文档提交，代码等价 `155214afe89e89dc5a3b647148e55e3c21e74f37`） |
| feature 分支 | `feature/intelligent-tracing-pm25` @ `3cc7bdcdc56e4a584be435e41a4ce38110177a2e` |
| merge-base | `6ff95dd872300f8dfdb8dfcf4a4ab99e63cf5706` |
| `git merge-tree dev feature/...` | 无文本冲突，结果树 `a8ddd90bfa7a56612425de4d11485fae3d2e0584` |

## 2. 公开图清单（以 `langgraph.json` 为准）

```json
{"basic-qa": "./src/basic_qa/graph.py:graph",
 "data-analysis": "./src/data_analysis/graph.py:graph",
 "intelligent-tracing": "./src/intelligent_tracing/graph.py:graph"}
```

`Dockerfile` 的 `LANGSERVE_GRAPHS` 与之一致。历史文档中的“六图”为过期描述，
按本 change 任务 0.3 修正 `AGENTS.md`。

## 3. 运行时版本（升级前，0.7.5 基线）

| 包 | 版本 |
|---|---|
| deepagents | 0.7.5 |
| langchain | 1.3.15 |
| langchain-core | 1.5.4 |
| langgraph | 1.2.11 |
| langgraph-checkpoint | 4.1.1 |
| langgraph-checkpoint-postgres | 3.1.2 |
| langsmith | 0.10.15 |
| langchain-quickjs | 0.3.5 |
| langchain-mcp-adapters | 0.3.2 |

Python 3.13.3，`uv` 0.13.1。

## 4. dev 基线单测（0.7.5）

命令：`.venv/bin/python -m pytest tests/unit_tests -q -p no:cacheprovider --timeout=300`

结果：**7 failed / 465 passed / 73 skipped（38.4s）**，日志见
`pytest-unit-dev-075.txt`。失败集合（升级前既存）：

1. `tests/unit_tests/test_artifact_db.py::test_pg_failure_never_falls_back[asyncio]`
2. `tests/unit_tests/test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`
3. `tests/unit_tests/test_guard_middleware_framework_compat.py::test_real_model_call_sees_platform_identity[asyncio]`
4. `tests/unit_tests/test_knowledge_route.py::test_policy_route_fast_path_skips_llm`
5. `tests/unit_tests/test_knowledge_route.py::test_knowledge_route_fast_path_skips_llm`
6. `tests/unit_tests/test_knowledge_route.py::test_route_cache_reuses_llm_result`
7. `tests/unit_tests/test_knowledge_route.py::test_ambiguous_route_uses_llm_fallback`

说明：`tests/unit_tests/test_graph_import_no_network.py` 会覆写
`docs/reviews/evidence/takeover-03-05/import-network-guard.json`（历史六图内容），
本轮每次全量运行后必须用 `git checkout --` 恢复该历史证据，不把覆盖结果当作新证据。

## 5. 业务授权/MCP 入口（dev 现状）

- `src/common/business_registry.py`：`BUSINESSES` 三条业务，`intelligent-tracing`
  目前登记 `IPP_APPROVED_QUERY_TOOLS` 全量。
- `src/common/mcp_authorization.py`：`resolve_connection` 解析连接与
  `approved_query_tools`。
- `src/common/mcp_scoped.py`：`ScopedMCPToolFactory` 统一发现/执行边界。
- `src/common/identity/runtime.py`：`identity_from_config`、`guard_runtime_call`、
  `RuntimeIdentity.business_code/business_policy_version/business_permission/business_graph`。
- `src/common/event_ledger/store.py`：`attempt_reservation`、`thread_deletion_exists`、
  `LedgerScope`。

## 6. feature 分支与 dev 的差异性质

- 无同名文件文本冲突；差异是运行时语义：feature 依赖 deepagents 0.7.28 的中间件/
  子图签名，dev 为 0.7.5（tracing 测试在 0.7.5 下 8 failed / 74 passed）。
- feature 默认图入口使用 `UnavailableDataSource` + 空 `station_catalog`，
  且授权字段使用旧 `capability_snapshot_ref` 语义；两者都必须按本 change 改造。
- feature 携带 `openspec/changes/add-intelligent-tracing-multiagent/` 与
  `upgrade-deepagents-0.7.28/` 计划文档；合并会一并进入集成分支，属于保留的
  来源计划，不作为本 change 的完成依据。

## 7. 本机真实服务（复核时间 2026-10-20 11:23）

- `deployment/run_separated_local.py status`：IAM、admin API/Web、chat Web、RAG、
  langgraph（HTTP 200）均就绪。
- 端口：15432（业务 PG）开放；19100（IAM）HTTP 200；3080 返回 401（需鉴权）。
- 容器：`air-agent-langgraph-api`、`air-agent-postgres`、`air-agent-redis`、
  `iam-pg17`、`iam-redis7` 均 running。

真实服务可用性不等于 G1/G2 已执行；后续按任务逐项记录实际命令与结果。
