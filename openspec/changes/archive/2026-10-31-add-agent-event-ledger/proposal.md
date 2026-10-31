# Proposal

## Why

当前 SSE 状态、LangGraph 运行和 Langfuse Trace 缺少统一的持久关联，无法可靠统计提问、取消、回答版本及数据覆盖率。先建立租户隔离的事件台账和共享接口，才能让聊天反馈与运行观测由不同实施 Agent 并行开发。

## What Changes

- 新增可信提问、运行尝试、回答版本与 Trace 映射；区别提交重放、重新生成、内部重试和人工恢复。
- 新增版本化事件信封、来源白名单、幂等写入、事务 outbox、投递去重及重放工具。
- 接入六图的真实运行生命周期，保存停止意图与实际终态，补偿断流、进程退出和回调漏报；不以浏览器流结束推断成功。
- 新增有权限控制的本人回读、企业运营明细与数据覆盖率接口；原文与事件元数据分开保存。
- 固定首期指标口径和第 3/5 项使用的扩展契约，提供失败/重复/乱序/跨租户验收样本。
- 为事件迁移、保留、删除和恢复提供可复跑方案；历史未知数据明确标记，禁止用零补齐。
- 不在本项添加点赞按钮、复制埋点、Skill/MCP 选择器、完整观测采集、聚类算法或运营大屏。

## Capabilities

### New Capabilities

- `agent-request-identity`：可信逻辑提问、运行尝试、回答与 Trace 的关联和提交幂等。
- `agent-run-lifecycle`：六图运行状态、终态证据、停止意图和补偿校准。
- `agent-event-delivery`：事件版本/来源、事务 outbox、去重、重放和有界工作进程。
- `tenant-event-access`：台账隔离、分级读取、原文权限、保留与删除。
- `agent-metric-contract`：统计分母、时间、缺失标识和供后续 change 使用的查询契约。

### Modified Capabilities

无。现有 `agent-runtime-performance` 的懒初始化和消息治理保持；本项新增事实台账，不修改其既有需求。

## Impact

- 依赖 `add-tenant-identity-access` 的可信身份、会话上下文、RLS、原生运行授权和迁移结果。协调者已接管补修并放行本项本地实施；以独立报告和冻结工作树指纹核对接口，保留真实MCP和完整矩阵的生产门禁，不能并行修改身份基础。
- 扩展 `admin/api/` 的 Alembic、业务模型/服务/路由及独立台账工作进程；Agent 侧新增独立 `src/common/event_ledger/`，不跨进程导入 `admin/api/app`。
- 接入 `frontend/app/api/[..._path]/route.ts`、`frontend/lib/langgraph-api.ts`、`frontend/lib/chat-store.ts` 的运行关联；保持既有 SSE 字段和取消体验。
- 扩展 LangGraph 受授权入口与六图生命周期适配，不直接查询或改写平台内部表；Trace 映射只是关联接口，完整 Langfuse/usage 采集由第 5 项负责。
- 使用已有 PostgreSQL 与依赖，新增显式表迁移和 worker 启停配置；不默认引入 Kafka、数仓或第三个身份服务。
- 提供 `handoff.md`、通用目标提示及并行工作协议，实施结果保留为“待独立验收”。
