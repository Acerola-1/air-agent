# DeepSeek 实施交接提示词

下面内容可整段交给实施 Agent。它授权实施修订后的业务计划，不授权提交、推送、合并、归档或替换正式运行态。

```text
请在 /Users/acerola/Dev/Python/air_agent 实施 OpenSpec change：
openspec/changes/add-intelligent-tracing-multiagent/

用户已确认本次修订方向。请开始实际实现，不再重复做泛化架构评估；也不要只交计划或跑通图导入就结束。先完成PM2.5纵向闭环及证据，再依赖任务顺序推进阶段二。外部服务或升级门槛阻塞时继续不依赖它的工作，并准确报告剩余项。

先读：
1. 当前根/相关子目录AGENTS.md和docs/CURRENT-RUNTIME.md指向的正式清单。
2. 本change的proposal.md、design.md、evaluation.md、tasks.md及两个delta spec。
3. openspec/changes/upgrade-deepagents-0.7.28/全部制品。
4. docs/domain/air-quality-level-reference.md；相关现行能力选择、输出和生命周期契约。

先检查分支、HEAD、工作区diff、langgraph.json、实际解释器/包版本和升级工作区现状。2026-10-19修订时dev HEAD为6ff95dd，三图已提交；主环境0.7.5，/tmp/air-agent-upgrade环境0.7.28。这些是快照，开工必须复核，不覆盖/重置其他任务。若存在.codegraph，先用CodeGraph定位代码；不存在不建索引。

关于已有升级change：
- 它已被本计划纳入。负责0.7.28完整依赖锁、镜像、API注册、checkpoint升级/回滚及观测出网验证，不能和业务改造混记。
- 历史六图、旧网关地址、旧临时库是旧基线记录。不得恢复被删图/旧库/旧local鉴权来满足报告，当前清单说了算。
- 升级change的现行规格仍写六图，不可仅当历史跳过；G1前在该change内修正当前图清单要求并追加当前HEAD/三图的补验计划与结果，保留原六图历史实测及事故记录。本次规划修订未替你完成升级补验。
- 本业务固定Python3.13 + deepagents0.7.28，不维护0.7.5双基线，不私装升级后声称锁已覆盖。
- 升级尚未完成不妨碍G0：在独立目标版本环境先做纯函数、fixture、fake model与并行state spike。可复用核实有效的已有升级环境，不在未验证时替换主环境。
- G1正式配置联调前，必须有当前基线的真实模型、三图注册、镜像/锁、观测/出网和迁移/回滚证据。升级缺口按升级change单独处理和报告，不能用旧勾选或HTTP200替代。
- 旧升级事故/owner复核项保持原状，不擅自“修复”共享权限，不根据旧报告seed或回滚正式库。必要环境缺口不影响可独立业务任务推进。

本次必须实现的核心：
1. 四角色：station-data、grid-data、meteo-analyst、evidence-reviewer。前两者是零模型CompiledSubAgent；reviewer找反证/遗漏，不作事实源和最高证据裁判。
2. 不创建trajectory-analyst。lead/专员/内部工具/恢复都不装配和调用轨迹或PSCF；实际请求数必须零，固定trajectory_unavailable。用户不需要你继续猜cl参数。
3. 在数据委派前校验并冻结TracingRequest。处理站点消歧/时间补问；task只有description/type，子图从可信状态读取请求，不能假定自然语言会自动映射字段。
4. 明确structured_response到ToolMessage与父findings更新两条路径。各子图只导出自己的字段，原始数组不进lead；验证双专员并发、反序完成、失败、第二轮修订和迟到结果。
5. 拆分spatial_pattern与process_assessment。多站同步/网格连片只证明区域现象；传输关联须有附加时序/风场证据并讨论共同静稳等替代解释。L4只作待核查假设，L5/L6不可达。
6. 证据由执行层注册，带scope/revision、数据来源、时间/单位、质量、派生关系与策略版本。门禁验证claim allowlist和引用语义，不只查ID存在/数字匹配/禁词。
7. 使用受控报告计划和模板，生成ValidatedReport。正文、画布、持久化、重开历史共用同一验证版本；溯源画布工具只接已验证引用，不能直接收任意content。门禁失败不重试模型洗白、不回退原始AIMessage。
8. 保留合法完整率/同步率百分比及“无法计算贡献率”限制说明；同时拦住没有禁词的越级归因。
9. 字段级哨兵、负u/v、时间对齐/单位、NWP模式来源、网格同源关系和调查半径都按设计处理。先冻结具体analysis-policy-v1；没有依据不猜法规阈值或源距离。
10. 实际工具/schema/dispatch验证GP、execute、文件工具边界，tools=[]不等于无工具。复合工具内部依赖也受当前受理选择和实时权限约束，不能直接HTTP/裸MCP绕过。
11. 主/子/摘要外呼覆盖身份撤销与IAM不可用，追踪真实子任务但不伪造新顶层request/attempt。维护context_version/epoch、停止事实、删除墓碑、恢复和缓存边界。
12. PM2.5先通过再扩展其他五项；O3小时过程和日最大8h评价分别保留。未实现画像明确说明，不能静默复用PM2.5。
13. include_tools/fork默认关闭，独立可选实验，不是主链或首阶段验收前置条件；不要重新把它们变成业务必需项。

按tasks.md推进，关键顺序：
基线/策略/资源 -> 请求/证据契约 -> 目标版本fixture spike -> PM2.5内核/四专员 -> 统一发布与回读 -> 析因评测 -> G1就绪后真实SDK/模型/MCP/浏览器 -> 首阶段交付 -> 其余五画像。

评测采用相同专员/内核/渲染器的2×2：
A0固定编排+规则门禁；A1固定编排+LLM reviewer+规则门禁；
B0 DeepAgents+规则门禁；B1 DeepAgents+LLM reviewer+规则门禁。
区分冻结数据回放与调查任务。完整实现前先固化P01–P18及正向控制；相同输入的数值/规则/allowlist需100%一致，不能用三次两次一致验收确定性部分。安全/发布/必需步骤不允许被95%总体准确率掩盖。不要制造更弱的A组来证明B组优秀。

资源与交付边界：
- 遵守Apple container指南。只用本任务自建空临时资源并回收；正式库不seed、不truncate、不downgrade，不compose down影响共享栈，不用历史账号/DSN。
- 保留配置模型及真实服务。不可达就记未执行；fixture、受控服务、真实模型、真实MCP和浏览器证据分别标注，不用mock冒充。
- 不改外部IAM/RAG仓库、MCP服务、其他图、graph ID或公共事件语义；共享接线仅最小可选扩展并做针对性回归。若确需超出这些边界，先停下该部分并说明具体原因，继续其他已授权工作。
- 按任务实际完成情况勾选，补修与首交分开。证据写docs/reviews/evidence/<新run-id>/，不覆盖旧报告；不输出/提交凭据、会话、内部推理或原文。
- 使用定向Ruff/pytest、相关契约检查及真实浏览器验收；不为纯文档机械跑全套，不删断言、不跳过已知失败来宣称通过。
- 开始与结束均strict验证本change。不要commit/push/合并/归档或部署替换正式运行态。

最终交付：改动与原因、PM2.5/阶段二逐项状态、依赖升级关口、定向检查及真实证据、析因结果、首交/补修耗时与Token（未知记未采集）、现存失败/未执行项和资源回收情况。PM2.5通过后可以阶段性交付，但阶段二未完成不得把整个change写成已完成或归档。
```
