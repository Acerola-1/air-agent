# Design

## Context

动机和用户确认见[proposal](proposal.md)。本轮只写计划，未运行服务或修改三仓业务代码。

| 仓库 | 当前核查基线 | 当前事实及直接证据 |
| --- | --- | --- |
| Agent | `dev@40b3f6f1fb9d9492ca8ec09b2a6ff4b9723f51a3`，Python3.13、deepagents声明≥0.7.28 | `runtime_tools.py`只有时间/区域/联网搜索/画布/计算和MCP；没有知识工具。QA知识车道基于常识，闲聊schema为空；分析空allowed-tools分支仅计算；tracing lead显式4工具 |
| IAM | `c040855ab5191558b8ffcd9e5c37266af8c479a8`，核查时干净 | `IntrospectionServiceImpl.java:156-161`限制非runtime audience等于登录受众；301-342的执行引用按client/audience绑定，不能跨到knowledge；`RuntimeTicketServiceImpl:118-122`只签runtime票 |
| RAG | `7f4cf374b356e287a39b08ea32ad9ad523c4804b`，核查时干净 | `access/server.py:481-560`已有`POST /api/query`纯检索；`gateway.py:717-799`保留证据状态；`auth.py:202-230`与`gateway.py:971-988`严格绑定记录owner/源会话和修订 |

Agent运行身份只保留verification_ref和修订（`identity/runtime.py`、`ticket_binding.py`），原始用户token不进入native运行；知识HTTP却要求knowledge audience的subject Bearer。**当前不能仅添加一个HTTP工具壳完成安全接入**。RAG没有已实现的OBO/委托或MCP服务；本方案增加明确的只读用户委托协议，不放宽现有受众规则。

现有`agent-runtime-performance`的policy/knowledge路由来自已删实现；本change修改该条，不恢复旧Milvus/Ollama。Agent目录已有其他未提交计划/题库，不覆盖。tracing活动change及Change6/7仍有任务未闭合，不能把文档勾选当真实运行；共享文件串行集成。

## Goals / Non-Goals

**Goals:** 默认工具在三图所有轮次可见、具备真实执行器；用当前用户的实时资料权限检索；法规依据可验证、可点开；tracing知识与污染证据隔离；受控/真实/浏览器和性能分别有验收证据。

**Non-Goals:** 新Graph、第五专员、任意子任务工具开放、共享root/机器主体检索、知识写入/入库/删除工具、检索算法与索引重建、默认启用LightRAG融合/额外生成模型、新知识库选择UI或独立检索页面、自动部署/提交/推送/归档。

## Decisions

### D1. 单一受管本地HTTP工具，不新建MCP服务器

新增`src/common/knowledge/{contracts,client,delegation,tool,policy,citations,store}.py`（允许实现时按项目分层拆包，责任不变），稳定工具名`knowledge_search`。复用httpx异步连接与现有观测。`runtime_tools.get_business_tools`注册同一个惰性工厂，导入无网络；tracing只显式添加此工具，不改成导入全部业务工具。

选择HTTP适配器是因为Knowledge API已负责权限、检索、来源与下载；再造MCP服务会增加独立部署和目录准入。该工具不受`capability_mcp_mode=allowlist([])`关闭，不添加普通人员Skill/MCP授权码；读取仍要求知识资源权限。业务许可、删除/停止守卫照常执行，不能因它是本地工具省略逐发送复查。

工具只接收query、可选公开library_codes和受限top_k；无tenant/workspace/URL/token/route参数。服务器默认映射全部当前有权库，显式混合无权库整体拒绝。模型不看到内部token、技术映射或无权库目录。

### D2. 新增短期用户委托，三仓顺序接入

新协议详见[contracts.md](contracts.md)：Agent内部客户端凭自己拥有的runtime execution ref向IAM申请`knowledge`只读委托；IAM实时核验源会话PG权威与Redis存活、企业/成员、修订及资源权限。委托采用256位随机opaque token，只存摘要与受控源引用，TTL≤60秒且不超过源ref/会话期限；固定可用操作，不可登录、生成、切企业、签票或再委托。

IAM新增独立签发scope和严格DTO，现introspect增加独立版本的delegation分支，只让登记的knowledge consumer核验。每次使用都重新核验源身份和权限；不把原runtime ref放宽成跨服务token，不把BFF原始token送进图state。

RAG保留`principal.type=human`的原用户语义并明确记录delegated provenance，使用原源session/tenant/context/revisions构造稳定auth_binding，不将新委托随机ID/到期时间加入owner。原knowledge登录token和服务账号路径继续按各自旧规则工作；新委托只准query/record/source/document读，不准`/api/generate`等操作。

运行端从可信runtime config拿ref，来源代理从当前Agent服务端会话生成/取得同一源session的新ref；只在当前调用内持有委托。ref过期须经正常可信入口更新或拒绝，不能自行延长旧引用。重新登录产生另一session时旧record继续拒绝，UI提示重新检索，不放宽owner。

### D3. 固定纯检索路线、状态和预算

一期固定`POST /api/query`，部署控制`route=pg, workspace=all, expand=true, no_record=false`；保留record用于原文回读。不调用`/api/generate`，不新增路由LLM/Embedding或默认启用LightRAG。模型top_k默认8、最大20（上游最大50），上限由后端配置收窄，选库使用IAM权限到技术workspace的受控映射。

工具总deadline默认20秒，含IAM签发/内省、检索与后置复核；响应上限1MiB、最多20个片段、每片段最多2000字、向模型合计最多12000字、最终引用最多8条、单条公开摘录最多500字。达到任一界限明确truncated/context_limit，片段身份和完整性不随截断消失；不能把缩短后资料称为完整清单。数值由profile固定，性能验收后可升策略版本调整，不在失败后临时放宽。

HTTP/SDK默认0次自动重试；不确定响应不盲重发。用户显式重试产生新工具逻辑调用，先重新核权并受原总预算约束。取消传播到RAG请求任务及PG语句，RAG按剩余deadline设置连接/statement timeout，不只由Agent断开HTTP而让60秒SQL继续运行。只取消本调用，不影响共享池其他用户；残留查询用受控PG会话证据验证。

保留上游`retrieved/complete/partial/ambiguous/missing/stale/service_failure`，再给出规范outcome。当前PG合法空候选也可能是missing，故RAG新增明确empty_discovery reason：只有完整授权成功、来源快照当前有效、真实查询结束且候选为空才映射no_match，不能仅凭空evidence推断。`retrieved`仅支持逐条已验证片段引用，不支持“全部”“唯一”结论；其余明确非可用状态只返回缺失/原因，不把原文送入确定法规回答。没有页码或分数则null+原因，不从chunk_id推导。

### D4. 三图默认工具与实际执行同步改动

| 位置 | 必须改动和保留 |
| --- | --- |
| QA `builder.py`、`nodes.py`、`prompting.py` | ToolNode实际注册；所有意图含chitchat都包含knowledge_search；knowledge与data_query可执行；纯问候提示不主动调用；显式Skill不隐藏通用工具；将“基于常识”的法规路径改成来源策略 |
| 分析 `graph.py`、`prompt_builder.py`、`nodes.py` | 静态ToolNode与模型可见过滤均加入；allowed-tools为空也有检索；无菜单可做知识问答但不能启动任意数据分析；菜单/显式Skill冲突仍在运行前拒绝 |
| tracing `graph.py`、`tools.py`、`publishing.py` | lead的4工具名单增为5；保留`inject_mcp_tools=False`与无文件/执行工具；站点/网格仍确定性、气象/reviewer工具限制不变 |
| 公共包装器/预算 | 将知识工具登记为通用但受保护的本地工具，模型schema、ToolNode、实际执行与重试一致；预算缩减不能静默丢此schema；动态MCP刷新不能替换/重复注册 |

现有纯问候零工具规则及相关断言是明确产品变更，应有新反例证明“schema有、调用0”。不机械把旧零工具断言删掉；保留不主动调用的行为断言并记录基线变化。

### D5. 强制检索采用服务端证据门禁

新增`KnowledgeAnswerPolicy`：具体法规、文号/条款和标准限值请求在任何车道进入时标`retrieval_required`；最终输出也检查新增的具体规定，防止“问题未要求来源但模型自行给限值”。模型结构化声明只是输入，不能作为唯一裁决。服务端把引用ID与真实登记结果核对；未检索时有界补检索一次或返回“未取得可核验依据”，不从常识补文号、条款或精确限值。

QA `visible_result`、分析`finalize_output`、tracing ReportGate之前执行。所有尚未核验的新正文、表格和画布先在服务端缓冲到完整可判定单元；一期允许全答缓冲，不能仅按原问题分类决定保护。扫描覆盖跨chunk文号/数值/单位，无法判定时不先公开。原生messages/values/updates/debug等旁路和模型stream callback也在边界内，不以ui_visible标记或前端隐藏代替阻断。一般概念与纯计算仍可不调用检索，纯问候调用0；增加缓冲后的首正文耗时如实计量，不以进度事件代替。合法非拒答控制必须通过。

有效receipt和引用只证明来源身份。`KnowledgeClaimSupport`另将具体断言映射到已核验原文，检查数值、单位、标准版本、适用范围和否定条件；不得只靠模型自报“支持”。一期不能可靠验证的自由改写降级为有界原文摘录，或明确不能给该确定断言；不额外引入未经授权的新模型裁判。受控模型故意改35为350、换单位/年份/范围/否定词、引无关真实段均须拦截。

从本次工具执行产生可信receipt，不接受模型/浏览器自造`retrieval_required=false`、receipt、来源内容或引用ID。receipt绑定tenant/user/source_session/context/request/attempt/submission、查询摘要、资料版本与状态，来源变化或旧attempt不能重放。服务端先登记再公开ToolMessage或最终正文；登记失败不得公开无可追溯引用。

### D6. 受保护引用存储及原文代理

新增Agent自有引用表/访问映射，复用非owner请求角色、FORCE RLS、事务本地GUC和同tenant复合外键，保存receipt/ref ID、源session/ctx、request/attempt/submission/answer/version、RAG record/evidence索引、workspace/doc/chunk/快照/摘要和保留期。原文摘录是受控正文，和普通统计元数据分列或独立表；不得把内部认证、完整原文写入普通事件。新迁移接实施时head，当前观测为0022，不预占revision。

前端引用只指向自己的`/api/knowledge/citations/{citation_id}/text|pdf`。同源BFF→受保护Agent API由数据库映射确定RAG固定路径，再用本次用户短委托回读。先核验本人会话/企业/context及引用所属会话/回答，随后RAG owner、资料权限和快照验证；不接受浏览器传record_id、evidence_index或目标URL。历史来源读取使用历史/正文权限规则，不因为业务使用权撤销就扩展或收缩既有历史权限；知识资料权限仍必须当前有效。

引用文档名/位置/片段由服务端可信结构渲染，模型只引用ID，不允许Markdown任意地址冒充内部出处。原文复用现有ArchiveReader交互/共享组件（或相同局部阅读模式），有加载、无权、过期、来源变化和重试状态；位置缺失就不显示页码。PDF保留上游白名单、内容摘要和安全Content-Disposition，不向浏览器跳转裸8099或带token链接。响应`no-store`，不允许重定向到任意站点。

### D7. 删除、撤权和历史资料回放

不能只保护“点开原文”。新检索ToolMessage/答案/画布/报告均登记知识引用依赖，原文工具归档及运行checkpoint不保存凭据。摘要、压缩、计算、画布及其再次派生由服务端保存并传播来源依赖闭包，模型删掉可见citation_id不解除依赖；缺依赖信息时不推断安全。复用前按50项一批分批全量验证，任一失败或未知不能只放行前50项。原生threads/state/history、runs回放、BFF历史、工具归档、报告回读和恢复入口均覆盖。受限资料失效时保守拒绝相应受保护结果或整段回放，不从旧checkpoint把摘录重新发给用户/模型。没有可靠细粒度剥离时不尝试文本猜删，不拿“前端隐藏”代替后端拒绝。

在官方镜像先用`identity/langgraph_auth.py:on_thread_read`、create_run及custom HTTP层证明state/history和各run stream/replay的服务端拦截覆盖；当前on_thread_read只有owner校验，尚无知识复核。只读校准凭据只能拿状态，不能借该分支读取含知识原文的state。若某平台路径无可用钩子，必须先在受保护代理建立等价能力并关闭原绕过入口，再交付知识功能；这是一项前置可行性门禁，不留到上线后补。

合法会话仍可执行不依赖失效资料的新问题。重新检索/重建报告使用新结果与新版本，不修改已存报告digest来假装内容未变；历史正文失效状态与真实删除分别保留。线程墓碑优先于晚到查询、receipt登记、引用回读和恢复；RAG原文删除/快照变更在使用边界即时拒绝，Agent受控摘录及引用映射按独立保留期与删除作业清理。不能为了新报告功能全局清理旧数据。

### D8. 溯源独立知识通道与报告兼容

新增受控`KnowledgeReferenceRegistry`，绑定runtime scope、analysis_id、revision和检索receipt；不复用`EvidenceRegistry`。`ReportPlan`只扩展`knowledge_reference_ids`，不允许模型传原文、文号、页码或任意URL。`ValidatedReport`增加默认空`knowledge_basis`及明确的schema/gate版本，按旧/新版本分别计算digest；旧报告原digest保持有效，不回填引用。

ReportGate独立校验引用scope、当前权限、状态、快照、片段摘要与删除，然后服务端确定性渲染“知识依据”摘录+来源。污染正文仍用原claim allowlist，knowledge ID不得进入evidence_ids/claim_ids或提高结论强度。原文中合法的污染归因/贡献率字样按“原文引用”边界验证；不能关闭整个报告安全扫描或允许模型借该段输出自由正文。

`claims.py`渲染、`tools.py`正常/fallback、`publishing.py`发布重建和`_report_is_current`、`report_store.py`存取、画布都使用同一引用上下文，digest覆盖知识结构。读旧报告与新报告、空引用、重试、晚到revision、删除后恢复分别回归；不用新知识功能修复或放宽原tracing数据缺口。

### D9. 观测和性能

工具观测复用`runtime.tool_call`及existing tool attempt/span，服务标签固定`knowledge-api`，工具kind为本地HTTP检索，不归入ipp MCP。分别测委托/IAM、HTTP、实际PG、来源复查、结果治理、引用发布、浏览器原文读取。原文/query正文不进普通日志，采样ID和摘要必须足以对账；缺用量或价格不记0。

默认PG新增服务内回答模型、Embedding、rerank预期为0，但必须由实际调用计数证明，不能把Agent自己的决策/回答模型也记0。工具客户端在并发1/5/10，各35样本×3轮，测受控服务固定延迟后的本地适配新增P95≤100ms（含序列化、客户端处理与引用登记，外部IAM/RAG等待独立测），普通受控调用业务成功率100%。真实20秒总deadline、P50/P95/错误/权限/来源正确率逐项报告；真实检索P95目标须在fixture规模冻结后记录，不能用受控服务100ms宣称真实检索达标。整体三图回归沿用docs/testing规范，不重用旧分数。

### D10. 测试与实施交接

完整映射见[acceptance.md](acceptance.md)；固定问题和可复制实施说明见[handoff.md](handoff.md)。先冻结三仓DTO/错误/操作scope与受控样本，再IAM→RAG→Agent服务/三图→引用UI→组合真实验收。允许按契约并行开发独占文件，身份契约与共享图/迁移接线串行集成。外部仓库实施需要遵守各自AGENTS和CodeGraph；本仓apply不能被理解成自动可写任意仓库。

与Change6共享`lifecycle.py`、引用/正文保留和迁移时串行接线；与tracing共享schema/gate/发布；根依赖锁和langgraph.json不因该工具擅自升级。前端实施读取frontend/AGENTS及当前安装Next文档、DESIGN/UX-CONTRACT与前端设计技能，不重做聊天UI。当前已读Next Route Handlers指南，实际实现必须再以安装版本为准。

## Risks / Trade-offs

- [跨受众凭据不可复用] → 新建最小只读委托；安全验收先于三图发布；不留共享账号兜底。
- [资料搜索效果不等于可核验规定] → 保留完整性/快照/verified；固定原始文档金标，未命中不编造；不承诺本期提升检索算法质量。
- [所有轮次schema增加开销] → 共享精简schema、无导入预热、纯问候调用0；预算不足拒绝而非藏工具。
- [历史/画布成为旧原文旁路] → 引用依赖登记和所有读取/恢复边界复核，无法可靠剥离时保守拒绝受保护结果。
- [报告digest兼容或引用段绕过归因门禁] → 分版本digest和独立受控渲染，旧报告测试/正常与fallback一致性均为硬门禁。
- [取消只断HTTP但SQL仍跑] → RAG任务/连接取消、剩余deadline、实际PG活动和连接回收证明。

## Migration Plan

1. 先在自有空IAM/Agent PG、Redis、RAG合成资料和独立端口完成新协议及迁移；测试保护正式15432/19132和共享RAG，不复制正式凭据，不重新入库。
2. IAM部署兼容的签发/内省扩展并按正常管理面配置内部客户端scope；RAG部署委托只读验证；旧登录和机器主体路径保持原契约。
3. Agent加入引用存储及工具，三图同批发布。新版本健康但上游未就绪时保持工具可见/明确不可用，不对外宣布功能完成。
4. 组合验收后才能进行实际运行切换。正式配置、迁移/部署和业务权限变更由用户后续明确授权；本计划和strict通过不代替上线授权。
5. 回滚按已验证的上一应用版本回退；扩展表不做破坏性downgrade。立即停用签发scope/新委托并等待最长60秒只是辅助，使用端实时拒绝必须有效；不恢复local鉴权、不用旧token或共享服务账号继续检索。保留新旧报告与失败证据。
