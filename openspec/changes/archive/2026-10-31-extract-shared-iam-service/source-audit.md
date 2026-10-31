# 现状核实与抽取边界

核实日期：2026-10-14。Agent根目录`/Users/acerola/Dev/Python/air_agent`，HEAD `0f2cd027941cdb6fa773ef8aba41940e692b7e52`；含本次3/5接管未提交改动，不能只按HEAD重建基线。知识库路径由用户确认；只读检查，没有写入该仓库。部署域名未定。

## 1. 核实结果与线索修正

| 入口 | 已读代码事实 | 抽取影响 |
|---|---|---|
| `admin/api/app/models.py` | 全局User与TenantMembership分离；角色scope=platform/tenant；Permission既有grantable_to_tenants；AuthSession存摘要、活动企业和ctx_version | 不另造以用户名或企业编码为主键的用户体系；保留原UUID |
| `app/services/identity.py` | 每请求从cookie解析会话并查当前用户/企业/成员；平台与企业权限现算，平台解析临时GUC随后复位 | 不能把旧权限复制进长期token后离线接受 |
| `app/routers/auth.py` | host-only `air_agent_session`、HttpOnly/Lax、Secure可配置；登录30天绝对期限；登出幂等204；改密撤销全部会话；切企业CAS；签票需要内部凭据+本人会话 | 当前没有完整独立refresh/SSO系统；拟定续期行为是新增能力 |
| `app/security.py` / `src/common/identity/ticket.py` | Python bcrypt rounds10；60秒rt-v1签票，JSON信封`{kid,p}`的base64url文本做HMAC-SHA256；另有mt-v1及独立密钥/受众的rs-v1 | Java兼容需双向固定向量，不能改称JWT或误签其解码后JSON |
| `app/services/tenant_context.py` | me/login/switch响应附用户关注城市；用户列表/详情也联取关注城市 | 当前响应混入业务偏好；新IAM DTO剥离，兼容门面在业务侧补齐 |
| `app/services/followed_regions.py` | 关注城市写入先锁User或TenantMembership行，最多5个 | 不只是删外键：拆库后锁需改成本地业务聚合根或作用域advisory lock |
| `app/routers/{users,tenants,roles,departments,permissions,bindings}.py` | 账号/组织/角色授权、跨企业检查、最后管理员保护与审计均已存在；角色变更有受影响成员修订推进 | 迁移现有业务规则，而非只实现Sa-Token login/checkLogin |
| `scripts/seed.py` / `services/tenant_seed.py` | 平台/企业权限分型，企业模板不含原文读取、绑定管理或平台账号管理；价格管理另码 | IAM持有权限目录/授予规则；业务服务注册自己命名空间，不各自写权限表 |
| `src/common/identity/db.py` | 同一文件含身份联表、MCP绑定和线程ownership SQL；异步池与同步连接均有路径 | 只替换身份查询会不够；也不能整个删除文件而损坏业务归属 |
| `identity/{langgraph_auth,runtime,outbound}.py` | 原生Auth检查票据并读PG；外呼前核验四修订；default-deny；认证身份只取平台注入字段，凭据不进config/state | 入站、同步/异步外呼、重试、子Agent/摘要都需HTTP适配；现有归属/台账门禁继续业务本地执行 |
| `frontend/lib/admin-api.ts` | 聊天BFF向admin转发cookie和Set-Cookie、内部签票；无本地账号回退；me no-store | 按身份/业务API拆客户端，不把ADMIN_API_URL整项改成IAM后让台账请求发错服务 |
| `admin/web/lib/api.ts`、`session.tsx` | 同源/api反代、cookie凭证、上下文声明、409刷新 | 独立IAM后台可复用现有React壳；企业切换和错误态须复验 |
| `docker-compose.yaml` | Redis7已经服务LangGraph migration lock/broker；现有Redis段无命名数据卷或显式AOF配置 | 证明有Redis依赖，不证明它是可直接复用的生产IAM会话存储 |

运行时身份引用静态清单共17个文件：`artifact_http.py`、`business_graph/{nodes,tool_wrappers}.py`、`data_governance.py`、`event_ledger/{lifecycle,service_read}.py`、`identity/{db,langgraph_auth,outbound,runtime}.py`、`mcp_authorization.py`、`mcp_scoped.py`、`middleware/tenant_runtime_guard_middleware.py`、`models.py`、`observability/context.py`、`skill_router.py`、`text_reduction.py`（均相对于src/common）。这只是显式引用清单，实施还要沿调用点审计，不能据此承诺只改17文件。

## 2. 表与逻辑归属决定

| 表/对象 | 目标所有者 | 处理 |
|---|---|---|
| users、roles、permissions、user_roles、role_permissions | IAM | 保留UUID/码、哈希、平台/企业scope及授予规则；停用/撤销与修订同事务 |
| tenants、departments、tenant_memberships、membership_roles | IAM | 部门父子/成员角色同租户复合约束、无环触发器与最后管理员规则移入IAM迁移/服务 |
| auth_sessions | IAM | 迁入会话权威；Sa-Token token映射不能代替PG实时修订。旧摘要会话允许兼容适配，不复活旧HMAC |
| 新service_accounts、service_tokens、IAM内部客户端 | IAM | 与人类会话分型，按企业/受众/权限/到期/撤销约束；仅预留及后续实施 |
| 新resource_catalog、tenant_entitlements、权限资源关联 | IAM | 通用资源开通；知识库是首例，目录不保存实际库地址或访问密钥 |
| regions、user_followed_regions、membership_followed_regions | Agent业务侧 | 用户资料页面需要时调用业务API；偏好锁留本地，不让IAM依赖空气质量业务 |
| thread_ownership、tool_artifact_storage、artifact_computations、原生LangGraph表 | Agent业务侧 | 本人/租户归属和删除保持原契约；原生平台表不被身份迁移改结构 |
| agent_requests/submissions/run_attempts/run_resumes/answers/trace_links/events/outbox/consumer_receipts/content_refs/thread_deletions/projection_state | Agent业务侧 | 整个台账留本地；身份标识为历史归属/快照，不联远端表求历史事实 |
| agent_feedback_current/operations/comments及浏览器行为投影 | Agent业务侧 | 保留父回答复合约束、幂等、CAS、删除标记、原文双权限 |
| 模型/工具/Skill观测、调用尝试、模型价格与费用修订、观测Trace映射 | Agent业务侧 | 保留tenant范围、Decimal费用/版本、collector与清理worker；IAM不保存usage/对话 |
| mcp_identity_bindings | **Agent业务侧** | 当前一行混合外部主体、server_key、credential_ref、协议版本与绑定修订，属于连接/外部业务授权配置；IAM只提供用户/成员。将来有跨产品外部账号关联再提出通用federated subject能力，不能现阶段把全部凭据引用塞进IAM |
| audit_logs | 按事件域拆 | 新身份操作在IAM同事务审计；新业务操作留Agent。旧混合表保留只读、标记source与原作用域；分类清单可幂等迁移明确的身份行，未知/无tenant历史不公开；无必要不物理搬旧表 |
| migration_ledger | 按迁移任务拆 | 当前混含身份/线程实体，保留原表原指纹；IAM新迁移作业保留独立账本与来源关联，不将整个旧迁移表视作线上身份数据 |

特别纠正：**台账、反馈、观测并非都直接外键到身份表**。已读0007、0011_feedback、0012_observability及完整性后续迁移：它们主要以UUID身份列和业务父子复合FK关联；不能批量删除所有外键。真实跨身份FK主要在旧models：关注城市、thread_ownership、MCP绑定、混合审计等。物理拆库前必须从目标库`pg_constraint`导出实际图，核对模型/DDL漂移后逐条迁移。

## 3. 迁移链、RLS与事务

- 0003建身份域、RLS、运行角色授权；0004加归档租户；0005修复pending归属/列授权/归档与计算策略；0006加claim_session_id。模型文档“部门无环仅服务层”过时，0003实际有`departments_reject_cycle()`触发器，DDL才是权威。
- 0007–0010是业务台账、启动权、服务校准与submission/run映射，不属于IAM迁移。
- 0011_feedback与0012_observability都从0010分出，经0013_merge合并，再经0011f_integrity、0012o_integrity、0012o_pricing、**0012o_delivery（实际head）**。不能按文件数字猜最后是0013，也不能把所有0003之后迁移搬到Java。
- `app/db.py`事务本地GUC有user/tenant/session/platform/login；台账还使用`agent_operations`、worker等窄标志。当前新域11表已实测FORCE RLS，不能宣称所有旧身份表都已经FORCE。新IAM必须继续使用非owner/nonBYPASS请求角色，并逐表明确同等或更强策略。
- 当前每请求身份与业务共用AsyncSession；拆服务后失去共同事务/行锁/快照。计划只承诺每次受保护操作前的权威核验，已发出的外部调用无法撤回；要求更强撤销线性化时需独立授权租约协议，不能假称HTTP自动提供分布式事务。

## 4. 知识库只读核查

仓库：`/Users/acerola/Dev/Python/RAG/env-rag-final`。已读AGENTS.md、WORKSPACE-ACCESS.md相关约束、`access/auth.py`、`registry.py`、`gateway.py::check_context/workspace_for`；`audit/identity.py`是文档内容身份哈希，**不是用户IAM**。

- 8099是当前鉴权查询入口；8091是旧对照工具。当前注册表保存Principal、enterprise、角色、token摘要、revision及库授权；`AuthContext.cache_key`带用户/企业/策略修订/库集合。
- `resolve_selection`对未知或无权库整体拒绝，all仅展开有权库，不允许default回退。`check_context`在文件/记录/检索等边界复查注册表修订及授权；remote适配必须保留这些调用点，替换权威提供方。
- Workspace目录还包含PG/向量/图命名空间、parsed_root、实例URL与密钥引用、embedding/语料身份。这些是知识库技术目录，**留知识库**。IAM资源中文名/顺序与library_code不能变成任意存储位置输入。
- remote不把本地文件中的enterprise文本直接当IAM tenant UUID；迁移清单显式映射旧用户/企业/库到IAM UUID及library_code，名字相同不自动合并。
- 所有现有服务只绑定127.0.0.1，查询不初始化LightRAG存储，正式语料只读。IAM评估不修改这些承诺，不触及原语料/PG/Qdrant/Neo4j。

## 5. 当前验收与在飞状态

3/5最终接管报告：`docs/reviews/agent-interactions-observability-takeover-acceptance.md`；源码SHA256清单：`docs/reviews/evidence/takeover-03-05/source-baseline.json`。作者工作区34/42文件指纹保持，第三模型03/04的58/55分与131/83分钟独立记录，不归入协调者成绩。

2026-10-14审计时CLI：身份44/46、台账38/38、反馈31/32、观测39/39、LangGraph迁移57/57、MCP治理37/37，均未归档。创建IAM规划后当前12个在飞目录；Change4仅脚手架，暂停派发。checkbox完成不是独立验收或归档证明。

实测管理端183 passed/3 skipped，根362 passed/75 skipped/5既有失败，默认集成7 passed/31 skipped/1既有失败，前端41通过及生产构建通过。六图24条真实主模型运行/台账终态均成功；真实Langfuse、恢复、受控MCP单独证据见接管报告。旧“98项admin”不是当前基线，未来采用稳定验收ID映射，保留基线失败集合。

推荐先做已验收事项的规格收敛与基线冻结；**不必等全部历史change形式归档才评审IAM**。未闭合剪贴板/真实MCP门禁不能为了抽取而勾选。IAM实施的Gate0必须列清兼容来源，消费方切换前重跑受影响场景；旧change不被本规划自动归档。


2026-10-15更新：用户豁免反馈最后实机项并接受3/5归档，已推送组合基线2d6f7c8；Change4恢复展开，沿当前身份接口与IAM并行，原历史计数保留。验收容器清理及新状态见Agent收尾报告。
