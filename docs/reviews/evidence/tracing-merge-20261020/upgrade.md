# Phase 0：依赖升级与运行时基线（2026-10-20）

范围：`openspec/changes/integrate-intelligent-tracing-into-dev` 任务 1.1–1.5。以下结果均在本机
`dev` 集成分支 `feature/intelligent-tracing-dev-merge` 与自建临时资源上取得。

## 1. 声明与锁

| 项 | 结果 |
|---|---|
| `pyproject.toml` | `deepagents>=0.7.28,<0.8`、`langchain-quickjs>=0.3.9,<1.0`、`langchain>=1.4.6`、`langchain-core>=1.6.11`、`langgraph>=1.2.16` |
| 锁生成 | `uv lock --python 3.13`（uv 0.13.1，Python 3.13.3） |
| 安装 | `uv sync --frozen --all-extras --python 3.13` 成功 |
| `uv.lock` sha256 | `0cb0b87e7c67cc49d138974c03b8acda8f4b682b77f4a05966ca6407d4100ec9` |
| `pyproject.toml` sha256 | `bee91b0dd63b535c58d6c582c147fc85757ab26b999f9d4f020823f22abc836b` |

实际安装版本（`versions-0728.txt`）：deepagents 0.7.28、langchain 1.4.6、langchain-core 1.6.12、
langgraph 1.2.16、langgraph-checkpoint 4.3.0、langsmith 0.14.11、langchain-quickjs 0.3.9。
`uv sync` 同时移除了未声明的历史手装包（pytest-asyncio、sqlalchemy、mako），测试改用 anyio 插件；
升级后全量单测与升级前对照，未出现相关新增失败。

## 2. 单测基线对比（按失败集合）

| 环境 | 结果 | 失败集合 |
|---|---|---|
| `155214a` + 0.7.5 | 7 failed / 465 passed / 73 skipped | A |
| `155214a` + 0.7.28（独立 worktree `/tmp/air-agent-155214a-0728`） | 7 failed / 465 passed / 73 skipped | A（与 0.7.5 完全一致） |
| 集成分支（merge feature）+ 0.7.28 | 8 failed / 546 passed / 73 skipped | A + `test_total_deadline_discards_late_success` |
| tracing 目录 + 0.7.28 | 82 passed | — |

A（既存失败，两个版本相同）：

1. `test_artifact_db.py::test_pg_failure_never_falls_back[asyncio]`
2. `test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`
3. `test_guard_middleware_framework_compat.py::test_real_model_call_sees_platform_identity[asyncio]`
4. `test_knowledge_route.py::test_policy_route_fast_path_skips_llm`
5. `test_knowledge_route.py::test_knowledge_route_fast_path_skips_llm`
6. `test_knowledge_route.py::test_route_cache_reuses_llm_result`
7. `test_knowledge_route.py::test_ambiguous_route_uses_llm_fallback`

`test_total_deadline_discards_late_success` 单跑连续 3 次通过，属既存 flaky（历史报告已记录），
不计入升级新增失败。原始日志：`pytest-unit-dev-075.txt`、`pytest-unit-155214a-0728.txt`、
`pytest-unit-merge-0728.txt`、`pytest-tracing-0728.txt`。

冷导入：三图导入成功且网络尝试 0（`import-network-guard-0728.txt`）。

## 3. 镜像

- `FROM langchain/langgraph-api:3.13@sha256:c171cf1546cf8af4cfbd01c8d1989f0a91569da81d0c5b1ba9316cdef87f53ab`
  （2026-10-20 解析：linux/arm64 revision `8d6666b`、Python 3.13.16、API 0.16.2）。
- 新 tag：`air-agent/langgraph-api:tracing-merge-20261020`；`docker-compose.yaml` 已改用该 tag，
  不再依赖未固定的 `latest`。
- 基础镜像 `/api/constraints.txt`：OTel 全家桶仍为 1.42.1，与本项目约束一致；
  langgraph `<2`、checkpoint `<5`、langchain-core `>=0.3.66` 均容纳目标锁。
- 构建成功（`image-build-0728.txt`）。

隔离验收（自建 PG/Redis + 新镜像）：

- `/ok` = 200。
- 启动日志注册 `basic-qa`、`data-analysis`、`intelligent-tracing` 三图（`api-startup-3graphs.txt`）。
- 未认证与伪造 Bearer 访问 `/assistants/search` 均为 401（补上可解析的测试票据键后；此前 500
  `ticket keys config invalid` 属测试环境假键，不是镜像缺陷）。

## 4. 迁移

- 空库：`DB_MIGRATION_BY_CORE_API=false` 时 Python 运行时应用 62 条迁移（max version 63），
  `dirty=false`；`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`store`、`thread`、`run`、
  `thread_ttl` 等表存在（`migration-empty-db.txt`）。
- 存量：旧镜像先迁移自建库 → 新镜像接管，`/ok`=200、`dirty=false`、新增迁移 0 条
  （`migration-existing-db.txt`）。
- `DB_MIGRATION_BY_CORE_API=true` 时 Go core 在本镜像中找不到 `storagepostgres/migrations`
  （API 0.16.2 镜像布局），因此生产继续沿用 `DB_MIGRATION_BY_CORE_API=false`；该结论写入镜像说明。
- 回滚限制：旧 `:latest` 镜像内置代码需要已退役环境键，不能直接作为回滚镜像；回滚须按旧锁重建，
  未执行。

## 5. 临时资源

见 `resources.md`。本轮没有 seed/truncate/downgrade 共享正式库，也没有 `compose down` 共享栈。
