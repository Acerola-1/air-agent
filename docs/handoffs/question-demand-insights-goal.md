# Change 7 通用目标提示词

下述目标模式语法可按工具调整，目标与边界保持。6未完成时可先做7后端，但不能宣布页面和最终组合已验收。

```text
/goal 在Air Agent中完整实施并自验OpenSpec变更 add-question-demand-insights，交付企业隔离的热门问题、主题与用户诉求分析，以及可独立验收的证据。

先阅读AGENTS.md、docs/handoffs/parallel-tracing-console-insights.md、openspec/changes/add-question-demand-insights/全部proposal/design/specs/tasks/api-contract/acceptance，以及add-agent-admin-console的api-contract、permission-handoff和共享UI设计。使用项目已有OpenSpec apply技能或等效流程。本项来源是确认受理的用户原问题，不是溯源报告或Skill路由标签。

先核实实际HEAD/工作区/6和溯源状态。独立codex/change-07-demand-insights分支/worktree，不切换或覆盖其他Agent工作区。规划基线baa1b24仅供溯源；完整新计划可能未提交，工作区缺计划时只复制相关非秘密文档，绝不复制.env/凭据/正式数据。

采用design已决方案：异步增量来源扫描、必要脱敏和原子拆分、PG自有隔离向量/有界候选、意图约束与少量LLM消歧、稳定标准问题+主题、SQL时间统计。不要每日全量LLM重聚类，不使用共享RAG/Qdrant，不改图/MCP/DeepAgents或根依赖锁。每个模型/向量profile和阈值有版本，低置信进入未归类，不用全部拒绝换高精度。

按design实现持久job/来源唯一键/租约fence/晚确认补扫，有限人类委托通过现有会话桥实时内省，过期/切企业/撤权/故障失败关闭；不能创建永久root令牌。新增insights管理权限在自有真实IAM正常管理面配码/授予验证，不修改IAM/RAG仓库或正式root。每批/外呼/提交前复查，job不能存Bearer或cookie。

来源保留期与删除覆盖脱敏文本、向量、簇原型、样本和摘要；晚到模型回包和恢复旧备份不可复活。合并/拆分/重算有CAS、幂等、版本与原子切换；统计独立区分原子意图、去重request/users、负反馈证据与覆盖，无评价不等未解决。

先完成独立后端和真Embedding/模型160条校准及留出门禁，再接6共享组件的operations/insights页面；6壳未就绪时后端可继续，最终产品验收不能用临时mock页面替代。迁移head、router/nav和删除接线按并行契约串行集成，不复制6的会话/查询条或覆盖它的接口。

逐项完成tasks并运行acceptance Q01–Q20，保留来源可核对的SQL/API/真实浏览器证据；纯函数/fake向量、真实PG、真实Java IAM、真实模型分开。每项功能同时补相应测试与文档，不能只打勾。真模型、IAM权限或最终6/溯源组合受阻时明确保留未完成门禁，继续其他独立工作，不改断言或编造成功。

只用自有空测试资源与脱敏样本，不分析/外发正式用户历史，不故障注入正式15432/19132或共享服务。记录并回收本任务资源，新目录保存证据，不存原始秘密。记录实施/验证/外部等待、返工和算法/应用性能；Token不可得写未采集。

交付最终报告、配置/权限/迁移/回退/启用清单、矩阵及脱敏摘要，留下可审查diff；不自行commit/push/merge/归档或部署正式环境，交协调者独立验收。
```
