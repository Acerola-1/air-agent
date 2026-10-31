# 三仓接口契约 v1（计划）

状态：待实施。下面标“新增”的协议尚不存在；不得将示例当可调用接口或把占位符当凭据。此文件是三仓共同输入，实施前冻结为DTO/OpenAPI/契约样本；字段变化先更新此契约及三方验证器，不能各仓自行猜测。

## 1. Agent模型工具：新增knowledge_search

```json
{
  "query": "GB 3095-2012原文如何规定二级标准？请检索对应表格和条文。",
  "library_codes": null,
  "top_k": 8
}
```

- query：去空白后1—2000字符；不为空；原文作为受控请求内容，不进普通日志。
- library_codes：null为当前用户有权的全部活动库；显式数组1—10个公开资源code，逐项规范化/去重并整体授权，空数组返回参数错误；不能传技术workspace、tenant或任意地址。
- top_k：默认8、整数1—20，服务端可收窄；不表示枚举完整度。route、expand、no_record由后端固定，模型不能覆盖。
- schema默认提供，不要求先选Skill或MCP；无身份、未获图业务许可、无知识权限时执行拒绝。

返回`schema_version=agent.knowledge-search.v1`：

| 字段 | 约束 |
| --- | --- |
| search_id / receipt_id | 服务端生成的非秘密ID；绑定本次真实调用，不接受模型指定 |
| outcome | `ok / no_match / permission_denied / context_changed / evidence_unusable / unavailable / timeout / cancelled / unknown` |
| retrieval_status | 原始`retrieved / complete / partial / ambiguous / missing / stale / service_failure`或null及原因，不据HTTP状态合成complete |
| completeness | `candidate_only / complete / incomplete / unknown`，只对本次已说明范围有效 |
| queried_libraries | 实际获准公开资料code列表；无权/未知库不回显目录 |
| references | 最多8个已登记来源；包含citation_id、doc_name、verified、excerpt、location、source_version、source_sha256；location不具备则null+not_provided，不制造页码 |
| truncated / limits | 原始与给模型长度/片段数、触发上限及context_limit原因；不补造未测字节数 |
| warnings | 白名单错误/局限码和面向用户中文说明；不含原始HTTP错误、secret、上游URL或prompt |
| timing | 有效的阶段计时/缺失状态；总耗时按同进程单调时钟；成本不混在无证据时间推算中 |

references是对模型可见的最小结果；RAG record/evidence索引、技术workspace、源session和凭据在受保护服务端映射保存，不直接把完整上游返回体透传模型。内部按`(workspace, chunk_id)`或枚举条目的真实复合身份去重，不能只按doc_name或chunk_id。

`ok+retrieved`只允许引用已核验片段，不允许“查全全部条文”。上游partial/ambiguous/stale/service_failure映射evidence_unusable（服务故障也可unavailable），references不含可供确定法规断言使用的原文。现PG合法空候选也可能返回missing，故新增`retrieval_reason=empty_discovery`：只有当前资料授权和来源快照有效、实际查询正常结束、候选为空才允许missing映射no_match；其他missing仍为evidence_unusable。不能仅凭空evidence推断无命中成功。

真实引用还必须支持具体断言。具体法条/限值的数值、单位、年份版本、适用对象/范围与否定条件应和原文对应；引用真实但内容无关、35被改成350等不能通过。无法可靠校验的自由改写仅允许降级为明确原文摘录或拒绝确定断言。

## 2. IAM新增签发接口

`POST /v1/internal/knowledge-delegations`；内部客户端认证复用当前IAM登记机制，新增精确scope `knowledge-delegation:issue`；不对浏览器开放。

请求版本`iam.knowledge-delegation-request.v1`，必填：

```json
{
  "schema_version": "iam.knowledge-delegation-request.v1",
  "execution_context": {
    "verification_ref": "<源引用，不是Bearer>",
    "expected_revisions": {
      "session_ctx_version": 1,
      "user_auth_revision": 1,
      "tenant_auth_revision": 1,
      "membership_revision": 1
    }
  },
  "source_tenant_id": "<当前可信企业UUID>",
  "operation": "query"
}
```

operation仅`query / record:read / source:read / document:read`。服务端从引用取user/session/tenant/member，比较而不是信任source_tenant_id。目标audience固定knowledge、源audience固定langgraph-runtime、签发方必须等于该ref登记client；禁止原始subject_token、任意audience、任意权限集合或目标URL参数。

响应版本`iam.knowledge-delegation.v1`，必填`delegation_token`（256位随机opaque，服务端只存摘要）、`token_type=Bearer`、`audience=knowledge`、`operation`（单一获准操作）、`expires_at`（带时区）、`source`（user_id/session_id/tenant_id/membership_id/context_version/四修订）。TTL最长60秒且≤ref.valid_until和会话绝对期限。source仅在可信服务器间使用；Agent在收到后比对本次scope，差异立即拒绝。

无知识有效权限、企业未开通或无活动资源时不签发；细粒度资料映射仍由RAG裁决。签发过程重查PG源会话状态和Redis存活；禁用/注销/改密/切企业/修订漂移立即失效。storage使用IAM自有表/受控TTL缓存，迁移按该仓当前head新建，不修改已执行revision。

## 3. IAM委托内省与RAG委托身份

复用`POST /v1/internal/identity/introspect`，为新增opaque委托增加严格分支。RAG提交`subject_token=<委托>`、`audience=knowledge`及`operation`；operation为新可选请求字段，仅委托分支必填，旧human/service请求DTO行为不变。调用者必须是登记且获准的knowledge内部consumer，Agent签发scope不能兼任任意consumer。

新增响应`iam.knowledge-delegation-introspection.v1`：

- `active`、`issuer`、`audience=knowledge`、`operation`；inactive只含白名单reason及必要上下文，不泄露其他用户信息。
- active时`identity`采用完整、严格验证的现有`iam.identity.v1`人类投影字段结构（user、源session、current_tenant、membership、context_version、permissions、revisions、issuer、knowledge audience等）；permissions只含实时有效`knowledge:lib:*:read`，保留完整四修订，不直接复用当前包含其他tenant权限的全量投影。query/record/source/document采用完全相同知识权限投影，operation另校验；不是machine身份，也不声称新建了knowledge登录session。
- `delegation`为issuing_client_id、source_audience、source_session_id、operation、expires_at。此来源标记与登录态解析隔离，consumer不能把未知版本降级成旧human响应。
- 每次内省重读源状态、四修订、有效知识资源权限及企业开通；授权不可只由opaque存储的签发快照决定。

RAG仅对query、record读、source读、document读接受对应单一operation的委托；不得query token读PDF、source token查询，不能用于login/logout/switch/generate/写入。request进入和检索/回读前后分别复核权限；原query业务无权/混合非法库时，真实检索请求增量0。

auth_binding沿用原principal/user/source-session/tenant/context/权限修订/目录策略修订，不加入委托随机ID、过期时间或单次operation（否则source续签无法读query record）；每请求operation单独验证。保留稳定的delegated来源类型/签发客户端校验，不与另一独立knowledge SSO会话互通。

## 4. 复用及受限扩展的RAG接口

主调用：`POST /api/query`，Bearer为本次query委托，服务端固定：

```json
{"question":"<query>","workspace":"all","route":"pg","top_k":8,"expand":true,"no_record":false}
```

指定公开库时由RAG/IAM受控映射成workspaces，模型不能写该数组。保留现返回`user_id, record_id, workspaces, policy_revision, result`，result含snapshots/status/failures/stale/evidence/per_workspace/evidence_sha256。PG evidence当前没有统一页码。新Agent DTO只规范有证据字段，不预设额外rank/score。

已存在来源接口：`GET /api/source/{record_id}/{evidence_index}/text|pdf`、`GET /api/document/{record_id}/{doc_id}/text|pdf`；每次使用对应operation新委托并执行原owner/权限/快照校验。原文较大时RAG增加受控text分页（offset≥0、limit默认/最大8000字符）与剩余deadline/取消传播，不削弱原来的文件白名单和hash检查。PDF按流式/安全Range提供，不缓冲无界文件，不跟随任意redirect。

新增仅委托可用的`POST /api/knowledge-references/validate`（operation=`record:read`），请求为同一授权owner下至多50个record_id/evidence_index/source_digest复合项；返回每项`valid / forbidden / stale / deleted / expired / unknown`，不返回原文。仅当前授权项可返回当前digest；forbidden/unknown不回digest。deleted/expired必须有本owner墓碑或确定保留期证据，不存在记录只能unknown；委托到期不是引用过期。当前RAG没有完整record墓碑/过期契约，实施须显式实现必要生命周期标记或返回unknown，禁止猜补状态。先核对全部项owner，跨主体/企业整体拒绝，不可借批量验证枚举他人资料。超过50项由Agent分批**全量**验证后才放行，不能只验证首批。

### 来源摘要与deadline字节规范

- `source_digest`/Agent的`source_sha256`表示同一片段描述摘要：对`{schema_version:"knowledge.source.v1", workspace, doc_id, chunk_id, kind, source_version, workspace_snapshot, location, content}`按RFC8785 JCS规范化为UTF-8字节后做SHA-256，输出小写64位hex。content保持服务端原片段Unicode文本，不做NFKC、去空白或换行替换；缺location为null，不补页码。workspace_snapshot为RAG验证使用的原始版本化快照对象；非法/非有限JSON数值拒绝，不能各仓自定义字节格式。
- RAG负责从真实来源构造并返回digest；Agent保存/复核同一描述，不能用模型生成的content参与源摘要。契约样本须含中文、换行、null位置、跨库同chunk_id和快照变更测试向量，三仓一致验证。上游`evidence_sha256`为检索批次原摘要，另字段保留，不混成单片段digest。
- PDF `document_bytes_sha256`是原文件bytes的SHA-256，和片段JSON摘要分列；文件不具备可信hash时不伪造。
- 新增受限请求头`X-Knowledge-Remaining-Ms`，整数1—20000，缺失用服务端配置上限；每跳取本地上限与收到剩余时间的最小值，并按本进程单调时钟扣除已用时间再下传。不跨进程相减monotonic绝对值，不信任客户端扩大上限。IAM使用自己更小的硬deadline，PG按剩余预算设置连接/statement timeout；原文每次分页/PDF范围读取也是独立有界请求。

## 5. Agent新增受保护引用入口

浏览器只访问同源`/api/knowledge/citations/{citation_id}/text?offset=0&limit=8000`或`/pdf`；BFF服务端调用Agent API对应`/agent/knowledge/citations/...`。引用ID为不透明服务端标识，但不能以难猜代替授权。

Agent复查现有cookie会话、企业/context、本人thread/answer/citation和源session，内部映射record/evidence，再请求新委托。原文读取不以当前Agent业务使用权替代历史读取规则，也不以业务资格代替知识权限。源会话/权限变化无法重用旧记录时返回可解释失效；需要重新检索不能自动扩大来源owner。

前端收到：文档名、真实位置或缺失状态、已授权文本页/安全PDF流、范围和后续分页状态；无URL/token/内部身份。`Cache-Control: no-store`，不同用户/企业/图/epoch不得命中旧原文缓存。点击原文和加载下一页均重新核权，不做整库/整PDF预取。

## 6. 统一错误和观测

| 情形 | 服务HTTP语义 | 工具/界面语义 |
| --- | --- | --- |
| 非法参数、非法操作/协议 | 400/422 | validation_rejected，未发业务查询 |
| 源身份无效/委托失效 | 401 | 明确登录/身份失效，停止重试 |
| 无图业务权/无资料权/未开通 | 403 | permission_denied，不用无命中冒充 |
| context/修订/来源快照冲突 | 409 | context_changed或evidence_unusable；不换企业继续 |
| 他人/不存在引用 | 404 | 不泄露owner或资料存在性 |
| 自己的引用已过期/删除 | 410或已确定删除语义 | 明确失效，不回读旧摘录 |
| IAM/RAG暂不可用 | 503 | unavailable，保留来源，不回退旧身份 |
| 总deadline | 504 | timeout，取消状态需独立确认 |
| 合法空结果 | 200+明确空状态 | no_match，不编造法规依据 |

每逻辑调用/尝试使用现有可信request/submission/attempt关联；record/source校验与查询不能混成多次用户提问。事件仍走当前白名单；确需新增字段时同步登记、Python/admin/TS验证器、生成副本、样本与OpenAPI，不自由写入payload。委托及原文不得进错误正文、日志、事件、Trace或模型schema。
