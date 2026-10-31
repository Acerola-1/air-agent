# IAM消费者固定交接契约

日期：2026-10-16；这是本轮消费者实施的共同输入。IAM产品基线为8da8ba1127b7ea7aa9c0a9f7289d90cbcc96a368（dev），源码位置 /Users/acerola/Dev/Java/air_agent_iam。文件SHA-256见baseline.json。不得根据供应商身份改变本契约或评估标准。

## 权威与范围
1. remote模式的身份、会话、成员、角色和有效权限只由IAM裁决；业务数据库/本地注册表不得成为第二套授权源。local与remote是启动时选择的部署模式，不是出错后的两级兜底。
2. 请求走HTTP，不读取IAM表、不依赖Sa-Token内部Redis键。业务侧自己的线程归属、偏好、MCP绑定、台账及技术workspace目录仍归业务服务管理；同一文件含身份与业务函数时逐函数拆分，不能整文件删除。
3. 既有49103/391xx/191xx实例不是本轮可写夹具。两个项目分别从冻结IAM提交导出到各自忽略的测试目录，使用自有PG/Redis/库/角色/端口与不同随机秘密。可构建/运行该副本、注入测试配置及合成种子；不得更改IAM原工作区或共享实例。使用前核实端口可用、连接实际身份和资源所有权。I2建议501xx、I3建议502xx，仅作候选，实际值记录manifest。
4. 实际运行配置、内部client注册、SSO client注册和业务权限清单必须各自核对。内部introspection client、SSO client、被核验主体是三类不同凭据。两个客户端注册表不能混用，秘密不得复用、入报告、命令回显、浏览器导出、Trace或Git。
5. 本轮为消费方接入和隔离环境验收，不授权切换现有用户身份源、写正式语料或修改现有库的身份记录。正式切换、历史UUID接管、生产回退及物理拆库单独放行。本轮达标与正式上线分别报告。
6. 不覆盖现有.env或本地凭据清单；为自有环境创建新的忽略配置文件，权限0600，模板只含字段形状。测试权限目录可用受控迁移注册元数据，但用户/角色/成员/资源开通与授予的正常验收流程必须走真实管理API，不能SQL种授权后冒称流程通过。

## HTTP及类型
- POST /v1/internal/identity/introspect：Authorization Bearer为登记内部client；body为subject_token+audience，或execution_context={verification_ref,expected_revisions}+audience，二者互斥。不要把rt-v1票据作为subject_token。
- 人类：schema_version=iam.identity.v1、principal.type=human、realm=workforce。受众、issuer、UUID、active、context_usable、当前企业/成员的一致性和四修订都验证。revisions四字段为user_auth_revision/tenant_auth_revision/membership_revision/session_ctx_version；context_version与session.ctx_version及session_ctx_version一致。
- 机器：schema_version=iam.introspection.v1、principal.type=service_account、realm=service；user/session/membership/revisions为null，current_tenant为固定企业，另有service_account_revision、credential_id、credential_revision及到期信息。机器不是人类Identity，不能套用仅人类identity.schema.json，也不能伪造membership/session/第五修订号。以ServiceIdentityVO和实际响应冻结本地严格验证器。
- inactive：schema_version=iam.introspection.v1、active=false，拒绝且不能回显身份详情。active=true且permissions=[]不表示有资源许可。bool不能当整数，字符串修订、未知schema、错aud/issuer、缺字段/非法JSON即失败关闭，不把HTTP200当授权。
- 只有服务器校验过的身份进入业务上下文、GUC、缓存及SDK可信auth上下文。浏览器的企业/ctx字段只是期望基线，不能替代身份。过期上下文409，不静默套用新企业。
- 网络/协议错误、超时及明确的内部client认证故障以503关闭；主体无效401，有效主体无许可403，旧上下文409，对象越权延续原404语义。上游错误需要明确分类，不能靠异常message字符串猜授权，也不要把服务故障伪装成用户登出。
- 复用HTTP连接，不缓存跨请求的正向授权；同步/异步接口均有总预算，默认单次IAM核验deadline 500ms（包含排队、连接和读取），有界失败，无自动授权重试。导入模块不预热外网。性能采样只压身份/受控传输，不反复消耗真实模型额度。

## 运行票据和执行引用：I2必须实现的桥接
现有rt-v1载荷是固定顺序sid/uid/tid/mid/aud/iat/exp/crev/urev/trev/mrev，不包含verification_ref。签票响应只有ticket/expires_in。IAM不接受任意session_id复查，也不接受rt-v1当subject_token。
- Agent自己的签票门面先以当前人类subject_token、langgraph-runtime受众和include_execution_context=true取得真实引用，再调用IAM签票，验签并比较票据身份及四修订与引用快照完全一致。竞态不一致即409，不登记可用绑定。
- 在Agent服务端专用存储登记票据摘要→引用、身份、四修订和入口到期的短时绑定。原始票据/人类token不落checkpoint/state/Trace。登记入口只允许可信服务端，不能接受浏览器提交的引用。两个进程必须共享该业务侧存储，不能只放BFF内存。
- native Auth验签后必须找到匹配绑定并通过带内部client认证的执行引用实时复核；不能凭有效签名构造引用或继续PG身份查询。入口后仅将绑定的非秘密引用和修订放入可信auth上下文。引用本身不能发起新运行、签票或自选用户。
- IAM引用绑定创建它的client_id及audience。I2的BFF签票/引用登记与native复查使用同一登记的Agent后端internal client（不同进程可配置同一受控凭据，仅本Agent后端可用），不能创建时用BFF client、复查时换runtime client。SSO和I3客户端仍各自独立。若未来拆成不同IAM client，必须另有明确的受认证broker/委托协议，本轮不发明跨client引用转移。
- 超过60秒但会话仍有效的已受理运行可继续实时复核；新请求/恢复重新认证及签票。撤销/改密/切企业/ABA后旧引用拒绝，下次外呼增量0。
- 现有mt-v1迁移和rs-v1校准是独立的窄业务服务能力，不能被误删或伪装成人类，也不能成为remote用户鉴权失败后的fallback。保留路径/租户/投影限制和独立密钥。当前IAM未提供这些业务专用签票端点，不能发明路由。

## SSO已知文档差异：先探针固定，不猜接口
当前OpenAPI部分历史SSO描述与实现不一致。以下来自冻结源码/现有回归，仍须在自有实例G0实际核对，不能宣称本规划已经实测：
- GET /v1/sso/authorize（也有/web-api/sso/start）要求中央浏览器会话，返回303，回调query是ticket和state；不要按旧文档假设code/PKCE已实现。
- POST /v1/internal/sso/exchange使用SSO client凭据，body必须ticket/redirect_uri/state；当前OpenAPI的SsoExchangeRequest缺state，此处按DTO/实现要求补全消费者请求。
- SSO ticket默认30秒，运行票据最多60秒，不能混同；过期值由实际配置确定。SSO客户端不在通用internal_clients表中。
- 消费者自行持有一次性state，绑定登录发起方/精确回调/客户端，callback拒绝重放和错误state，兑换仅服务端；随后清理URL中的ticket。JS和localStorage不得持有IAM token或内部秘密。
- Cookie不按端口隔离；消费者使用不同cookie名称、host-only/HttpOnly/SameSite，生产Secure；每个并行验收使用独立浏览器context。不能靠不同localhost端口认为cookie天然隔离。
- source locator：IAM的SsoController、SsoExchangeDTO、SsoAuthorizeServiceImpl、SsoExchangeServiceImpl和BrowserSessionIntegrationTest；真实差异记录contract-probe，不在消费方静默发明新IAM协议。

## I2身份/业务分离前置
Python users.is_active、角色/权限和外键与Java iam.users.status/realm等并非同构。不能把Flyway V1套到Python库，也不能换DSN即宣布接管。
- 本轮只对隔离夹具建立与IAM同UUID的业务引用投影；本地投影只服务FK/RLS/展示，不存有效会话/角色授权，不能授权。既有历史UUID不匹配时不自动同名合并，正式启用受阻并给出dry-run映射/差异清单。
- 原业务权限如agent:operations:read、agent:content:read、agent:metrics:read、agent:replay:manage、agent:pricing:manage、mcp:binding:*和followed:*不全在IAM内置目录中。I2输出逐码清单、作用域、原可授规则及兼容请求，并在自有IAM副本的受控测试迁移中注册用于实证；不改共享IAM，不用platform_admin=true或“所有已登录用户”绕过。共享IAM发布业务权限清单仍是正式切换前置，缺少该发布不虚报生产就绪。
- 旧身份CRUD在remote部署必须受控代理到IAM或明确关闭并引导IAM后台；业务API不可整体转发到IAM。关注城市、MCP连接绑定、反馈、台账、价格及观测仍由Agent处理。

## I3资源边界
- 知识库核验受众固定knowledge。精确knowledge:lib:<library_code>:read加当前企业才可选择库，不按角色名/类似前缀推导。
- 显式(tenant UUID,resource code)→本地workspace映射；技术目录只决定实际PG/Qdrant/Neo4j namespace、实例及就绪，remote不读取本地principals/grants作为授权。
- all仅展开有权且技术就绪库；显式混合任一无权/未知库整体拒绝。来源、文档、导出、记录、缓存命中、重试及生成前同边界复核；撤销后不得因答案已缓存而继续。
- 人类和机器主体分别构造上下文。记录/缓存包含主体类型、主体ID、企业、必要修订/上下文、权限摘要及映射/语料版本；机器不编造四修订，依靠每边界新核验精确许可。实例密钥只在网关到LightRAG一跳使用，IAM主体token绝不透传实例。

## 证据与评估
只用“本轮实施”或用户给定匿名编号，不读历史模型身份/评分表，不猜身份。首次交付冻结后再修复，评分30/25/20/15/10；协调者补修不记实施模型成果。基线测试必须新跑并记录具体失败nodeid，不能从历史测试数量推断当前通过。真实IAM/受控HTTP/真实存储/实际SDK/真实配置模型/浏览器分别标注，不相互冒充。冻结后新增门槛必须另记计划变更，不能事后补题扣分。
