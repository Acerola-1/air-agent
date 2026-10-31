# 热门问题与用户诉求分析（Change 7）

## Why

运行量与反馈能说明使用情况，却不能说明用户反复提出什么需求。需要把同企业内表达不同、意图相同的问题持续归入稳定标准问题与主题，以真实统计决定业务、知识和产品改进优先级。

## What Changes

- 在聊天热路径外，按已确认受理的逻辑提问增量读取受控问题快照，脱敏、必要的上下文补全和多意图拆分；一问多意图与一问多次运行分开。
- 按企业与分类版本进行向量候选检索、意图/实体约束复核，保存稳定问题簇；不按天把全部问题重新交给 LLM 聚类。低置信度进入未归类池。
- 实现标准问题与主题两层、当日/近7/30业务日、独立用户、增长、新出现需求和有证据的体验问题关联；不把未评价认定为未解决。
- 提供问题簇详情、授权来源样本、未归类处理、合并/拆分/改名与版本化重算；模型摘要只引用确定统计，不冒充真实原因。
- 异步分析任务支持有界处理、幂等、租约恢复、周期增量扫描和明确的授权/模型故障状态；向量、标签、代表样本随源数据删除/到期失效。
- 在 Change 6 后台扩展 `/operations/insights`，共用查询/权限/状态组件；本项不修改 Agent 图、MCP、LangGraph依赖锁或共享知识库向量存储。

## Capabilities

### New Capabilities

- `question-demand-classification`：受控问题标准化、原子意图、增量向量归类与稳定簇。
- `question-demand-statistics`：标准问题/主题榜单、窗口/增长/独立用户与证据覆盖。
- `question-demand-governance`：分析作业、人工纠错、版本、隐私删除和租户隔离。

### Modified Capabilities

无。复用事件台账、原文保留/删除及反馈口径，本项不新增公共事件类型，不把质量评测平台纳入范围。

## Impact

- 新增 `admin/api/app/demand_insights/**`、`admin/api/app/routers/demand_insights.py`、独立 worker 入口和本域迁移/测试；不在 `src/common` 中实现后台问题分析。
- 新增 `admin/web/app/(dashboard)/operations/insights/**` 与本域组件，使用 Change 6 的 UI/DTO 契约。后台壳集成依赖6，分类/统计后端可先按已冻结契约开发。
- 新增 `agent:insights:manage` 精确权限，后台服务重用现有会话桥和实时 IAM 内省；IAM 目录配置交接独立，不更换身份体系。
- 首版向量保存于 Agent 自有 PG 表，使用有界精确检索，不要求 pgvector/Qdrant，也不访问 RAG 的存储；达到规模门槛后另行设计索引。
- 与溯源集成只共享事实边界和集成接线；详情见 [并行契约](../../../docs/handoffs/parallel-tracing-console-insights.md)。本轮只有规划，不执行模型、迁移或实现。
