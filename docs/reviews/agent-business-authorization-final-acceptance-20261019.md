# Agent 与 IAM 业务授权联合最终验收

日期：2026-10-19验收，2026-10-20收尾。结论：**本轮两个业务授权变更验收通过，已同步主规格并归档，保留可提交的工作区diff。**

范围是IAM的`add-agent-business-entitlements`与Agent的`simplify-agent-business-authorization`。这不等于整个企业级后台路线图完成，也不包括外部MCP服务端的企业数据授权。

## 验收与补修归属

用户最初指定Codex主导、AGY执行。AGY补充了真实HTTP屏障并发、撤权/重试测试及官方API checkpoint探针，但触及60分钟上限，未交付完整最终报告。其部分成果和缺口见[超时复核](agent-business-authorization-remediation-review-20261019.md)。本次用户明确要求Codex接手，以下新增补修、独立验证和归档属于协调者收尾，不倒算为AGY或原匿名实施模型的首次交付成果。原报告和原始失败证据全部保留。

Agent核验基线HEAD为`6ff95dd`，IAM为`630b865`，两仓均为dev及已有未提交实现。没有重置、覆盖其他任务或执行commit/push。

## 发现并修复的实际问题

真实恢复探针证明：在原生运行metadata中把已受理的MCP空集合改为auto，原实现返回200并把载荷覆盖回权威allowlist。这没有证明工具越权，却违反“载荷冲突启动前409”的契约。

现已在消耗启动权前逐项核验metadata、kwargs.config.configurable及顶层config中的全部选择字段。匹配或省略字段继续兼容；冲突、工具ID类型错误及重复项明确409。补充22项定向回归，并在真实官方API复验冲突拒绝后仍可使用原预留合法恢复。没有放松业务、身份或选择边界。

重试测试另有断言`or first_res is not None`过宽，已收紧为明确MCP错误；新增真实HTTP503注入，将业务错误与传输错误分开。服务端在503时撤权，验证后续SDK/调用方重试的全部MCP协议请求增量为0，恢复授权后有合法正对照。

## 独立证据

本轮证据目录：`docs/reviews/evidence/run/codex-business-final-20261019/`。这是本机脱敏验收材料，不含正式口令、令牌或数据库转储。

| 门禁 | 独立结果 | 证据 |
|---|---|---|
| R1真实并发 | A到达HTTP屏障挂起，B先完成；认证主体、结果、计数不串用 | `mcp-execution-tests.log`；保留测试源码 |
| R2握手后/重试前撤权 | 11项执行测试通过；真实HTTP503及工具错误分别验证，撤权后全部协议发送增量0 | 同上，pytest 9.36s |
| 业务/连接/DeepAgents | 42 passed，pytest 10.03s | `targeted-tests.log` |
| 新原生冲突与相关身份/外呼守卫 | 47 passed，含新增22项，pytest 4.15s | `native-guard-tests.log` |
| R3真实完整恢复链路 | 9个检查全部通过，官方API 0.15.1、当前basic-qa源码、PG/Redis持久化 | `resume-13/result.json`及`official-api.log` |
| IAM业务求交及配置边界 | 7项集成测试通过 | `iam-owned-tests-02/result.json`及`maven.log` |
| IAM root命令/清理/事务/幂等 | 5项集成测试通过 | 同上 |

R3先经正常Agent登录门面、合成IAM实时内省、内部签票与真实线程认领，然后调用`POST /agent/requests`受理。官方API静态中断在call_model前生成真实checkpoint；从平台读取状态后，调用既有内部受理确认和状态观测API，未用SQL插入请求、attempt、submission或修改运行状态。

已证实：受理时改选择409；同时拥有两业务资格仍不能换来源图409；原生恢复时换图/改选择/改业务均409；撤业务403、撤会话401，负例模型新增请求均0；合法恢复success并有实际受控模型产出，模型增量1。最终只读计数为1 request、1 attempt、2 submissions、1 resume，恢复不重复统计提问。

R3使用合成IAM/受控模型，不冒充真实Java IAM或正式模型。IAM新增两类测试独立运行在另一组自建空PG/Redis，iam_app与iam_migrate均非superuser、非BYPASSRLS；Agent请求走非owner、非BYPASSRLS的air_agent_runtime。没有对正式身份或业务库注入撤权/故障。

## root、真实业务及旧存储清理

Codex此前本机独立证据位于`docs/reviews/evidence/run/codex-business-completion-20261019/`：

- `root-normal-login-125738.json`：正常IAM登录200，alpha-env、beta-env、gamma-env、root-acceptance四企业切换/me均200，三业务及运营、统计、正文共六个精确权限均有效。验证前后密码摘要一致，自己的临时会话已退出；不据此推断所有历史阶段从未改密。
- `root-agent-view-133220.json`：Agent正常登录200；四企业运行列表/统计均200；alpha-env与root-acceptance有root自有正文且读取200，另两企业没有自有样本，记录null，未伪造正文读取通过。
- `codex-business-acceptance-20261019/live-metadata.json`：正式业务库air_agent_business在0022，mcp_identity_bindings不存在；IAM实际iam_identity，三个旧权限码定义计数0。备份命名表计数0仅针对被检查命名模式，不冒充全磁盘历史资料已擦除。

真实模型和真实MCP运行沿用已核对的原作者矩阵：三公开业务×native/BFF×stream/wait共12项，首次9项有实质回答，数据分析3项最终整理失败，随后3项重试有回答。这是有失败和重试的证据，不改写成首次12/12稳定成功；答案内容质量与外部服务缺陷不在本次授权补修中冒充修复。对应原始材料在`agent-business-authorization-20261019/`。本轮不再次消耗正式模型或改其配置。

root在每企业通过明确角色获得权限，不设用户名旁路，也不自动拥有未来业务。原外部MCP共享只读接入能查询，不代表服务端已实现企业数据隔离或可证明的全数据范围；此后续边界保留。

## 文档、资源与交付范围

- 观测主规格中的旧绑定措辞同步为业务资格/连接/已受理范围；旧身份change明确标记被替代，原7.6未实测事实保留。
- 回填IAM联合任务以本报告与原始证据为准；原交付中的短口令阻塞说明保留为历史，不覆盖旧报告。
- 成功恢复探针全过程约26.39s（含引导和回收）；IAM定向测试全过程约26.90s。之前探针初始化、合成DTO和列表元数据问题的失败轮次保留。Token未采集，不把AGY会话累计量当成本轮量。
- 本轮所有自建aa-codex-bizfinal-*和aa-codex-iamfinal-*容器已回收，AGY的aa-bizfix-*先前也已回收。未清理共享卷/镜像，未创建旧授权备份。
- 本轮新增补丁在自有官方镜像验证；未重启共享正式服务，不把本轮补丁写成已单独发布。两仓保留可审查diff，无commit/push。归档不等于Git提交或部署。

此前AGY/作者的全量测试数量仍属于其对应执行记录。本轮独立复跑仅以上述89项Agent定向测试、12项IAM集成和9个官方API检查计数，不将未重跑套件记成本轮全绿。主规格严格校验的13项既有失败按同步前后集合比较，结果另附归档验证证据。

## 2026-10-20 归档与提交前收尾

本节更新前述10月19日时点：两仓主规格已经同步，Agent `2026-10-20-simplify-agent-business-authorization`（29/29）与IAM `2026-10-20-add-agent-business-entitlements`（23/23）已归档。可随Git提交的[脱敏验收摘要](evidence/business-authorization-acceptance-20261020.json)在两个仓库各保留一份；原始日志位于Agent的忽略目录，不纳入提交。

本次另在自建空PG/Redis完整复跑IAM Maven：99项通过，0失败、错误、跳过；Vue 28项测试通过且生产构建成功。Vue单测有既有生命周期提示，构建有包体积提示，均不是零警告结果。Agent89项定向回归及9项官方API恢复检查保持前述来源，未重复计数或冒充全仓重跑。涉及的7份主规格校验通过；Agent13项既有规格失败集合未增加，IAM全部主规格通过。

测试结束后回收本任务所有临时容器，删除已退役Change3/5的两份测试库备份、过期探针日志、测试缓存、IAM Surefire报告/测试类和临时协调脚本。保留失败轮次结果摘要、测试源码、最终证据和历史评分；没有删除正式配置、共享卷、镜像或其他项目资源。清理条目、字节数见摘要及本机 `codex-business-closeout-20261020/cleanup-*.json`。

两仓仍是dev工作区，未commit、push或重启共享正式服务；最新补丁通过隔离官方API验证。最终秘密扫描、diff检查及归档列表核对见同目录提交准备证据。外部MCP服务端仍按用户要求不纳入本次工作。
