> 2026-10-19 复核：当前 `dev` 已收敛为三图（basic-qa、data-analysis、intelligent-tracing）。本文下方“六图”描述是 2026-10-18 的历史记录，保留用于溯源；当前验收计划见 `tasks.md` 第 5 节。升级 worktree 的 `pyproject.toml` 仍声明 `deepagents>=0.7.5,<1.0`，只是锁解析到 0.7.28，该缺口未完成。

## Why

仓库当前锁定 `deepagents 0.7.5`（2026-08-06 发布），PyPI 最新为 `0.7.28`（2026-10-16），10 周内 23 个 patch。`intelligent-tracing` 下一步要评估多 SubAgent、结构化返回与 `CompiledSubAgent`，需要先确认运行时基线是否值得升级；否则等于用旧框架给新框架下结论。

升级涉及的不只是 deepagents：0.7.28 要求 `langchain>=1.4.6`、`langchain-core>=1.6.11`，会级联升级 langgraph、checkpoint、langsmith，并新增 `httpx2`。这些变化必须独立评估、独立回滚，不能和溯源业务改造混在同一个 diff。

## What Changes

- 在独立 worktree 用 Python 3.13 重新锁定依赖：deepagents 0.7.5 → 0.7.28，langchain 1.3.15 → 1.4.6，langchain-core 1.5.4 → 1.6.12，langgraph 1.2.11 → 1.2.16，langgraph-checkpoint 4.1.1 → 4.3.0，langsmith 0.10.15 → 0.14.11。
- 将 `langchain-quickjs` 下限从 `0.3.5` 提到 `0.3.9`，与 deepagents 0.7.28 的 quickjs extra 对齐。
- 构建独立镜像 `air-agent/langgraph-api:deepagents-0.7.28`，在隔离 Postgres/Redis/端口/卷上验证：API 启动、六图注册、checkpoint 迁移、鉴权失败关闭、线程归属、台账受理与运行创建。
- 记录新增 `httpx2` 的供应链与出网审查项；固定基础镜像 digest；准备回到 0.7.5 的回滚路径。
- **不修改**任何业务图、Skill、提示词、MCP 权限语义或事件契约；本 change 只改依赖基线与验收证据。

## Capabilities

### New Capabilities

- `runtime-dependency-baseline`: Agent 运行时依赖锁、镜像构建、六图加载、容器启动、checkpoint 迁移、鉴权与运行准入基线的可复现要求。

### Modified Capabilities

无。本 change 不改变现有业务规格，只新增运行时基线约束。

## Impact

- `pyproject.toml`：`langchain-quickjs` 下限 0.3.9
- `uv.lock`：12 个包升级、新增 `httpx2`/`httpcore2`，锁格式 revision 3 → 5
- `Dockerfile`：基础镜像需从 tag 固定为 digest（待办）
- 运行与部署：官方 `langgraph-api` 镜像内的 langchain/langgraph 版本随锁变化
- 观测与出网：langsmith 0.14.11 使用新的 `httpx2` 客户端，现有基于 `httpx` 的出网守卫不覆盖
- 数据库：checkpoint 4.3.0 在空库验证通过；存量库升级路径仍需演练
