# 多企业统一身份与访问隔离设计

## Context

动机与范围见 `proposal.md`，总体关系见 `docs/enterprise-agent-roadmap.md`。代码基线为 `cb09a06`。

已核实：`admin/api` 已有 PG 用户、全局 UserRole/RolePermission、Alembic 0001/0002、账号停用和审计；聊天端仍读 `frontend/data/users.json`，后台与聊天使用相同的旧 HMAC cookie 名称/格式。代理仅检查用户归属，LangGraph compose 使用 noop；归档只有用户/线程/运行作用域。显式图与三个 DeepAgents 图的工具执行路径不同。

`langchain_mcp_adapters` 当前安装版本支持调用拦截器与请求 headers 覆盖，可以实现调用级身份注入；当前项目未使用这套身份能力。外部平台究竟接受哪种证明、哪些主体字段、是否按企业限制工具和数据，不能从工具提示词推出。

现有 `agent-runtime-performance` 主规格要求服务级 discovery 去重；本次增量把它收敛为授权作用域级去重。尚未归档的流式/归档 change 继续提供完整数据与消息展示契约；本次不改写这些历史计划或宣布其已同步。

## Goals / Non-Goals

**Goals:**

- 用一个身份源服务两个前端，允许一个用户加入多家企业；将成员身份与全局账号分开。
- 在服务端、数据库和实际 MCP 调用处验证授权，禁止只靠界面隐藏或模型提示维护边界。
- 在同一进程/连接池中并发处理 A/B 企业而不串数据、缓存或凭据。
- 保留现有消息、完整数据、预算、停止与归档体验；迁移中归属不确定的资源默认不可访问。

**Non-Goals:**

- 本次不做统计事件库、点赞/复制采集、Skill 选择、热门问题、完整评测/告警平台。
- 不做外部平台用户开通、任意 MCP 地址接入、收费、SSO/OIDC 或 MFA；保留身份接口扩展点。
- 不在本次把两个前端合为一个应用，不直接修改 LangGraph 内部表结构，不开发第三个身份服务。
- 不用旧 HMAC cookie 的过渡授权恢复企业模式，也不自动赋予旧用户某企业或超级管理权限。

## Decisions

### 1. 复用后台作为唯一身份域

`admin/api` 管理登录、会话、企业/成员、角色和审计，两个前端通过自己的同源代理使用它。聊天 `auth.ts` 改为调用统一认证，保留其对上层的适配形状；`user-store.ts` 的 JSON 写入路径退出线上，仅迁移工具读取旧文件。

理由：已有 SQLAlchemy、Alembic、权限与审计可复用。继续维护两个相同 cookie 下的身份库容易产生主体不一致；另建身份服务增加部署与迁移面。本期默认由管理员创建/授权关联成员，停止公开注册；企业加入流程不以客户端提供 tenant_id 直接授予身份。

### 2. 数据模型：全局账号 + 租户成员

在现有 Alembic 链上追加迁移，不回写 0001/0002。表名可遵循现有命名，但语义与约束固定：

| 对象 | 主要字段与约束 |
|---|---|
| `users` | 沿用全局 UUID/唯一 ASCII account/password_hash/is_active；新增 auth_revision。公开 profile 不含哈希 |
| `tenants` | UUID、唯一 code、中文 name、active/suspended 状态、授权修订号、时间 |
| `departments` | tenant_id、部门 ID/名称/可选 parent；父子复合外键保证同企业，无环 |
| `tenant_memberships` | membership_id、tenant_id/user_id 唯一、部门、active/disabled、revision；部门关联同企业 |
| `roles` | 企业角色带 tenant_id；平台角色独立 scope；内置模板可读但不可跨域修改；代码唯一性按作用域 |
| 成员角色关联 | tenant_id/membership_id/role_id，复合约束保证企业一致；旧 user_roles 不再参与租户授权 |
| `auth_sessions` | 随机 token 的摘要、session_id、user_id、active_tenant_id、到期/撤销/创建时间；不存原 cookie |
| `mcp_identity_bindings` | tenant_id、可选 membership_id、统一 server_key、external_subject、credential_ref、协议版本、授权版本、状态；tenant 默认绑定与成员覆盖唯一 |
| `thread_ownership` | 可信 thread_id、tenant_id/user_id、来源/迁移版本；与原生 metadata 一致，作为自有归档/旧资源核对索引 |
| 审计 | actor_user/membership、tenant、action、target、请求关联；平台操作有显式 platform scope |
| 迁移台账 | manifest 指纹、实体/原身份映射、状态、冲突码、操作者；不含密码/原文 |

`permissions` 与公开行政区字典可作为全局定义；租户授权只取得其允许授予的子集。关注城市迁移为成员作用域，同一账号在两企业的偏好不串用。平台全局身份和字典的访问不依赖“伪造一个 tenant_id”。

旧 `super_admin` 不自动变成每家企业的管理员。平台 bootstrap 使用显式操作者/迁移清单，禁止生产默认 root/root；内置租户角色没有平台权限，角色编辑不能超出操作者可授予范围。不能停用或移除企业最后一个活跃管理员，除非平台执行明确的企业停用流程。

租户管理员只管理成员的状态/部门/角色。修改全局账号、重置其他人的全局密码、删除全局用户属于平台权限；成员不能因为管理 A 企业就影响同一用户在 B 企业的账号。旧 `/users/{id}` 接口必须收敛范围，不保留旁路。

### 3. 可撤销会话与企业切换

浏览器继续使用 `air_agent_session` 的 HttpOnly Cookie，但切换为高熵不透明 token；服务端只存 token 摘要。默认有效期沿用 30 天，安全传输遵循 HTTPS 配置。管理员改角色、停用成员/企业、改密码、登出都由数据库状态即时影响后续请求，不能只等待 token 到期。

`frontend/middleware.ts` 可用 cookie 存在性做导航提示，授权必须由 Node 代理/admin API/Agent Auth 执行；旧 `verifySession` 不能被保留为充分授权条件。旧 token 在切换后拒绝，要求重新登录；跨语言测试更新为新协议和 legacy 拒绝行为。

登录后返回可加入企业，单企业可选为当前企业，多企业无当前企业时进入选择页，不自动落到列表第一个。企业选择写入服务端 session；请求也携带非授权性的目标企业选择，服务端必须验证它与当前 session 匹配。并发切换使旧页面请求得到明确的 context_changed，不能把旧页面提交悄悄写入新企业。

切换前处理当前运行：明确请求停止并等待确认；停止未确认不继续切换。切换后清空旧线程、归档、个人偏好、后台列表和请求缓存，刷新 `/auth/me`。多标签页收到上下文变更或旧目标企业拒绝时刷新/清空，不继续展示旧企业数据；切换回 A 仍可访问 A 的原资源。

Cookie 驱动的写操作校验可信 Origin/CSRF 证明及允许来源，避免仅凭带 cookie 的跨站请求执行切换或管理。数据库/身份服务故障返回不可用并拒绝业务请求，不回退 JSON 身份或匿名访问。

### 4. 可信上下文与 LangGraph 原生 Auth

统一 API 签发短时、有 audience 与 key id 的服务端运行票据，BFF 用它访问 LangGraph；票据含 session_id、tenant_id、user_id、membership_id、issue/expiry 和上下文修订号，禁止包含业务凭据。签名使用独立运行密钥，不能复用浏览器旧 HMAC secret；支持当前/上一有效 key 的有界轮换。客户端无需获得票据。

LangGraph `Auth` 的 authenticate 验证票据签名、受众、时效后，再检查数据库中的用户/会话/企业/成员状态和修订号。通过 `langgraph_auth_user` 提供已验证上下文；客户端的 `metadata/configurable/input` 不能覆写这些字段。运行阶段再次核验当前授权，角色或绑定被撤销后下一次模型/工具操作停止；不承诺撤回已经被外部平台接受的调用。

运行票据只属于传输认证，不能持久化到 state/可回读 config/Trace。实现必须用非秘密的服务端运行授权记录或只含标识的可信上下文承接长运行，不能把 Bearer 串塞入 checkpoint。持续调用前根据 session/membership 标识复查授权；会话撤销、企业停用后不继续向外部发新调用。

首选原生 Auth `on` 钩子限制资源并强制注入 metadata，覆盖线程 create/search/count/read/update/delete、状态/历史、run create/get/list/stream/join/cancel/resume，及公开 assistant/store/crons/batch 等入口。本期普通用户仅访问本企业本人线程；平台管理员也没有默认的原始对话旁路。Assistant 可读定义按企业允许图清单筛选，写入 assistant、cron、任意 store 命名空间默认拒绝。

任何 thread metadata 的更新保留可信 tenant/user，不允许更改所有者。已结束/正在运行的 run 校验父线程与发起上下文。所有自定义 archive 路由单独验证可信上下文，不能假定内置资源 hook 自动保护自定义应用。

必须在本项目当前自托管镜像验证 authenticate/resource hooks 和自定义 HTTP 路由真实生效，同时同步 `langgraph.json`、Dockerfile、compose 环境与启动探针。现有 `LANGGRAPH_AUTH_TYPE=noop` 必须退出企业配置。若实际版本不支持，记录具体不兼容并保持拒绝状态，不用 Next.js 前端保护冒充后端鉴权；架构变更要回到本 change 更新设计后再实现。

参考：[LangChain 自定义认证](https://docs.langchain.com/langsmith/custom-auth)。官方支持是候选实现依据，实际镜像验证是任务与验收条件。

### 5. 数据库隔离与归档

应用自有租户表启用 RLS，运行连接使用非 owner、非 BYPASSRLS 角色。事务开始后由可信上下文 `SET LOCAL` 设置租户/用户作用域，连接返回池后不能保留作用域；缺失上下文拒绝查询。跨表使用 tenant 复合外键，SQL 查询仍显式包含授权范围。迁移角色与运行角色分开；共享字典和全局用户查询通过受限服务方法/策略实现。

平台管理请求先验证 platform 权限，再走单独的受审计操作路径；不能给所有运行连接禁用 RLS。审计、成员、关注城市、角色分配、MCP 绑定的分页/count/搜索都要隔离，不只检查详情 ID。并发 A/B 事务交错复用同一连接池，测试首尾上下文和异常/取消后的清理。

归档 token 升级为包含协议版本、租户、用户、线程、权限、过期和受众的签名；客户端不能传入可信 token。`runtime_scope`、所有 save/read/update/delete/list/compute/cleanup 路径都使用租户维度，原始/派生资料一致。计算输入只可读取当前作用域的归档；不可通过混合 A/B 引用拼出跨企业计算。

历史无租户记录通过迁移台账和线程可信 metadata 映射。未知归属不可读，不能签发默认企业 scope。到期和线程删除继续执行既有契约；有界后台过期清理使用明确的维护角色，只清理到期数据，不成为任意对话读取入口。LangGraph 内部表不加自定义 RLS，改由原生 Auth 与资源元数据过滤。

### 6. 统一 MCP 平台的身份与授权

先实现 `CredentialProvider`/授权解析接口：输入已验证 RuntimeIdentity 与 server_key，返回绑定版本、已授权 external_subject、内部 credential_ref 和协议适配；实际凭据只在发送边界解析，不进入模型 schema/状态/结果。绑定默认企业级，成员级覆盖必须属于同企业；无绑定、绑定停用、凭据失效或身份服务故障不回退到全局凭据。

本轮已选定调用级拦截器支持的方向。首个具体核验任务读取平台已有协议文档和经授权的 MCP schemas，验证 initialize/initialized/tools/list/call 全链路，确认 header/token/签名机制、企业与成员映射、参数中的主体字段和工具清单范围；结果写入无秘密的 `mcp-auth-contract.md`。不尝试猜测 token/header 或以匿名成功作为企业授权证明。

适配器按已验证契约覆盖主体参数中的 `userId/user_id/tenantId` 等精确字段；字段清单来自显式 schema 映射，不能靠模糊名称替换业务目标用户字段。身份参数不由 LLM 提供。若外部平台只有一个可伪造 userId 参数且没有可信认证，就保持 tenant MCP 禁用，说明生产授权契约缺失；不声称已实现企业隔离。

执行前检查当前企业成员、图权限、具体工具许可与 binding revision。模型披露工具先过滤，但执行层仍要拒绝伪造的越权 tool_call，包括显式图 ToolNode 和 DeepAgents。首期由平台维护 Graph/Tool 许可模板，可为企业/角色缩小范围，不提供用户自选工具入口。

客户端/会话、认证后的工具发现、失败退避与任何结果缓存的键至少含 tenant_id、外部主体/成员授权范围、server_key、binding revision；需要时含 permission revision。重试重新核验身份并保留同一业务调用关联，不允许失败后切换到别的企业。连接池可复用无身份传输，但不能修改全局 headers/env 注入一次请求凭据。

公共 Skill 元数据、无敏感数据的公开工具 schema 可共享，须有明确公共标记；默认授权发现结果不能共享。参数/响应日志与 Trace 默认脱敏，异常路径不能输出 headers/token。为 A/B 企业设计屏障并发、初始化、失败重试、绑定轮换及移除授权的测试。

### 7. 接口与前端最小交付

沿用两个前端同源 `/api` 代理，下面是统一 API 的契约方向，实施时固定字段与错误码并同步类型：

| 接口 | 行为 |
|---|---|
| `POST /auth/login` | 统一账号认证，创建可撤销 session，返回本人和可选择企业 |
| `GET /auth/me` | 查询实时 user/session/current_tenant/membership/部门/当前权限/上下文版本 |
| `GET /auth/tenants` | 本人活跃企业列表，无其他企业详情 |
| `POST /auth/switch-tenant` | 校验 membership 与前一上下文版本，切换 session 企业并审计 |
| `POST /auth/logout` | 撤销当前 session，清 cookie；幂等 |
| `/tenants` 与成员/部门子资源 | 平台创建/停用企业；企业管理仅访问本企业资源 |
| `/roles`、`/users` 既有接口 | 按平台/租户职责收敛；作用域明确，禁止旧全局授权旁路 |
| MCP 绑定管理 | 仅授权平台角色设置绑定/credential_ref；返回脱敏状态和协议版本 |
| 内部运行票据接口 | 仅服务器调用，基于有效 session 和目标企业签发；浏览器不可取得票据 |

401 表示未认证/会话撤销；403 表示自身已知范围内的权限拒绝；对外部企业对象使用不区分不存在/越权的 404；409 表示上下文版本冲突或管理约束；503 表示依赖不可用。详情不得带哈希、凭据或其他企业存在性信息。

`admin/web` 增加企业/部门/成员管理及当前企业切换；`frontend` 增加当前企业与授权身份状态。复用 DESIGN/UX-CONTRACT 与现有组件，不大改聊天布局。租户管理员看到成员级管理，平台管理入口需独立权限；旧全局用户按钮不能继续对租户用户开放。中文空态、选择企业、停用、会话过期、切换冲突、保存失败和键盘操作都必须可验证。

首次上线采用管理员创建/授权关联成员，账户关联不可仅凭同名或知道用户 UUID。平台操作者可通过明确受审计授权绑定已有全局账号；企业管理员无法枚举全局用户或自行把其他企业账号拉入本企业。

## Risks / Trade-offs

- 身份 API 短时不可用 → 失败关闭并保留可读提示；身份与业务服务仍分进程，不用旧 JSON 兜底。
- 全局用户多企业成员使旧用户操作语义变化 → 成员管理替代全局用户编辑，平台操作明确区分，覆盖同一用户加入 A/B 的测试。
- RLS 与全局字典/平台操作混用 → 独立访问路径、非 owner 运行角色、复合约束与连接池并发测试。
- Agent 执行时间长、权限动态变化 → 入站和下一次外部调用复查；切换先停止，后台撤销不承诺取消已接受的远程操作。
- 当前六图执行技术不同 → 授权适配共用，六图逐个验证；不把前三图通过等同全部通过。
- 外部平台真实认证材料缺失 → 先完成国内身份/隔离及受控 MCP fixture 验证，相关真实任务保留未完成；企业 MCP 保持安全禁用。
- 现有完整单元集有 10 项基线失败 → 本次相关用例必须通过，全量结果逐项对照；共享 MCP 代码变更后旧 MCP 用例应迁移到授权注册表真实契约，不能简单删除。

## Migration Plan

1. 只读预检两套用户、PG RBAC、线程 metadata 与归档归属；报告只含必要 ID、计数、冲突类别，不导出密码哈希或聊天原文。
2. 在隔离数据库演练；备份应用表和 LangGraph 平台数据。明确仅本地专用测试数据库可 reset，禁止重建现有 pgdata。
3. 显式 manifest 指定旧 JSON UUID、PG UUID、canonical user、tenant、成员/角色与线程归属。UUID 可安全保留时保留；同名不同 UUID 不自动合并。导入 bcrypt 哈希需在服务端验证兼容，报告不输出哈希；未能映射的对象隔离。
4. Alembic 增加身份和租户表/列、复合约束及策略；迁移角色执行可复跑回填，分批有 checkpoint/计数。线程仅通过受限内部 API 更新授权 metadata，不改历史消息或平台内部表。
5. 旧 UserRole/全局关注城市按 manifest 转为成员角色/偏好；旧审计缺少可信企业时保留在限制访问的历史域，不开放到任意企业。
6. 校验迁移前后用户/线程/归档计数、密码登录、权限与 manifest 指纹；未解决冲突使 apply 停止或仅执行已明确对象，不把全部任务勾完。
7. 停止新写与活动运行，切换统一认证/原生 Auth/新的 archive scope，撤销旧 cookie，按真实登录流程验收 A/B/共享成员。
8. 新代码上线后旧 JSON 文件仍保持本地受保护备份，无线上写入；待验收后再按保留策略处理，不直接删除账号数据。
9. 回滚演练：无新写时可恢复备份及旧部署，但企业公开入口保持关闭；已有新写时先维护模式并导出增量/执行前向修复，禁止 downgrade 丢弃新数据或恢复 noop 后暴露服务。

## 验收矩阵与外部材料

| 层级 | 必须验证 |
|---|---|
| 身份 | 同一账号 A/B 成员权限不同；登出、密码修改、成员/企业停用和角色撤销后下一请求失效 |
| 后台 | 列表/count/详情/修改/授权/审计仅本企业；租户管理员不能改全局用户或授予平台权限 |
| API | 无票据、伪造 config/metadata、直连 2024、旧 cookie 拒绝；六图合法运行与跨租户拒绝 |
| 平台资源 | search/count/state/history/runs/join/cancel/resume/删除/辅助 store 与 cron 无旁路 |
| 归档 | A/B 与同用户不同企业的回读、分页、计算、到期、删除互不影响 |
| 数据库 | 非 owner RLS、复合外键、连接池交错、异常/取消回收后不串租户 |
| MCP | initialize/tools/list/call、A/B 认证差异、注入主体、并发、重试、撤销与轮换；错误/Trace 无秘密 |
| 浏览器 | 两个前端登录/企业选择/切换/刷新/旧标签页/停止确认/成员管理；保留原聊天体验 |
| 迁移 | dry-run、显式冲突、幂等复跑、计数一致、未归属隔离、恢复演练 |

需要实际平台协议和至少 A/B 两套授权测试身份及可区分的授权数据。实施者优先读取本机既有合法配置与项目协议资料，不输出凭据；材料缺失时只报告缺失项和受影响任务，不能虚构协议。这是完成验收所需的外部输入，不是对模型的日常实现选择提问。

证据写入 `docs/reviews/tenant-identity-access-acceptance.md` 与脱敏附件，逐项区分模拟/隔离 PG/真实平台/真实浏览器，状态为“待 Codex 独立验收”。不得以 `/goal` 循环完成代替验收。
