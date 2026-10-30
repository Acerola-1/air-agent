# 会话能力 v2 评阅入口

2026-10-29；本文件为实施者交接清单，**不是独立验收结论**。当前33/35：5.1真实模型回复质量仍待收口，7.6留给后续评审者。按用户要求本轮未使用子代理。代码未提交、未发布、未归档；没有执行正式迁移、重启或配置升级。

## 先读哪些材料

- [当前实施记录](implementation.md)：分层结果、原失败、局限及用户排除项。
- [实际流程图](flow.md)、[运行协议](runtime-design.md)、[整体升级回退](upgrade-and-rollback.md)。
- [OpenSpec任务](../../../openspec/changes/revise-basic-qa-session-capabilities/tasks.md)、[设计与用例](../../../openspec/changes/revise-basic-qa-session-capabilities/design.md)。
- [固定用例集](../data/bqa-session-capabilities-v2.json)及[工具检索集](../data/bqa-mcp-retrieval-v2.json)。49例／31组和九Skill×两模式是固定清单，**不是全部真实模型均已执行**。

## 九份delta的核对入口

- `basic-qa-session-capabilities`：session_capabilities、basic_qa_runtime、basic_qa_integration、builder/nodes；看下列编译图、SDK、真实模型及浏览器证据。确认跨轮不清空、多个来源并存、加载批次串行累计、当次实际披露和独立终答。
- `basic-qa-mcp-tool-discovery`：mcp_tool_catalog/index/search、迁移0030；核对Top 3、稳定ID／schema版本、非owner FORCE RLS、持久增量索引、取消回滚和独立加载。检索score含对象先验，不能称纯向量或概率。
- `agent-authorized-capability-catalog`：capabilities、business_registry、mcp_scoped及scope边界测试；业务注册109项不是模型一次看到109项，也不是当前每项服务都独立执行过。
- `agent-service-connections`：受保护连接修订和逐发送复核；看真实回环MCP零请求反例与正控、SDK变更矩阵。不引入人员级Skill／MCP授权。
- `agent-request-capability-selection`：可信冻结选择、BasicQAInput限制、同attempt严格恢复与新轮失效重载；不新增前端Skill选择协议。
- `agent-capability-observability`：capability_observation和事件源／Python／admin／TS副本；区分selected、loaded、activated、reused、executed及模型／网络尝试，不把未知usage记零。
- `skill-content-basic`：九Skill依赖清单和材料审计；图表可选，其他必要依赖不被静默吞掉；自然语言指定Skill有单独模型用例。
- `skill-three-layer-system`：当前版本完整fast/expert规则按每次发送恢复、预算休眠及摘要后恢复；历史加载回执不是当前规则权威。
- `composite-backend-routing`：保留读取磁盘材料的兼容路径；Basic QA仍是原生StateGraph，未换DeepAgents，也未把工具搜索做成每轮必经节点。

## 已有证据与边界

- 单测／编译图：主要领域回归192通过、24项原有外部检索跳过；共享tracing、封存图必要兼容及正文249通过；新取消及有界纠错51通过；最后提示改动36通过。这些分组有重叠，不相加为独立用例总数。
- 真实原生SDK／持久化：[native-sdk-v2.json](native-sdk-v2.json)、[native-sdk-upgrade-v1.json](native-sdk-upgrade-v1.json)、[bff-sdk-v1.json](bff-sdk-v1.json)、[完整变化及整体回退矩阵](native-matrix-v3.json)。全是自有空资源、受控身份／模型／服务，不替代真实模型。
- 真实模型核心八链各三次：[核心逐场景审查](real-model-core-v3-review-v2.json)；修正Skill优先与独立发现后的[15轮复测](real-model-policy-v4-review.json)通过；[澄清／指定Skill](real-model-clarification-named-v1-review.json)通过。保留全部原失败，不以无工具错误替代业务完成。
- 最新范围链：[v6](real-model-scope-v6-review-v2.json)7/9，一次终答被安全缓冲中止，一次拒答后冗长列历史数据；原v6未采集终答中止字段，补正版将对应用户异常计数记为未知1轮，不能引用零异常结论；前一版[v5](real-model-scope-applicability-v5-review.json)8/9、[v4](real-model-scope-v4-review.json)均保留。当前不是全部模型场景通过，禁止将5.1或产品整体验收写成完成。
- 工具检索：[固定12题Recall@3](frozen-retrieval-object-prior-v2.json)12/12；[实际模型候选选用](frozen-top3-model-selection-v2.json)12/12含可接受目标，另选了2个gold外候选；没有执行这些候选。不代表任意自然问法召回保证。
- 真实只读MCP：坐标行政区与原空气质量接口均独立执行成功，见[live-retrieval-initial-summary.json](live-retrieval-initial-summary.json)。未将它们与受控模型拼成一条全真实业务成功链。
- 真实浏览器：[记录](browser-v6-summary.json)。三城、复述／感谢、刷新后重选持久化会话再追问、取消、图切换和知识原文已检查；受控内容。用户明确排除画布，不记画布通过。模式按钮运行中仍可点击的原有行为未修复，服务端冻结模式的SDK反例已通过。
- 相同固定输入的新旧对照：[lifecycle-comparison-v3.json](lifecycle-comparison-v3.json)，Skill加载7→2、前置工具错误3→0；仅三链各一次，不证明容量或普遍提速。

## 最终检查和仍未完成

受影响文件Ruff／format通过；前端契约94通过，独立副本生产构建通过；本change strict通过，历史三份主规格外壳问题留待未来同步前处理。具体命令日志留在忽略目录`.local/bqa-session-capabilities-v2/`，不提交原始正文、账号或测试凭据。

5.1仍开放，原因是回复简短性及一次真实终答失败；没有靠新增分类器、接口时间城市回显校验或强行结果门禁来改变通过率。7.6必须由后续评审者执行，本文件不能代替它。正式部署前还须按升级说明排空所有受全局策略变更影响的实际运行；本轮只演练自有资源，未执行正式排空。产品效果仍由用户独立判断。
