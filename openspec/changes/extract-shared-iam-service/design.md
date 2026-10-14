# IAM 抽取设计与可行性结论

## Context

动机见proposal；已核实入口、表归属、迁移和知识库事实见[source-audit.md](source-audit.md)。Agent已经具备可撤销会话、企业组织、角色和真实隔离基础，**适合迁移复用，不需要推翻业务身份语义**。新的独立项目确定为`/Users/acerola/Dev/Java/air_agent_iam`，部署域名未定。本轮仅规划，不启动Java/Redis、不连接或修改正式身份库、不切换Python消费者。

3/5已由协调者补齐，本地证据与模型归属分开：03=58分/131分钟，04=55分/83分钟，模型最终未独立完成；管理183通过，根362通过及5既有失败，前端41通过。Change3剪贴板实机、生产MCP及生产负载条件仍保留。

## Goals / Non-Goals

**Goals：**唯一身份权威；HTTP不透明Bearer契约；即时撤销及四修订；用户建档/企业/组织/成员/角色/审计后台；Agent与知识库均可用；可逆分期；通用资源开通及服务账号契约先冻结。

**Non-Goals：**本轮不实施；不在IAM保存业务对话、原始知识语料、关注城市或模型价格；不重写知识库local模式；不把所有历史OpenSpec强行归档；不在首包引入多账号真实数据体系、任意MCP市场、完整OIDC/MFA或账单平台。SSO是本次接入需要，原路线图“SSO不属于第一change”的历史限制不再代表此新需求。

## Decisions

### 1. 技术可行，采用有状态会话；Sa-Token是组件而非完整用户服务

建议JDK21 + Spring Boot4 WebMVC + Sa-Token **1.46.0候选**的`sa-token-spring-boot4-starter`，RedisTemplate适配与版本一致。Spring Security仅复用crypto模块做bcrypt，避免无必要同时启两套Web安全过滤链。用户/企业/RBAC/四修订/审计/SQL约束仍需业务实现，不能以StpUtil.isLogin替代。

2026-10-14官方核实：

| 项目 | 可确认结论 | 实施门禁 |
|---|---|---|
| Sa-Token Boot4 | 官方1.45.0发布说明明确引入Boot4 MVC/Reactor starter；当前官方示例及中央仓库可查1.46.0 | 不是套用Boot3 starter；锁定实际Boot4补丁、starter/Redis插件、Jackson3依赖树并执行最小启动/序列化/重启会话测试 |
| JDK21 | Boot4.0官方系统要求最低Java17，JDK21在兼容范围；新框架涉及Spring7/Servlet6.1/Jackson3 | 在真实JDK21构建与启动，不据文档声称本机Java工程已验收 |
| Python bcrypt `$2b$` | Spring官方BCryptPasswordEncoder文档支持2a/2b/2y；当前Python用UTF-8、rounds10 | 对原始无`{bcrypt}`前缀直接matches；DelegatingPasswordEncoder需显式兼容适配。冻结ASCII/中文/错密码/72字节边界向量，不改现有密码、不打印哈希 |
| Redis | 仓库已有Redis7，版本满足当前插件Redis6+特性 | 现有是LangGraph基础设施且无已配置持久卷/AOF；IAM独立实例/ACL/命名空间与持久化策略，不能直接flush共享Redis |

官方来源（阅于2026-10-14，版本不是未来自动升级指令）：

- [Boot4 starter发布](https://sa-token.com/blog/release/sa-token-v1-45-0.html)、[当前集成示例](https://sa-token.com/start/example.html)、[1.46.0中央仓库starter](https://central.sonatype.com/artifact/cn.dev33/sa-token-spring-boot4-starter)、[Boot4依赖BOM](https://central.sonatype.com/artifact/cn.dev33/sa-token-spring-boot4-dependencies/1.46.0)。
- [Boot4系统要求](https://docs.spring.io/spring-boot/4.0/system-requirements.html)、[BCryptPasswordEncoder](https://docs.spring.io/spring-security/reference/api/java/org/springframework/security/crypto/bcrypt/BCryptPasswordEncoder.html)。
- [Sa-Token Redis集成](https://sa-token.com/up/integ-redis.html)、[JWT模式比较](https://sa-token.com/plugin/jwt-extend.html)、[SSO模式三](https://sa-token.com/sso/sso-type3.html)、[SSO开放接口](https://sa-token.com/sso/sso-apidoc.html)。部分网页抓取失败时以成功的官方检索摘要/发布说明为限，未做本机Maven解析或Java运行验证。

**纠正“Sa-Token默认Redis”的说法：框架默认是内存，需要显式引入Redis适配并配置连接。**

| 方案 | 状态与即时撤销 | 本项目决定 |
|---|---|---|
| 不透明随机token + Redis会话 | 服务端持有映射，支持注销/踢下线；仍需实时读当前修订/权限 | **推荐**，消费者不需要JWT，减少两套真相 |
| JWT Simple | 官方定义主要替换token风格，登录/Session仍保存在Redis，支持踢下线 | 能用，但未减少introspection/存储，不增加当前收益 |
| JWT Stateless | 无服务端Session，单靠验签到期不能立即撤销单用户token | **不选**；加jti撤销表/账号版本+每次权威查询可补偿，但已恢复有状态复杂度；短TTL仍不是即时撤销 |

多账号仅预留`principal.type/realm`及客户端命名空间。人类workforce、业务service_account、internal_client分别处理；平台/企业角色是授权层，不把不同角色拆成重复账号。未来customer独立StpLogic/命名空间由单独change实现。

### 2. 权威在IAM主库，Redis不能制造旧授权窗口

IAM PG保存账号、成员、权限、会话撤销/期限及修订，Redis提供Sa-Token会话/索引；introspection每次从主库读当前状态，禁止把权限正向缓存至Redis或进程内后直接放行。框架登录态检查之后仍必须通过统一权威核验；管理API同样适用。

- 登录：PG身份/会话提交及审计可靠后才能成功；Redis初始化失败不返回可用token，并撤销或清理未完成会话。PG未提交而Redis残留不能通过introspection。
- 撤销/停用/改密/授权回收：PG状态/修订+审计+清缓存outbox同事务提交；随后旧Redis映射也不能放行。Redis清理失败可重试，不能撤销成功后等待缓存过期才生效。
- Redis宕机/缺失会话：拒绝或503，不能从PG重建已失效身份而静默放行。Redis重启后可要求重新登录；不能从旧快照复活凭据。具体恢复策略在最小启动测试中固定。
- Sa-Token标准Redis结构可能含令牌索引，**不承诺引入框架后所有存储天然只存摘要**。PG会话/服务令牌摘要保持；Redis网络/ACL/备份按凭据存储保护，日志不输出key。若后续要求全部摘要化，需自定义DAO并证明注销/续期一致性，不能凭空声称已实现。

### 3. HTTP热路径与缓存边界

现有`fetch_identity_state`异步与同步都替换为同语义HTTP客户端；线程归属、业务绑定及业务PG连接保留并移出混合命名模块。新增调用边界见http-contract第4节。

- **建议验收目标，尚未实测**：同机暖连接introspection P50≤10ms/P95≤30ms，业务前置核验新增P95≤50ms；分别测无缓存读、并发与故障。连接池保持连接，连接预算100ms、总deadline500ms、pool等待50ms（并入总deadline），无自动重试风暴。局域网部署须重新测，不将本地目标当跨机承诺。
- 单个请求内同一授权决策可传不可变快照，避免依赖注入重复查询；同一run的后续外呼/重试视为新受保护操作，不复用上一次成功。正向权限缓存TTL=0，不共享跨边界singleflight结果。
- 可缓存连接/TLS/DNS、静态菜单文字或资源目录展示；展示缓存不提供授权。拒绝短期缓存也可能延迟重新开通，首版不缓存身份允许/拒绝结果。事件通知只加速界面/缓存清理，不作为撤销正确性条件。
- timeout/5xx/无效JSON/字段缺失/TLS失败统一业务503与停止后续调用；明确撤销401、停用/无权403、上下文冲突409、跨对象404。准入失败关闭；已经产生的答案遇观测写失败仍按3/5规则处理，两者不冲突。
- 原始入站token不持久化。IAM发非授权verification_ref供内部客户端实时复查，绑定原会话/修订/受众；短rt票据仅控制准入，长运行不因60秒入口TTL误停。详见HTTP契约，不自行设计“永久运行凭据”。

同一流程可能有N个顺序外呼，身份代价近似累加各次核验延迟；报告调用次数与总占比，避免只测单次/me。不要为降低此开销改成5分钟授权缓存。

### 4. 双库RLS与外键处理

IAM请求角色、迁移owner与业务运行/worker角色分开，消费者撤销对身份表SELECT/UPDATE。两库各自保留事务本地GUC/数据库范围约束，IAM可以过渡保留`air_agent.*`名称，不在同轮全面改名。

业务侧不再读取`air_agent.login`作身份搜索旁路；`platform`不能从HTTP自报或platform_admin一键映射所有业务数据可读。运营/原文/重放/清理仍按独立业务权限设置窄GUC。

物理拆库时建立最小本地引用目录（无密码/会话/权限）：user_ref、tenant_ref、membership_ref(id,tenant_id,user_id)，由可信实时身份或经认证迁移作业维护。必要FK重新指向引用，保留同租户复合唯一约束；业务内部request/attempt/answer/observation FK不动。投影只保证引用完整性与显示，不能授权；实时身份合法而新投影尚无行时可在业务事务内幂等建引用。历史身份未知则隔离，不默认归当前企业。

当前followed_regions锁身份父行改锁本地偏好聚合根或稳定作用域advisory lock；user/me和列表的关注城市联取改业务接口。Identity.user/session ORM依赖改成不持久化密码的DTO，不能为了少改代码伪造ORM行继续触发flush。

先用FK/策略/视图/函数/触发器/权限全清单证明无残留，再撤身份DB连接及授权。使用前向迁移，保留UUID、不重新seed现有账号，不删原迁移历史。删除身份不级联删除账本/费用，业务删除保留期另管。历史audit/migration_ledger按事件域归属，见source-audit。

### 5. rt-v1与内部端点过渡

首期**保留rt-v1的字节格式**：固定Python→Java与Java→Python签验向量，验证UTF-8/base64url无padding、kid、HMAC签名输入、四修订、时钟偏差/上界、错误受众和重复字段拒绝。密钥通过受保护渠道配置，不复制到文档/提示词。

这减少入口兼容风险，但不表示Python完全无改动：其PG实时查询必须改HTTP。最终runtime可将rt-v1作为opaque subject_token交IAM验证，不再直接解析Sa-Token内部格式。过渡保留旧rt解析器只用于已明确的发行版本，全部仍读同一IAM权威，无自动双验签OR放行。

新IAM内部端点使用每服务独立client credential与scope/audience；X-Internal-Token在兼容门面暂留，按窗口退役。台账confirm/claim/健康等业务内部API留Agent，rs-v1校准/mt-v1迁移不升级为人类或知识库通用令牌。

### 6. 浏览器SSO与后台工作量

推荐中央登录+各应用host-only BFF cookie，精确回调+一次性ticket HTTP兑换，Sa-Token SSO模式三/NoSDK可作为底层；不依赖Python共用Redis或解析Java对象。域名未定先不开放外部Origin，不将SameSite=None或宽Domain设成默认。具体cookie、CORS、ctx CAS见HTTP契约第8节。

已有Next.js后台不必用Java重写页面。将身份页面/会话壳复制并归属IAM的独立`web/`模块；业务后台保留业务导航，身份入口可跳中央管理。不得搬走整个admin/web后丢掉行政区/关注城市。界面延续中文和现有行为，不在此做视觉重设计。

| 工作包 | 可复用与实际改造 | 相对量 |
|---|---|---|
| 用户建档/资料/改密、登录/选择企业/菜单 | 复用现有表语义与页面；Java重写查询、校验、DTO与会话 | 中 |
| 企业、部门、成员、平台/企业角色、权限树 | 同租户复合约束、无环/最后管理员/可授边界、四修订与审计 | 大，核心正确性工作 |
| SSO/BFF、续期、内部客户端、服务账号 | 部分新增；跨前端全局会话/旧页冲突 | 中到大 |
| 账号/企业/成员/部门/角色/审计后台迁移 | 已有9类身份管理/资料流程可复用组件，重新接API与导航；业务页留原项目 | 中，不能只算CRUD接口 |
| Agent HTTP适配与六图/native/BFF/worker回归 | 覆盖同步/异步、辅助模型/MCP、归档与3/5 | 大，需独立change |
| KB remote适配 | 替换注册表授权源，保留文件/记录/cache隔离，local不自动降级 | 中，另仓库负责 |
| 物理拆库/审计/引用与恢复 | 约束、行锁、权限、增量/回退演练 | 大且不适合先做 |

不按人工人日推算纯AI时长，也不承诺单次goal完成全部迁移。以每包首交时间、修复次数、真实契约通过率记录；可并行页面与固定接口客户端/测试，但同一轮不要两个Agent同时改迁移、会话、共享schema。

### 7. entitlement为紧邻包，接口现在冻结

模型和权限求交见http-contract第7节与tenant-resource-entitlements规格。首个独立IAM包预留字段与接口版本，**实际资源开通功能单独I1a**；KB remote上线依赖I1a，不能在功能未实现时忽略entitlement仅看权限码。

权限定义可由业务服务提交版本化清单，IAM通过受控管理员/迁移注册；业务不能在线直接写权限表或自授平台权限。resource_catalog管理授权资源，知识库workspace目录管理技术就绪，二者职责不同。

## Risks / Trade-offs

| 风险 | 对策/放行条件 |
|---|---|
| HTTP新增可用性依赖/开销 | 主库实时读+暖连接、500ms有界失败、无放行缓存；测N次外呼累积影响与故障路径 |
| Redis与PG写入非原子 | PG权威+事务审计/outbox、Redis残留不可授权；崩溃点/重放测试；首期允许安全退出重新登录 |
| 旧BFF路径与业务路由混用 | 旧auth兼容门面明确转IAM，ledger/feedback/observability继续原服务；独立配置IAM_API_URL与业务API地址 |
| 新旧共同写入/影子放行 | 单一写者、影子只比较不授权；差异停切、无OR兜底 |
| Java bcrypt/rt序列化差异 | 固定跨语言向量+真实账号合成fixture，不需要真实口令导出；兼容失败禁止切换 |
| 内部客户端权限过大 | 每服务最小scope/受众，验证引用不可签票/换身份；校准/迁移仍是独立窄能力 |
| 拆库丢失外键/锁/RLS | pg_constraint等清单+本地引用/锁+非owner正负控制+真实恢复；禁止全量drop FK |
| 旧备份重新放行撤销会话/删除数据 | IAM与业务各自重施最新撤销/修订/删除清单，验证后开放；旧快照不是当前授权源 |
| 资源回收只隐藏菜单 | 可授目录、introspection、业务使用时三处求交，事务推进tenant revision；服务令牌同样受限 |
| 配额不足或模型中途退出 | 文件化契约/任务/证据、每阶段可接力；不让Agent隐式扩大本包跨仓库写入范围 |

## Migration Plan

**不等待全部历史归档再设计；先冻结可用基线。** 已完成并独立接受的台账/观测可安排后续同步归档；反馈剪贴板、身份生产MCP等未完成项保留。LangGraph迁移/MCP治理checkbox完成不自动代表本轮重新验收，IAM门禁引用当前真实运行证据并复验受影响部分。不存在“归档五个就自动安全”的条件。

| 阶段 / 实施包 | 交付与前提 | 单一写者 / 回退 |
|---|---|---|
| I0 契约冻结（本轮文档） | 方案、HTTP/身份JSON、表界、官方兼容结论、3/5基线、验收ID映射；用户评审 | Python当前实现仍权威；只改文档，回退文档即可 |
| I1 `establish-shared-iam-service`（新IAM仓库） | JDK21最小验证→独立服务/空隔离库→会话/RBAC/组织/审计/后台→兼容向量/fixture；不连接现有生产库 | 独立测试数据仅Java写；真实用户仍旧服务权威。失败删除/停用自有测试服务，不影响Agent |
| I1a `add-tenant-resource-entitlements`（IAM紧邻包） | 资源目录/开通/有效权限交集、服务账号知识库测试、回收与恢复 | Java自有环境；失败不启KB remote。I1可预留，不能自称已完成I1a |
| I2 `switch-agent-to-remote-iam`（Agent） | 部署兼容门面、Java先接同一身份schema的单写接管、HTTP适配、BFF/SSO与六图/3/5回归、影子差异核对 | 维护窗口停旧身份写者→Java唯一写；业务仍本地PG。保留原UUID/FK暂不物理拆。回退适配器优先继续同一权威；若切回Python写者需停Java、核对兼容schema/所有新写/撤销，不可双开 |
| I3 `connect-knowledge-remote-iam`（KB） | I1a+稳定HTTP，显式旧ID映射、local/remote配置、记录/来源/缓存/脚本权限验收 | 可在I2实现期间独立开发，对独立IAM测试；上线门禁各自满足。回退部署到明确local版本是运维决策且隔离本地身份，不得运行时故障自动local兜底 |
| I4 `separate-iam-business-databases`（协调跨库，独立审查） | 消费者全部HTTP、清点FK/锁/策略、最小引用、快照+增量、冻结旧写、复制一致性、只读比对→切IAM URI→撤旧权限 | Java仍唯一身份写者；旧库只读保存。新写后只能增量保全/前向修复或停流量受控反向恢复，不直接换旧DSN开放 |

I2过渡共用实例/原身份表是为保留现有外键，不代表消费者可继续直读身份；Java迁移DDL先只做兼容增量，不用Flyway重建已由Alembic管理的历史表。若无法证明旧Python能读新schema，回退仅限恢复业务适配版本、保持Java身份权威，或维护窗口前向修复。物理拆库从独立目录/独立服务中解耦出来，降低首交风险。

I1完成独立评审后，I1a、Agent适配与KB客户端可按固定契约并行开发；同一个环境的身份切换/迁移只能由一个负责人执行。Change4可以沿现有稳定身份与MCP绑定边界实施，只依赖可替换授权适配接口，不必等待IAM实际切换；Change6的企业/账号管理归IAM，Agent后台只补用量与运营，不再复制CRUD。7/8算法/评测可用冻结DTO独立研究，上线仍依赖当前身份授权。原10个编号保留，IAM作为新增基础轨道，不混入匿名模型历史分数。

## Open Questions

只剩可安全后定的部署值：域名/精确Origin与回调、受保护密钥供应方式、各独立服务端口、生产IAM Redis容量/HA配置。默认全部回环、无外部回调、无默认口令。项目目录已确认，不再使用拟定datacenter名称。
