# Change 3 / 5 逐场景证据索引

这是协调者接管后的索引，不是匿名模型首交通过表。证据路径相对`docs/reviews/evidence/takeover-03-05/`。真实模型、受控模型/候选、真实服务和代码审查是不同方法；具体边界以[接管报告](agent-interactions-observability-takeover-acceptance.md)为准。

物理剪贴板文本一致性尚未证实；它不等于复制成功Promise/提示或事件数，保留Change3任务4.1。生产企业MCP材料缺失继续禁用；受控MCP不替代该门禁。

| Change / 规格 | 需求 | 场景 | 方法 | 主证据 |
|---|---|---|---|---|
| 3 / agent-answer-feedback | 评价绑定服务端确认的回答版本 | 正常回答后评价 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 评价绑定服务端确认的回答版本 | 未确认或错误关联 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 当前评价切换撤销与历史事实 | 点踩切换为点赞再撤销 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 当前评价切换撤销与历史事实 | 原因和意见越界 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 幂等与并发修订 | 已提交但响应丢失 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 幂等与并发修订 | 两个页面同时修改 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 幂等与并发修订 | 事务中途失败 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 反馈分级授权与删除 | 跨企业伪造身份 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 反馈分级授权与删除 | 删除后重投 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 反馈分级授权与删除 | 只有统计权限 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 中文交互状态与无障碍恢复 | 保存失败后重试 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-answer-feedback | 中文交互状态与无障碍恢复 | 切换企业时迟到响应 | 真实PG/API、客户端契约与真实浏览器；失败草稿/冲突交互另含源码审查 | backend-probes.json；admin-final.txt；frontend-contracts-final.txt；browser-feedback.json |
| 3 / agent-chat-interaction-events | 成功复制采集 | 剪贴板拒绝 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 成功复制采集 | 同一回答复制两次 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 稳定版本关联与回读 | 原回答与重新生成回答 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 稳定版本关联与回读 | 打开历史有多个答案 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 浏览器单调计时 | 先显示工具进度 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 浏览器单调计时 | 刷新中断计时 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 浏览器事件安全受理 | 混合批次和重复 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 浏览器事件安全受理 | 未来时间和非法关联 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 有界客户端投递 | 暂时失败与永久拒绝 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 停止阶段和结果保持独立 | 输出前后停止 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-chat-interaction-events | 停止阶段和结果保持独立 | 网络断开或旧回调 | 真实浏览器/PG、编译客户端时序、停止API反例；剪贴板物理读回例外见下 | browser-feedback.json；browser-stop-final.json；stop-association-tests.txt；frontend-contracts-final.txt |
| 3 / agent-feedback-metrics | 反馈口径与窗口 | 确定性样本 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 3 / agent-feedback-metrics | 反馈口径与窗口 | 窗口外修改窗口内回答 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 3 / agent-feedback-metrics | 复制计数与比率 | 重生成和复制 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 3 / agent-feedback-metrics | 浏览器延迟分布与完整性 | 部分采集 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 3 / agent-feedback-metrics | 租户过滤删除与查询边界 | 企业隔离与午夜边界 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 3 / agent-feedback-metrics | 可复跑的统计与接入状态 | 刚开启采集 | 真实PG确定性fixture、当前状态cohort、上海/UTC边界 | admin-final.txt；backend-probes.json；business-day.json |
| 5 / agent-capability-observability | Skill各阶段独立 | 候选没有执行 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | Skill各阶段独立 | 菜单确定性选择 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 工具逻辑调用和尝试 | MCP重试降级 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 工具结果分类 | 成功HTTP返回业务失败 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 工具结果分类 | 外呼前权限拒绝 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 多层观测不重复 | 双层回调 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 非秘密观测与真实验收边界 | 恶意工具异常 | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-capability-observability | 非秘密观测与真实验收边界 | 测试MCP与企业MCP | 真实受控MCP协议/绑定/PG；真实Skill文件/工具执行器，候选和LLM为fixture | controlled-mcp.json；skill-stages-True.json；skill-stages-False.json；mcp-authorization-final.txt |
| 5 / agent-cost-accounting | 生效区间和价格版本 | 价格切换边界 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 生效区间和价格版本 | 未知模型价格 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 精确分项计算 | 缓存和推理子集 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 精确分项计算 | 非法usage | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 已知小计与缺失覆盖 | 三次调用只有两次可计费 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 币种与归因守恒 | 多Skill共用模型调用 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 币种与归因守恒 | 不同币种 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-cost-accounting | 价格修订重算与外部费用 | 补齐历史缺价格 | Decimal单元及真实PG价格/修订/501条批次fixture | cost-501.json；admin-final.txt；root-unit-final.txt |
| 5 / agent-model-observability | 六图逐调用可信关联 | 主模型与辅助调用 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 六图逐调用可信关联 | 原生恢复 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | usage真实性与去重 | 流块和结束同时提供usage | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | usage真实性与去重 | 提供商缺usage | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 时间口径与未完成调用 | 无正文Token的工具规划 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 时间口径与未完成调用 | 运行中断 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 逻辑调用与尝试重试 | 首次失败后重试 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 观测故障有界降级 | Langfuse或观测存储不可用 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-model-observability | 观测故障有界降级 | 授权撤销 | 真实六图/原生恢复；受控模型与真实SDK/PG；回调故障为显式fixture | runtime-matrix.json；native-bff-resume.json；deepagents-langfuse-readback.json；root-unit-final.txt |
| 5 / agent-observability-access | 分级授权与数据库隔离 | 同用户加入两企业 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | 分级授权与数据库隔离 | 连接池复用 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | Langfuse实测与窄范围查询 | 回调已绑定但未落库 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | Langfuse实测与窄范围查询 | 伪造Trace标识 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | 元数据白名单和删除优先 | 删除时晚到回调 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | 元数据白名单和删除优先 | 外部删除服务故障 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | 真实聚合与有界分页 | usage与工具统计核对 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |
| 5 / agent-observability-access | 性能与故障证据分开 | 性能复测 | 真实RLS/API/SDK远端读回、受限worker与pg_dump恢复；故障边界fixture | final-environment.json；trace-proxy.json；langfuse-cleanup.json；restore-deletion.json；overhead-benchmark.json |

冷进程六图导入网络尝试为0另见`import-network-guard.json`；它使用企业强制身份模式、开启Langfuse和网络尝试即失败的护栏，不是将SDK替换成mock。元数据/Span隐私真实回读见`langfuse-scoped-readback.json`；SDK资源元数据与应用白名单分列。

UI：等待/成功/刷新/切企业/键盘/停止/窄屏已实际操作；网络错误下草稿保留和409回读还包含组件代码检查与客户端/接口反例，不宣称所有错误态都做了真实浏览器注入。
