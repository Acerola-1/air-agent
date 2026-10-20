# Change 6 通用目标提示词

将以下整段交给实施Agent；`/goal`可按工具实际支持的目标模式语法调用，正文目标保持不变。本文件只是一份启动指令，不表示本轮已开始实施。

```text
/goal 在当前Air Agent项目中完整实施并自验OpenSpec变更 add-agent-admin-console，交付真实可用的企业Agent运营后台与可独立验收的证据。

先阅读AGENTS.md、当前DESIGN.md/UX-CONTRACT.md、docs/handoffs/parallel-tracing-console-insights.md，以及openspec/changes/add-agent-admin-console/全部proposal/design/specs/tasks/api-contract/permission-handoff/acceptance。使用环境中已有OpenSpec apply技能或等效命令，严格以此change范围执行。理解代码前遵守仓库CodeGraph规则；Next相关查看已安装Next指南。有frontend-design及premium技能时使用并遵守既有中文后台设计。

先核实工作区和HEAD；计划编写基线是dev@baa1b24，但实施按实际最新dev记录，不能把此旧提交当最新。若溯源集成或另一Agent在开发，必须用独立codex/change-06-admin-console分支/worktree，不切换或覆盖它的工作区。当前计划可能尚未提交：先确认目标工作区中存在完整计划及并行契约；缺失时仅复制这些非秘密计划文件，不复制.env/数据库/凭据，不假设checkout baa1b24能得到计划。

这是现有Next.js admin/web的扩展，不重写Vue、不动IAM/RAG仓库、不实现热门问题或自研评测平台。复用实时远程IAM和业务授权，运营/统计/正文/新反馈管理权限分开，普通Skill/MCP不再逐人授权。新权限注册/授予在自有真实IAM测试环境通过正常管理API验证；正式配置只交接，不seed正式root。

先做DTO/授权与真实数据查询，再完成终答受控快照和反馈处理，最后接真实页面。现有回答台账仅ID/版本，必须补新三图终答采集，历史缺失诚实标注；不可用全能token、任意checkpoint或共享Langfuse补取正文。收尾回调小补丁、迁移head、main/导航和删除接线与溯源/7负责人串行集成；不改根Python依赖锁，不重写图/工具。后台切换隔离、分母/缺失/Decimal币种和有界分页均是硬要求。

按tasks推进，每项同时落实验证与说明；只对真实完成项打勾。按acceptance A01–A18验证，安全反例带合法正对照，真实PG/Java IAM/官方API/浏览器和测试替身分别标注。与溯源0.7.28最终组合未跑，不能说覆盖最终版本。没有外部条件时先完成独立工作，留下具体门禁，不编造/削弱断言/全局skip。

测试只用自建空PG/Redis/独立端口，记录所有权并回收；不触碰正式15432/19132、共享Langfuse/知识库数据或正式凭据。证据使用新目录，日志不含用户原文/内部推理/口令，提交摘要小而脱敏。记录实际实施/验证/外部等待和补修次数，Token不可得写未采集。

最终报告包括改动、任务/矩阵结果、精确测试命令与结果、已知基线失败、未验证限制、权限/迁移/回退清单和资源回收；保留可审查diff，不自行commit/push/merge/归档或部署正式服务，交协调者独立验收。
```
