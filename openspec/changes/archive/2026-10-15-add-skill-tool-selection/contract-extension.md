# Change4契约增量

本文件冻结本change的接口与兼容口径；实现及验证情况以最终独立验收报告为准。沿用agent-events-v1主版本，增加受控类型/字段；不放宽现有拒绝未知字段、敏感键、来源与大小限制。

## 1. 目录

原生GET /capabilities?graph_id=<六图之一>&limit=200&cursor=<opaque>；BFF GET /api/capabilities，参数相同。limit范围1–200，单页JSON最大256KiB，描述最多500字符；非法参数422。身份来自既有可信认证，不接受query/body传tenant/user/凭据或MCP地址。每次读取实时验证身份/授权；服务端游标签名且绑定完整scope/修订/图，失效409。

响应字段：schema_version="agent.capability-catalog.v1"、graph_id、catalog_version、generated_at、skills[]、mcp_services[]、tools[]、next_cursor。Skill项为id/name/description/material_version/status/required_tool_ids，status为available/unavailable；菜单不匹配的Skill可不返回。服务项只返回有权知晓的server_key、中文display_name和available/not_configured/unverified/temporarily_unavailable状态；无权项不返回、不泄漏数量。工具项为稳定id/server_key/name/display_name/description/status。目录版本是安全材料和scope修订的摘要，不含秘密、内部路径、外部subject、绑定引用或schema原文。

单服务超时2秒，总5秒，最多3个并发。局部故障返回200+明确服务不可用、可用部分可选；身份/授权服务失败503，无可信目录时不沿用缓存授权。未登录401、已撤销/非活跃或禁止的内部主体403。原生custom app也必须认证，不能因BFF有鉴权就放开直连。

## 2. 提交选择与快照

AdmitRequestIn新增可选capability_selection，字段严格验证：

```json
{"schema_version":"agent.capability-selection.v1","skill":{"mode":"explicit","skill_id":"skill:basic-qa:example-skill"},"mcp":{"mode":"allowlist","tool_ids":["mcp:business:query"]},"catalog_version":"opaque-version"}
```

这是结构示例，example-skill不表示已存在。skill.mode=auto时skill_id必须null；explicit必须合法ID。mcp.mode=auto时tool_ids必须空数组；allowlist允许空数组表示不用MCP，最多20个唯一工具，规范排序。单ID最多200字符；禁路径遍历/控制字符和未知字段。未传整个字段归一化auto/auto，catalog_version为空且只由服务端取当前材料；显式选择必须带当前catalog_version。提交字段为不可信意图，不能据此授予工具权限。

受理成功沿用现有响应并增加capability_selection：selection_id、selection_digest、digest_version=2、规范skill/mcp、材料版本与selection_source(user_explicit/automatic)、validation_time。服务端生成selection_id，引用本身不是凭据。正文、身份、能力版本和选择摘要受理时固定；scope与request/attempt/submission均校验，未知引用404，跨scope404，内容或选择冲突409。catalog过期409，图/菜单/明确依赖冲突422，越权403，授权基础设施不可用503。错误包含稳定code和安全中文message，不返回其他租户信息。

选择写入、request.accepted、选择事件和outbox同事务。v2摘要覆盖原content/graph/operation语义与规范选择，不对JSON键序敏感。旧v1仅重放旧auto语义，不更新原hash；新旧记录不能使同一预留创建多个平台运行。旧回读selection_status=not_collected；新记录collected。不允许补造历史用户选择。

## 3. 原生运行与BFF一致性

BFF在既有受理结果中取可信引用；原生metadata可带capability_selection_id/digest用于核对。原生hook须同时核对metadata、kwargs.config.configurable与顶层config.configurable的所有出现值：重复不同值、改选择、错graph、错submission/attempt都在模型/MCP前拒绝。运行上下文由数据库快照重建，不消费客户端伪造快照。原请求不声明选择时仍以受理默认auto快照为准。

同submission重试继承；resume原attempt只继承旧计划且实时复查，不能换计划；regenerate新submission可显式更改，保留逻辑request。旧客户端与旧记录兼容须有单独样本。flag关闭时新非默认选择返回409 capability_selection_disabled，不能静默忽略；已受理计划仍执行保护。

## 4. 业务数据与权限

新增业务域选择快照的UUID主键，带tenant_id/user_id/membership_id及可信request/attempt/submission/graph复合关联、schema/digest版本和安全材料指纹；FORCE RLS与非owner实测。继承当前线程本人读取/企业运营分级权限，不新增“知道selection_id即可读”旁路。普通人不能看其他成员明细，服务主体不得借目录或快照读取个人正文。

删除与既有thread/request墓碑关联；父子引用、晚到事件、resume、恢复旧备份均不得复活选择。迁移追加当前单head之后，升级/从零与既有数据兼容均验证；不改身份表/现有已执行revision。选择表不是身份目录，未来拆IAM时保留在业务侧。

## 5. 事件扩展

新增capability.selection_accepted：enabled=true，唯一source=server，可信受理端写入；browser/runtime直接冒认拒绝。required payload为selection_id、selection_digest、graph_id、submission_id、skill_mode、mcp_mode、selection_source；optional为skill_id、tool_ids、material_version。长度与上述受理限制一致；ID/digest只接受规定格式，tool_ids上限20、不允许嵌套任意对象。此事件只计选择，不计调用量；event幂等键由submission与selection唯一关联，事务重试不重复计数。

runtime.span在已有白名单上新增可选selection_id、selection_digest、submission_id，保持其余stage/status/skill_id/version与selection_source语义；对选择的关联必须在服务端scope内核验。matched/selected/loaded/executed各自产生实际事实，manual来源不得在loaded→executed时丢失。工具调用沿用真实attempt/重试观测，必要的选择关联落受控列/安全元数据，不复制调用正文。

同步docs/contracts/agent-events-v1登记/JSON schema/样本，src/admin/TS三份validator及生成副本；增加正例和browser、未知字段、string/bool/负数/嵌套secret等负例。不可因为新增字段放开全部payload或接受浏览器上报真实使用。旧事件重放、删除、outbox及价格/Token计算保持兼容。

## 6. 协调者收尾明确的边界

`GET /agent/feedback/attempts/resolve`的platform_run_id改为可选：指定时查本人线程的对应提交；省略时只读该线程本人最近已确认映射的提交。返回实际selection_id/selection_digest及规范选择，删除或越权404，旧记录仍not_collected。BFF确认只在request+attempt+submission三项均匹配平台metadata时补映射；未知结果不得降级已确认的同一提交。历史页不使用草稿或hash推算受理事实。

retry/resume省略选择在幂等查找前归一化为原计划；相同提交重放一致，显式改变计划409。regenerate显式Auto确实切回Auto，不被继承覆盖。MCP-only与显式空集合也必须校验当前catalog_version，工具存在性以当前成员实时授权发现为准，不能查进程全局目录。

DeepAgents显式Skill必须在首次模型请求前确定性加载完整指定规则并校验材料指纹，追加规则后继续校验包含工具定义的完整请求预算；不能等待模型自愿调用find_skill。loaded与executed保留manual及同一tenant/attempt/submission关联，禁止消费另一提交的迟到缓存。

BFF将本次受理使用的submission_key覆盖写入平台metadata.ledger_submission_key，作为断流前未收到run_id时的相关性提示，不作为授权凭据；本人线程的运行列表匹配本次键后仍须读取平台权威终态。其他提交的终态或带不同键的活动运行不得被误认。
