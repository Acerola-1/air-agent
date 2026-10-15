# Change6–10规划接手说明

2026-10-15。此文件便于将Codex会话导出交给另一位规划Agent。**会话导出足以理解目标与形成初稿；可实施规格仍须读取当前仓库及最新身份契约，不能仅从聊天记录推断代码已实现。** 本文件交接规划，不是在本轮创建或授权实施后五项Change。

## 用户已确认的目标与边界

- Agent已具核心对话、Langfuse；本轮企业化要求第一版多企业严格隔离。
- 统一业务MCP平台，每家企业独立授权身份；真实企业协议尚未核验的上线门禁保留，受控fixture不可冒充生产协议。
- 用户身份、企业、部门、角色、账号管理抽到独立IAM；目录`/Users/acerola/Dev/Java/air_agent_iam`，倾向Spring Boot4/JDK21/Sa-Token，后台已明确Vue3/TypeScript/Vite。当前实现状态应读取IAM仓库核实，不能沿用旧对话“尚未实现”的快照。
- Agent与知识库通过HTTP+Bearer+实时introspection消费身份，撤销/停用/改密即时生效、失败关闭。企业切换与ctx_version保留。
- IAM资源开通使用通用entitlement；知识库是首个资源，权限例为`knowledge:lib:<library_code>:read`。知识库仓库`/Users/acerola/Dev/Python/RAG/env-rag-final`，local/remote边界由该仓库处理。未授权不能因IAM故障自动降为local。
- IAM后台Vue不自动授权重写Agent聊天前端或Agent运营后台技术栈；后者先核实既有admin/web及当前用户决定。Change6不再重复账号/企业CRUD。
- 业务事件（停止/反馈/复制/选择）、Langfuse可观测性与基础设施监控分工清晰；统计必须有明确定义、分母、覆盖率、版本、业务时区、数据新鲜度和缺失值状态。

## 最新工程状态

- 原10个编号保留，IAM是额外基础轨道，不重编号。
- Change1/2已由协调者补修，不应把协调者成果计给匿名实现者；具体是否同步归档须查现状。
- Change3/5于2026-10-15验收关闭并归档，同步主规格，用户豁免3最后的系统剪贴板文本实机抽查。核心产品提交`2d6f7c8`、协调文档`06b411f`、Change4规划包`8620cf7`已推送origin/dev；当时main未改。这是已验证历史，当前HEAD/远端仍须复查。
- **Change4仍未通过。** 匿名模型两轮修复后最新58分，274分钟；三SDK图运行、新空库迁移、进程间目录、协议开关、权威回读、名称dispatch与恢复幂等均有反例。工作区含大量未提交实现，不能仅检出HEAD就得到产品现状。
- 本轮已交付Change4接管提示，尚未代替模型补修。规划6–10可以先做；不能据此绕过4的独立验收或自动放行后续实施。

## 必须一起交给接手Agent的材料

有本地仓库访问时直接读取；只有会话导出时，需额外提供以下文件/目录的非秘密副本，绝对路径本身不会把文件内容传给远端Agent。

1. `docs/enterprise-agent-roadmap.md`、`docs/enterprise-agent-parallel-work.md`、`docs/flash-blind-evaluation.md`。
2. `openspec/specs/`当前主规格、`openspec/config.yaml`及项目AGENTS；3/5的已归档Change，4的当前完整Change包及tasks。
3. `docs/reviews/agent-interactions-observability-takeover-acceptance.md`、`docs/reviews/enterprise-closeout-2026-10-15.md`。
4. `docs/reviews/flash-blind-05-repair-02-review.md`及`docs/reviews/evidence/flash-05-repair-02-independent/README.md`/`findings.json`，Change4后续接管验收若已产生应追加。
5. `openspec/changes/extract-shared-iam-service/`、IAM仓库最新身份HTTP契约/资源开通计划与用户确认的Vue决定。只按契约协调，不擅自改IAM/KB代码。
6. 代码与数据契约：`admin/api/app`、迁移、`admin/web`、`frontend`、`src/common/{identity,event_ledger,observability}`、事件registry及指标定义。实现计划必须核对实际查询/API/表/授权边界；无代码访问时明确标为待核实草案。

不提供真实.env、cookie、密钥、用户原始对话或数据库dump。导出的会话本身也先检查是否含这类数据。

## 后五项的既定范围

| 编号/计划ID | 目标 | 规划重点 |
|---|---|---|
| 6 `add-agent-admin-console` | 总览、用户用量、对话反馈、Skill/MCP、性能成本页面 | 复用真实台账与观测，聚合API/下钻/分页/权限/数据状态；身份CRUD归IAM |
| 7 `add-question-demand-insights` | 热门问题与用户诉求 | 增量规范化/向量匹配/稳定问题簇，主题与标准问题分层；当日7/30日、独立用户、未归类、合并拆分/版本/隐私；先校准误合并与覆盖率 |
| 8 `add-agent-quality-workbench` | 反馈处理、抽检、评测与复核、版本对比 | 用户反馈与评测评分分开，样本/标签/人工复核、Langfuse Scores适配；覆盖率与证据可追溯 |
| 9 `add-agent-operations-governance` | 告警、预算与改进闭环 | 去重/抑制/路由/审计、问题分派→优化→复评关闭；阈值与故障语义，自动通知/外部动作须另有授权 |
| 10 `harden-enterprise-delivery` | 企业交付加固 | 新装/升级/备份恢复、删除墓碑、租户/撤销隔离、负载故障、部署灰度回滚；脚本可先准备，最终验收等集成 |

顺序：先冻结6的读模型/权限/页面契约；7与8在稳定DTO及文件分工下可并行；9依赖5/6/7/8的信号和质量闭环；10从现在规划验收及演练，最终集成后验收。不要多个Agent同时改共享schema、auth/BFF、迁移head、事件生成副本或同一前端状态文件。

## 推荐交给规划Agent的指令

```text
请接手Air Agent企业化后续规划。先读docs/handoffs/enterprise-planning-06-10-context.md及列出的材料，核对当前代码与IAM最新契约。导出聊天用于理解历史，仓库与最新独立验收用于确认现状。

本轮只写中文OpenSpec方案，不写业务代码、不合并提交、不归档未验收Change。保持原6–10编号与范围，IAM独立轨道，Change4未完成不冒充已交付。先做当前能力/数据/API/指标口径/权限缺口核对，更新总体依赖路线与实施顺序。

输出6–10的proposal/design/specs/tasks与每项验收矩阵、关键接口/表/文件责任、数据权限、分阶段交付和回退方案；Change6细到可以评审后直接派发，7–10按现有证据写完整初稿，依赖尚未冻结处显式标记，实施前再刷新。避免给后面四项锁死未经验证的DDL/API/算法。提供可并行任务及共享文件冲突清单，每个Change单独通用goal提示词，strict校验并解释历史基线失败。不要给纯AI日历耗时作保证，用已测样本和不确定性说明工作量。最后提交待用户评审，不自动开始实施。
```

如果额度/上下文不足以一次产出五份可靠规格，优先交付后五项边界/契约/依赖总图和完整Change6，其余明确为草案。不能把不完整方案预填“可实施”。
