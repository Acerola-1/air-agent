# DeepAgents 0.7.28 升级设计

## 设计基线更新（2026-10-19 复核）

当前 `dev` HEAD 已推进到 `6ff95dd`，`langgraph.json` 只保留三图：basic-qa、data-analysis、intelligent-tracing；旧六图、旧模型网关地址和旧临时库仅是 2026-10-18 的历史记录。本文以下“六图”描述属于旧基线，必须按原文保留以解释当时的实测、端口和事故；当前验收以三图和 `tasks.md` 第 5 节为准。

复核同时确认：`/tmp/air-agent-upgrade` 的 `pyproject.toml` 仍声明 `deepagents>=0.7.5,<1.0`，只有 `uv.lock` 解析到 0.7.28。该声明缺口不是已完成项，G1 前必须收紧声明、重新锁并在新 worktree 补验三图。

## Context

- 基线：`dev` 分支 HEAD `8e172de`；主工作区有 25 个未提交改动（I2/I4 身份任务），升级不得覆盖。
- 当前锁：deepagents 0.7.5，langchain 1.3.15，langchain-core 1.5.4，langgraph 1.2.11，langgraph-checkpoint 4.1.1，langsmith 0.10.15，langchain-quickjs 0.3.5。
- 目标版本：deepagents 0.7.28，langchain 1.4.6，langchain-core 1.6.12，langgraph 1.2.16，langgraph-checkpoint 4.3.0，langsmith 0.14.11，langchain-quickjs 0.3.9。
- 运行镜像：`FROM langchain/langgraph-api:3.13`（未固定 digest），容器内 `langgraph-api 0.15.1`。
- 六个公开图以 `langgraph.json` 为准：basic-qa、intelligent-analysis、data-analysis、intelligent-report、deep-research、intelligent-tracing。

## Goals / Non-Goals

**Goals**

1. 证明升级后六个图可以加载、编译并在官方镜像中注册。
2. 证明空库启动时 checkpoint/store 迁移可完成，鉴权失败关闭、线程归属与台账受理边界不被破坏。
3. 量化单测回归：升级前后的失败集合必须一致，不引入新失败。
4. 给出可执行的回滚路径与未验证项清单。

**Non-Goals**

- 不改造 `intelligent-tracing` 的业务逻辑，不引入 SubAgent、Skill 或新工具。
- 不调整 MCP 权限、事件契约、前端交互或输出格式。
- 不接管共享数据库、不迁移真实用户数据。
- 不把 0.7.28 的实验能力（`mode="fork"`、`skill include_tools`）直接用于业务图；仅记录为后续评估项。

## Decisions

### D1. 在独立 worktree 升级，主工作区只落 OpenSpec 文档

主工作区有未提交的身份任务改动。升级在 `/tmp/air-agent-upgrade`（HEAD `8e172de` 的独立 worktree）完成；主工作区只新增本 change 文档和此前已批准的探针脚本。这样 lock 变更与身份任务互不污染，也便于整体丢弃回滚。

### D2. 以 `uv lock --upgrade-package deepagents --python 3.13` 为唯一锁定方式

仓库没有 `.python-version`，uv 默认会选到系统 3.14；不显式指定 3.13 会生成与 Docker 镜像不一致的锁。锁定后必须用 `uv sync --frozen --all-extras --python 3.13` 安装并验证，不能用 `uv add` 绕过锁。

### D3. quickjs 下限对齐到 0.3.9

deepagents 0.7.28 的 `quickjs` extra 要求 `langchain-quickjs>=0.3.9`；0.3.5 虽然能被 resolver 接受，但 `CodeInterpreterMiddleware` 的运行行为未验证。把下限提到 0.3.9，并在任务中保留运行时验证项。

### D4. 容器验证使用隔离栈，不碰共享栈

自建 `air-agent-upgrade` 项目：独立 Postgres（25432）、Redis、API（22024）、独立卷；身份库另建 `air_agent_tenant_test`，不读写共享业务库。共享栈只做健康检查。

### D5. 升级与业务改造分开评审

本 change 只含依赖基线、镜像固定、验收证据。溯源多 Agent 设计与 spike 另立 change，避免“框架问题”和“业务问题”混在一个 diff 中无法归因。

## 兼容性证据（2026-10-18 实测）

| 检查 | 结果 |
|---|---|
| 六图导入/编译 | 全部成功（basic-qa、intelligent-analysis、data-analysis、intelligent-report、deep-research、intelligent-tracing） |
| 仓库使用的 deepagents 符号 | `create_deep_agent`、`CompositeBackend`、`StateBackend`、`GENERAL_PURPOSE_SUBAGENT`、`SummarizationMiddleware`、`DEEPAGENTS_DEFAULT_SUMMARY_PROMPT`、`compute_summarization_defaults` 全部存在 |
| SubAgent API | 向后兼容；`system_prompt` 变可选，新增 `mode="isolated"/"fork"`（fork 为 beta） |
| 多 Agent 形态 | 声明式专员 + `response_format`、`mode="fork"`、`CompiledSubAgent` 三种均可编译 |
| 全量单测 | 升级：30 failed / 414 passed / 51 skipped；基线：30 failed / 413 passed / 51 skipped；失败集合一致，唯一差异是既存 flaky `test_total_deadline_discards_late_success` |
| 容器构建 | 镜像 `air-agent/langgraph-api:deepagents-0.7.28` 构建成功，manifest `18cccdd5feeb` |
| API 启动 | `/ok` 200，启动 15.9s；六图全部注册（日志 `graph_id`） |
| Checkpointer | 空库 `schema_migrations` 1–13 全部应用，checkpoint/store/thread/run 表创建成功 |
| 鉴权 | 未认证与伪造票据访问 `/assistants` 均 401；有效票据 `/assistants/search` 200，返回 6 个 assistant |
| 线程归属 | 显式 thread_id 创建 200 → `claim-confirmation` 返回 `confirmed:true` → 回读 200 |
| 台账与运行 | `admit_request` 生成 request/submission/attempt 与能力选择快照；带预留创建运行 200，run_id 生成 |
| 模型调用 | 失败于 `openai.APIConnectionError`；宿主 `192.168.64.1:8787` 无监听，非升级问题 |

## Risks / Trade-offs

1. **`httpx2` 新依赖**：`langsmith 0.14.11` 引入 `httpx2`/`httpcore2`。现有出网守卫与测试 mock 基于 `httpx`，不覆盖新客户端；需要供应链审查与实际追踪验证。
2. **checkpoint 存量库升级未验证**：本次只在空库验证了 1–13 迁移；真实库从 4.1.1 升到 4.3.0 的路径必须在库副本上演练。
3. **quickjs 运行时未验证**：只验证导入与图编译；`execute` 代码解释器需要单独冒烟。
4. **基础镜像未固定 digest**：`FROM langchain/langgraph-api:3.13` 只锁 tag；重建可能引入新的基础版本，评估期必须固定 digest。
5. **锁格式 revision 3 → 5**：团队 uv 版本需 ≥0.13；旧 uv 可能拒绝读取。
6. **既存 flaky 测试**：`test_total_deadline_discards_late_success` 在两个环境都偶发失败，回归比较时必须按失败集合而不是单次计数。
7. **图谱导入耗时告警**：basic-qa 导入约 4.1s，触发平台“导入过慢”告警；这是既存行为，不是升级引入，但值得单列优化项。

## 实施事故与善后

1. **`container-compose down` 连带停止共享 API**：container-compose 1.1.0 的 `name` 只影响容器命名，项目级隔离不完整。已用 `container-compose up -d --profile prod` 恢复共享栈，`/ok` 200。后续临时栈只能用 `container stop/rm` 处理自建容器。
2. **误执行 `admin/api/scripts/seed.py`**：该脚本没有参数解析，`--help` 会直接执行，且未显式指定 `DATABASE_URI` 时落到默认共享库。影响：platform `role_permissions` 删除重插（内容一致，5344 行）；为缺失企业补齐 `tenant_admin` 模板（tenant scope 角色 195 个，最新 13:48Z）；root 密码、平台角色、账号授权未变。需要 owner 复核后再决定是否回退。

## Migration Plan

- 升级路径：修改 `pyproject.toml` 下限 → `uv lock --python 3.13` → `uv sync --frozen --all-extras` → 六图导入/编译 → 单测对比 → 构建镜像 → 隔离栈验收。
- 回滚路径：保留 0.7.5 的 `uv.lock`；回滚时 `uv sync --frozen` 并重建镜像。数据库若已应用 checkpoint 4.3.0 迁移，回滚前必须在库副本验证 4.1.1 的兼容性，不能直接降级生产库。
- 上线顺序：镜像重建 → 空库/存量库副本迁移演练 → 灰度一个非关键图 → 六图健康检查。

## Open Questions

1. 真实模型运行需要宿主 LLM 代理 `192.168.64.1:8787` 可用；当前未监听。
2. 基础镜像应固定到哪个 digest，需要确认当前生产镜像的实际 digest。
3. 共享库 seed 事故是否需要回退 tenant_admin 模板，由库 owner 决定。
4. `httpx2` 的出网白名单与观测策略，需要与现有 outbound guard 对齐。
