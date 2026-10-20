# Design

## Context

基线2026-10-20 `dev@baa1b24`。动机见proposal。已有`agent_requests`的tenant/user/thread/graph/business、`agent_content_refs`原问题受控快照、确认受理submission、回答/反馈与墓碑；尚无问题标准化、向量归类、稳定簇、分析worker或运营洞察页面。已有Embedding配置不能证明管理API已具隔离分析能力，不能导入根Config触发图/模型预热。

Change 6与本项共用`../add-agent-admin-console/api-contract.md`和权限交接。本项后端可按该文件独立开发，页面最终集成等待6公共组件；不依赖新版溯源数据源/报告/四专员，也不需要升级DeepAgents。

## Goals / Non-Goals

**Goals:** 稳定标准问题+主题、可持续增量统计、可纠错和可删除，运营可依据热度和负反馈选择改进方向。

**Non-Goals:** 自动推断用户心理/任务已解决、跨企业挖掘、问题自动执行、重新调用业务MCP验证答案、RAG重建、独立评测平台、自由式自动Agent、收费账单。聚类用的标注校准不等于建设Change 8评测平台。

## Decisions

### D1. 采用异步扫描与持久来源清单，不修改公共事件

在`admin/api/app/demand_insights/`实现schema、source_reader、normalizer、embeddings、classifier、store、jobs、worker、statistics、privacy和api服务；router单独挂`/agent/insights`，不在图节点执行。worker通过`python -m app.demand_insights.worker`显式启动，默认off，不在web进程导入时启动线程或网络。

来源以同企业已确认受理request为单位（存在confirmed submission），选不可漂移的user_message/content_version快照，排除恢复/重新生成重复来源；历史无body、过期、未知归属单独记reason。保存`source_manifest`，unique(tenant,request,source_content_version,analysis_version)，原子意图unique(source,split_index)。同一问题两个相同原子意图先在请求内去重。

扫描默认每60秒一批100条，首次需授权启用和选择起始日（默认当日），单作业500请求，回填最多30业务日且显式确认预算。用(admitted_at,id)做索引分页，并对“先admitting后confirmed”“先缺源后补齐”保留pending来源/回看重试表；不是仅按created_at高水位跳过。断点与一批结果同事务提交；重启anti-join查缺口，不重复计算request。

### D2. 委托与worker最小权限

人工创建/续期job时要求ops+content+`agent:insights:manage`，固定tenant/user/member/会话ID/ctx/修订与分析版本。job只存服务器生成的委托引用，不能存Bearer/cookie/原文。新增独立受保护`agent_insight_delegations`保存现有应用会话handle摘要引用、固定作用域与到期，不复制subject_token；只有worker身份能读取，浏览器只得到job_id。通过现有`load_session_token(RemoteSessionProbe)`从服务端桥读取原subject并调用现有实时内省，逐项比较固定scope；不接受浏览器给hash或把数据库投影当授权。

委托最长12小时且不超过现有会话有效期；每批读取前、每次Embedding/LLM外呼前、结果提交前重新内省与比对，切企业/改密/撤会话/撤权后立即暂停，不静默接收新ctx。周期扫描只在有效委托覆盖内运行；无人续期时`paused_authorization`并显示原因，不能变成永久后台万能身份。未来机器常驻分析可另接IAM服务账号，本项不绕过当前契约擅自做长期token。

worker使用专门非owner、非super、非BYPASSRLS角色，本域表最小写权、来源/反馈/墓碑只读，事务本地tenant+必要operations/content GUC；不授旧身份表、图checkpoint或全库权限。委托读取与业务处理分离事务；每个处理事务必须重新应用限定scope，连接池A/B/异常/无上下文均验收。有限租约fencing token绑定job版本，worker回包提交使用CAS。

### D3. 标准化先保护业务语义

确定性规则清理空白、脱敏账号/手机号/邮件/凭据，对可能含内部秘密的片段转受限不可分析，不自称正则脱敏能保证匿名。只有配置批准的分析模型端点可出站；基地址与密钥只来自受保护部署配置，不接受URL/模型名任意透传。

必要时以schema约束的小模型拆意图/补指代/提取slot，不给工具，不执行原文指令；最大4000字符、最多5个原子意图，超限转复核。上下文最多同request受控引用的2条前序用户消息，总8000字符，不能扫描整个thread或跨租户借上下文；无法取得则unresolved_context。摘要输出白名单schema，禁止原问题中的注入指令改变系统流程。

每个原子意图保留`action`（查询/趋势/原因/预测/建议/其他）、`object`、time_kind、城市/站点/污染物/日期slots、negation、规范化文本/摘要、normalizer版本及来源。标准问题模板可抽象城市日期，但slots保留，涉及权限/数据范围不同的任务必须经约束复核，不靠删除实体直接合并。主题为“空气质量查询/污染成因/预测”等上层业务主题；Skill路由只供候选，不能作为真实分类。

### D4. 向量存储与增量归类

首版用Agent自有PG的`real[]`/float数组列，按tenant+embedding_model_version+analysis_version限定**最多2000个活跃簇原型**，分批读取、请求级有界精确cosine候选top10，再执行动作/对象/否定/时间类型约束；相似度临界或候选分数差不足时才调用LLM复核，不给LLM全库。每次任务有界处理，首版无需pgvector、共享Qdrant或RAG数据库。

超过2000簇时保留已知簇精确匹配，无法完整候选检索的新增问题转`capacity_review`，不截断后假装全局最近邻；后台显示限额，可由另行索引change提升。向量维度在model profile固定且<=4096，有限数值/非零范数验证，维度与profile不符拒绝。

高置信匹配关联稳定cluster_id；无可靠匹配形成candidate cluster，低置信进入unclassified。cluster有稳定ID、中文脱敏概括、action/object约束、topic、状态、原型与版本，命名不改变身份。相似度阈值/margin从校准集确定，记录在不可变analysis profile，不能抄一个0.8就称可靠。外部命名必须通过脱敏输出检查，标签不能照搬电话号码、账号、坐标等私有原句。

并发相近新问题以tenant+profile短锁/版本复查防重复建簇；耗时Embedding/LLM在事务外，提交前重查scope/墓碑/当前簇版本和租约，不能持PG事务等待外部模型。每日不重新聚类全部历史。

### D5. 数据结构与可追溯版本

本域模型/迁移独立于主`models.py`，物理名可按实现规范细化，但以下边界不变：

| 表组 | 关键字段/约束 |
|---|---|
| `agent_insight_profiles` | tenant、不可变profile版本、规范化/embedding/model/阈值/政策；active指针独立CAS |
| `agent_insight_jobs`、delegations | scope、窗口、pending/running/paused/failed/completed、租约/fence、用量、错误安全码、到期 |
| `agent_question_sources` | request/message/content_version引用、admitted_at、源hash/expiry、processing状态，唯一来源版本 |
| `agent_question_intents` | source、split_index、脱敏文本/slots、embedding profile/vector、confidence、expiry；不复制原文 |
| `agent_question_clusters`、topics | tenant/profile、稳定ID、safe canonical name、constraints、revision、候选/active/merged/retired |
| `agent_question_assignments` | intent/profile/assignment_revision、cluster或unclassified_reason；同有效版本唯一归属 |
| `agent_insight_operations` | 操作ID、请求摘要、期望/结果修订、安全审计；无原文 |
| `agent_insight_summaries`（可选） | scope/window/profile/assignment/source_revision、引用与失效状态，模型故障时榜单独立可用 |

所有企业关联复合FK/同tenant校验，runtime/worker均FORCE RLS。source是可识别来源锚，vector/文本是受限内容，不能仅按metrics权限直接读表。cluster/topic面向metrics只暴露去标识概括与聚合，样本独立ops+content。无跨企业身份表外键；身份以现有业务引用承载。

### D6. 统计口径与反馈关联

按原问题admitted_at入窗，不按分析日期；当天/近7/30业务日含今日至as_of。默认rank=atomic_intent_count，也可按distinct_users排序，均带distinct_requests；同主题请求/用户直接去重，不累加子簇数。占比=本簇原子意图/当前已处理原子意图（含unclassified），同时单独列`processed_requests/eligible_requests`，未处理问题原子数未知，不能用0补齐。

前期比较使用相同profile和assignment revision：最近7/30日与紧邻同长度时段按同日内截止比较，或明确完整日队列；today与昨日相同时刻比，昨日不存在数据标unknown。前期0时growth_rate=null/new=true；回填不足标not_comparable，不能把未处理当0。canonical版本切换提供当前分类回算视图，旧发布报表可按版本查看但不得绕过最新删除。

负反馈按request聚合关联，显示`request_level_negative_feedback`及评价覆盖，不复制成每意图确定错误；技术失败/停止分别列，复制不等于解决。不会在无证据时显示任务解决率。SQL从最新有效关联查询，初版不建不可校正的累计计数缓存，避免删除/拆分后漂移。

### D7. 维护、重算与删除

改名CAS，合并仅同tenant/profile，保留source→target重定向且检测环；按assignment新修订一次发布，子簇原统计可追溯。拆分先预览要分配的intent ID并覆盖全部来源，不能只有新名称无来源清单；人工操作幂等，跨tenant拼接404/拒绝。

换embedding/算法创建新analysis profile，旧榜单继续服务；新版本有明确水位/样本覆盖、校准与数量守恒后CAS切active，不无声更换历史簇ID。映射保留旧簇与新簇的分拆/合并关系，代表样本随当前关联刷新。

删除/原文到期同等影响派生文本与向量。每次读/写先检查墓碑、源有效期及当前可见来源；派生expiry<=源expiry。簇原型必须能从存活来源重新计算，删除后旧原型/生成名称若仍含源信息也失效，不能只删除原句留下向量。与现有删除worker串行接一处本域清理hook，失败时清理待办重试，本域读取持续禁止；没有实现清理hook不得以查询隐藏冒充物理完成。重算、旧备份恢复、迟到模型回包同样验证。摘要缓存绑定source/assignment修订，删除立即失效。

### D8. 故障预算、评测与实现阶段

默认并发2、批100、模型超时20s、最多3次尝试（含首次，退避1/2s）；单job最多500来源、200k估算分析Token，Embedding单批128k字符，真实usage独列，不能将缺失记0。重复失败入受限队列并可按权限重试，向量缺失不得偷偷换供应商。模型预算为工程保护上限，不当精确账单；实际profile可调但需版本和审计。

校准至少160条中文业务样本：60调参、100冻结留出（其中至少30个不同任务的困难近似对照），含多意图、追问、实体/日期/否定、注入/敏感字段和新主题。自动归类留出precision目标>=98%、清晰意图覆盖>=60%、至少50项自动归类；原因/查询/预测关键负例误合并为0。报告完整混淆/误合并/未归类、样本量及区间，不把小样本分数当生产保证；低覆盖不能通过全部拒绝换高精度。真实Embedding+辅助模型运行与fixture分开，不能仅fake向量验收算法。

阶段：后端可先开发→真实模型校准→人工维护/删除/恢复→接6页面→最终三图组合。自有PG至少10万requests/20万intents、1000簇，同环境35×2请求，SQL榜单本地P95<=1000ms、列表<=500ms；异步分类记录每阶段时间/用量不套用交互API时限。

## Risks / Trade-offs

- [用户原文可能包含商业秘密] → 明确启用/供应商配置与脱敏/拒绝；未启用不分析历史，不能声称不可逆匿名。
- [人类委托会话到期导致周期分析暂停] → 显式状态与续期，不偷接长期root token；未来服务账号独立扩展。
- [首版精确检索有簇上限] → 有界候选+capacity_review，提前测量；不为本项引入共享向量基础设施。
- [延迟确认/缺源导致增量漏扫] → source状态表+pending重试+回看anti-join，水位不等于全部成功。
- [7早于6实施] → 后端按冻结DTO独立，页面及共享文件串行接入；6未完成时7不能宣称全产品验收。

## Migration Plan

1. 权限与profile/config契约先冻结，空库创建本域表、运行/worker角色、RLS/复合约束；revision接合并时head。
2. worker默认off，先跑脱敏校准样本，确认供应商、模型ID、维度和阈值。没有真实模型时保留该门禁，不能用网络探活代替。
3. 正常管理面给自有测试企业授权，选择窗口创建委托作业；验证重启、晚确认、限额与撤权。
4. 维护/重算/删除/恢复演练，接6后台，实际浏览器验证榜单—标准问题—授权来源—纠错完整链。
5. 上线需要管理员显式启用并选起始窗口；本change实施不自动对正式原文做外发分析。
6. 回退先关worker和页面入口，保留旧active版本或上一个代码；不盲目downgrade，不停止删除/到期清理。恢复任何备份先重施墓碑、当前权限与有效期，再开放任务。
