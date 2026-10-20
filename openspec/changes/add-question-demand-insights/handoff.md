# Change 7 交接入口

按proposal/design、三份spec、api-contract、tasks和acceptance执行。160条样本校准、真实Embedding/辅助模型、有限会话委托、RLS、来源删除/恢复、版本化维护和实际后台页面均是完整交付条件，不只有聚类脚本。

通用目标提示：[question-demand-insights-goal.md](../../../docs/handoffs/question-demand-insights-goal.md)。并行约束见[并行契约](../../../docs/handoffs/parallel-tracing-console-insights.md)；公共DTO与精确权限看`../add-agent-admin-console/{api-contract,permission-handoff}.md`。

7后端可以先按契约开发，6未完成时不能假定共享组件已存在，也不能把临时测试页面当最终后台。最终集成顺序6公共壳/DTO→7页面；溯源若升级锁，最终回归记录真实版本。当前只有计划，没有模型/数据处理、迁移、Git提交或生产启用。
