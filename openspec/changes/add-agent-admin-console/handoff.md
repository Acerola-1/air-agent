# Change 6 交接入口

范围/事实看proposal与design；行为看三份spec；具体API与数据状态看api-contract；新增IAM精确权限看permission-handoff；逐项实施tasks并以acceptance矩阵自验。所有checkbox均为待实施。

通用目标提示：[agent-admin-console-goal.md](../../../docs/handoffs/agent-admin-console-goal.md)。与溯源/7并行时先读[文件责任与集成门禁](../../../docs/handoffs/parallel-tracing-console-insights.md)。不要把只读后台、未采集回答状态或mock页面当成全部交付；新终答真实采集、反馈写权限与真实页面都在范围内。

本轮只交计划，没有改产品、执行迁移、启动模型/服务或Git提交。实施时需从包含本计划的实际共同起点派生，记录真实HEAD。独立验收通过后由用户决定归档及Git操作。

## 2026-10-20 独立验收后的交接

Change6尚未验收通过，具体阻断与修复见 `docs/reviews/agent-admin-console-independent-acceptance-20261020.md`。以下是当前可复用接口，不代表已完成7的集成验证：

- UI沿用现有 `SessionProvider`，从 `lib/operations/hooks.ts` 导入 `useScopeKey` / `useOperationQuery`；载入函数接收AbortSignal，依赖包含窗口、过滤和分页。不得另建身份源或保留跨scope正文缓存。
- `components/operations/QueryToolbar.tsx` 接受 preset/startDate/endDate/businessCode 及其onChange、onRefresh/loading；children可放7的topic/cluster过滤器。通过 `windowQuery` 构建业务日窗口，不能用浏览器本地时区猜界限。
- `MetricState` 接 `{label,metric,format}`，metric的null和not_collected保留；`DataTable<T>` 接caption/columns/rows/rowKey，分页由调用页面负责；`ContentReader` 的loadPage接offset/AbortSignal，只显示纯文本。
- 7使用自己的 `/agent/insights/**` 客户端及DTO；可复用 `Meta` / `Metric` / `ListResponse<T>`，另加analysis_version、assignment_revision等字段，不能把展示版本冒充分类版本。
- 后端窗口/游标语义参考console scope，但7不得导入console router或拼其内部SQL。游标签名域、身份绑定、删除与授权复查要在7本域明确实现；绝不照搬旧无签名游标。
- `main.py`路由注册、`lifecycle._on_graph_end`当前快照接线、迁移head（本工作区0027）、恢复权限helper及侧栏由协调者串行整合。7从实际合并后head追加迁移，不占用0024–0027、不覆盖本轮diff。
- 7不得直接读取 `agent_answer_contents` 正文绕过双权限；采样须走自己明确的授权/保留期/墓碑校验。`agent_answer_content_metadata`只供状态/覆盖，不含正文与摘要。

## 2026-10-21 功能提交交接

- 当前集成负责人为Codex；溯源集成已在本分支起点内，不再按旧的待合入溯源分支重复接线。最终三图实跑仍需独立证据，不能由此推断完成。
- Change6负责当前main.py的console注册、lifecycle终答快照、worker删除清理、0024–0027迁移与后台公共组件；Change7仅提供自己的注册/删除/侧栏补丁，由协调者串行应用。迁移必须从实际已提交/合入后的head追加。
- Pydantic响应是权威，导出脚本为`admin/api/scripts/export_console_contract.py`；副本为`docs/contracts/admin-console-v1/openapi.json`与`admin/web/lib/operations/contracts.generated.ts`。执行`--check`可检查漂移。
- 编译期扩展示例为`admin/web/lib/operations/insights-extension.example.ts`，实际参与后台TypeScript检查；展示如何复用Meta/Metric/ListResponse/QueryToolbar而不产生假数据。7的analysis_version与assignment_revision由7自己的真实API提供。
- 用户已明确6/7功能完成后再统一压测。本次Git保存不是正式部署或全部独立验收通过；未测项保留在tasks和最新复验报告，不作为本轮已测结果。
