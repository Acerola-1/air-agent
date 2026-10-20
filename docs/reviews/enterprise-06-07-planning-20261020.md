# Change 6/7 规划交付与溯源影响核对

日期2026-10-20；核对起点`dev@baa1b24`，开始时工作区干净。**本轮只有规划，不是功能验收。**

## 与溯源集成的关系

读取了`integrate-intelligent-tracing-into-dev`的proposal/design/tasks和四份spec。它负责DeepAgents/镜像升级、新版智能溯源、真实MCP数据、ValidatedReport、台账发布门禁，尚有61项任务未实施。6/7不等待其业务算法与数据源，后台查询和原问题分析可独立开发。

存在需协调的工程接缝：6新增受控终答快照需要共用收尾回调的一处小接线；迁移head、root依赖锁、router注册、删除hook和公共文档不能并发改写。不能给出“完全无影响”的结论。结论是**不阻塞本轮计划，独立工作区可并行实施，共享接线串行，最终新锁/三图组合再验收**。具体文件责任见[并行契约](../handoffs/parallel-tracing-console-insights.md)。原溯源计划未改动。

## 新交付

| 计划 | 制品 | 待实施任务 |
|---|---|---|
| [6 运营后台](../../openspec/changes/add-agent-admin-console/proposal.md) | proposal/design、3份spec、tasks、API、权限交接、18项验收矩阵、handoff/goal | 30，已实施0 |
| [7 需求洞察](../../openspec/changes/add-question-demand-insights/proposal.md) | proposal/design、3份spec、tasks、API、20项验收矩阵、handoff/goal | 29，已实施0 |

6沿用Next后台，账号/企业CRUD归IAM；仅做轻量反馈处理和受控Trace，不另建Langfuse已有评测功能。7使用稳定标准问题/主题、增量向量匹配和SQL统计，不分析模型内部报告来推断用户诉求。7后端可按DTO先做，页面接6公共壳后才能完整验收。

两个新增运营写权限需要IAM目录正常配置交接，不修改身份体系、不按root名自动授权；worker首版使用有期限、实时复查的人类会话委托，过期显式暂停，长期机器身份后续另扩展。首版向量只用自有PG有界检索，不碰RAG向量存储。

## 本轮验证

- 6、7各4/4规划制品齐全，分别`openspec validate <name> --strict --json`无问题。
- 原溯源change严格校验仍通过且文件未改；Agent主规格原13项失败集合不变，没有趁规划修改历史主规格。
- 相对链接、任务未完成标记、范围和`git diff --check`已检查；没有业务代码、依赖锁、数据库、IAM/RAG仓库变更。
- 没有运行产品测试、模型、浏览器或容器；文档校验不能替代这些实施验收。未commit/push/归档。

[机器可读检查](evidence/enterprise-06-07-planning-validation-20261020.json)。目标提示分别在[6](../handoffs/agent-admin-console-goal.md)、[7](../handoffs/question-demand-insights-goal.md)，使用前确认独立工作区包含这些尚未提交的计划文件。
