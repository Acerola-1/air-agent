# IAM HTTP 契约草案 v1

状态：2026-10-14待用户评审，尚未实现。目标项目`/Users/acerola/Dev/Java/air_agent_iam`。本文件是评审快照；批准后由IAM项目维护版本化契约并给消费者发布固定版本/摘要，禁止两边各自修改同一v1含义。

## 1. 传输、主体与错误

- 基础前缀`/v1`；服务默认只监听`127.0.0.1`。跨机器部署经另行配置的私网/TLS或可信反代，不因初始化默认绑定0.0.0.0。
- 业务入站凭据为`Authorization: Bearer <opaque-token>`。禁止query token。浏览器推荐同源BFF，cookie由BFF管理，JS不保存长期Bearer。BFF不独立决定用户权限。
- `/v1/internal/*`：`Authorization: Bearer <client-credential>`认证**调用服务**，正文的`subject_token`是待核验的**用户/执行/服务账号令牌**。两个主体不可混为一个。每个客户端有独立受众、scope、密钥轮换与撤销，禁止浏览器调用。
- 错误：`{"error":{"code":"context_changed","message":"企业上下文已变更，请刷新","request_id":"..."}}`。400格式；401无效/过期/撤销凭据；403账号停用、有效身份无权限/企业或成员停用/未选择企业；404不可见对象与不存在一致；409上下文/并发冲突；429限流；503依赖不可确认（含上游429无法完成核验时的业务侧映射）。业务服务故障不伪装为用户退出。
- 所有认证响应`Cache-Control: no-store`；内部请求/响应不记录原始凭据。UUID用字符串，时间RFC3339 UTC；修订号为非负整数（crev至少1），拒绝bool/字符串伪整数；列表有界分页（默认20、最大200，权限大集合使用明确总大小限制，不静默截断有效权限）。
- `schema_version: "iam.identity.v1"`。可新增可选字段；已有必需字段缺失、未知主体类型、不匹配受众、错类型均失败关闭。破坏性变化发布v2，不同issuer/audience的token不能互通。

## 2. 人类身份 JSON（me与有效introspection共用）

下例全部为合成标识，knowledge资源有效权限仅在后续I1a资源开通完成后成立；I1单独交付时资源型有效权限为空。四项位于revisions；context_version是session_ctx_version的兼容别名，两者必须相等；不是第五修订号。

```json
{
  "schema_version": "iam.identity.v1",
  "active": true,
  "issuer": "air-agent-iam",
  "audience": "air-agent",
  "principal": {"type":"human","id":"11111111-1111-4111-8111-111111111111","realm":"workforce"},
  "user": {"id":"11111111-1111-4111-8111-111111111111","account":"member_a","display_name":"演示成员","status":"active"},
  "session": {
    "id":"22222222-2222-4222-8222-222222222222",
    "ctx_version":7,
    "created_at":"2026-10-14T00:00:00Z",
    "expires_at":"2026-10-21T01:00:00Z",
    "absolute_expires_at":"2026-11-13T00:00:00Z",
    "idle_expires_at":"2026-10-21T01:00:00Z"
  },
  "current_tenant": {"id":"33333333-3333-4333-8333-333333333333","code":"tenant-a","name":"演示企业","status":"active"},
  "membership": {"id":"44444444-4444-4444-8444-444444444444","tenant_id":"33333333-3333-4333-8333-333333333333","department_id":null,"status":"active","revision":3},
  "platform_roles":[],
  "tenant_roles":[{"code":"tenant_reader","name":"企业查询成员"}],
  "permissions":["agent:metrics:read","knowledge:lib:air:read"],
  "revisions":{"user_auth_revision":0,"tenant_auth_revision":5,"membership_revision":3,"session_ctx_version":7},
  "context_version":7,
  "platform_admin":false,
  "context_usable":true,
  "needs_tenant_selection":false,
  "evaluated_at":"2026-10-14T01:00:00Z"
}
```

- `active=true`只表示账号/凭据有效；企业或成员停用时me仍可显示其状态，`context_usable=false`，企业有效权限清空、企业业务403。未选企业时current_tenant/membership及其revision为null，tenant_roles为空；平台自身管理权限仍按实际平台授权返回。平台角色不是业务跨企业通行证。
- permissions为**当前受众可用**的实时有效集合，平台/企业来源合并需明确作用域；不得仅靠角色名或平台super_admin通配出所有知识库。业务API还校验对象归属。菜单仅UX，不替代权限。
- 原SessionContext里的`user.followed_region_codes`不进入新IAM身份；Agent兼容门面可另取本地偏好后组旧DTO。无密码哈希、外部身份密钥、cookie值或原始运行票据。
- `realm=workforce`首期唯一人类账号域；预留customer/partner等，实际增加须独立change。未来相同login_id必须连realm判别，不能把平台角色和realm混成一项。

## 3. 外部与BFF接口

| 接口 | 输入/结果 | 关键边界 |
|---|---|---|
| POST `/v1/auth/login` | `{account,password,realm:"workforce",audience}`；200 `{access_token,token_type:"Bearer",expires_at,identity}` | 只有注册受众；口令错误统一401/限流；服务端建会话并审计；返回令牌只用于受控认证响应，BFF吞下并存服务端，不透传给页面 |
| POST `/v1/auth/logout` | 本人Bearer；204 | 当前共享会话撤销，重复/已失效幂等；各BFF清自己cookie，下一次实时核验拒绝其他前端旧cookie |
| POST `/v1/auth/logout-all` | 有效本人Bearer；204 | 本人的全部会话撤销，需敏感操作确认策略；不允许任意传user_id注销他人 |
| GET `/v1/auth/me` | 本人Bearer；200 上述身份 | 不接受客户端声明tenant作为授权，身份状态来自权威 |
| GET `/v1/auth/tenants` | 本人Bearer；200 `{items:[{tenant,membership}],next_cursor}` | 仅本人有效企业×成员；同一用户不泄露其他企业内部成员 |
| POST `/v1/auth/switch-tenant` | `{tenant_id,expected_ctx_version}`；200 identity | 有效成员/企业；锁会话/原子CAS，旧版本409；未授权目标404；服务账号403 |
| POST `/v1/auth/change-password` | `{old_password,new_password}`；204 | 原口令校验，改哈希+user.auth_revision推进+撤销所有本人会话+审计同事务，所有BFF退出 |
| POST `/v1/auth/renew` | 有效本人Bearer与`{expected_ctx_version}`；200 `{expires_at,absolute_expires_at,idle_expires_at,identity}` | 不签发可离线复活的refresh JWT，不接受过期会话；核验后延长空闲期限，不能越过绝对上限；不因续期重置修订或活动企业 |
| GET `/v1/auth/menus?application=iam-admin` | 本人Bearer；200 `{items:[{code,name,path,icon,sort,children}]}` | 按当前有效权限与应用过滤；path只能是注册站点内路径或显式白名单链接 |

新会话建议绝对30天、空闲7天，显式renew将空闲上界推进至`min(now+7天,absolute_expires_at)`；普通me/introspect不偷偷延长期限，后台轮询不能永续登录。已有30天绝对到期会话迁移时保留原expires_at，idle可为null，不延长已过期会话。最终空闲策略可在用户评审时调整，不能被实施Agent随意更改。初版renew不轮换令牌，后续轮换需另定并发与在飞引用策略。

普通登录API不是OAuth/OIDC完整实现声明；第三方授权码/PKCE接入如有需要另定正式协议，不把自定义login命名为标准OAuth token endpoint。

## 4. 内部introspection与长运行

### 4.1 普通token模式

POST `/v1/internal/identity/introspect`

```json
{"subject_token":"<仅传输中存在的令牌>","audience":"langgraph-runtime","expected_context":{"tenant_id":"33333333-3333-4333-8333-333333333333","ctx_version":7},"include_execution_context":true}
```

- 调用客户端必须有`identity:introspect`及对应受众；include_execution_context仅runtime客户端可用。核验token真实性、类型/受众、会话、账号、成员/企业状态和当前权限，读取权威主库，不读延迟副本。
- 合法token返回200 identity；若创建执行复查上下文，额外返回`execution_context: {verification_ref, audience, session_id, revisions, valid_until}`。`verification_ref`是服务器登记、绑定客户端/主体/原会话/受众/修订的非授权引用；valid_until不超过会话有效上界。
- 无效token内部返回200 `{schema_version:"iam.introspection.v1",active:false,reason:"invalid_or_revoked"}`，不带身份详情；已知账号停用可返回`account_disabled`供客户端映射403。非法服务客户端本身401/403。消费方把inactive映射401/403，不能将内部HTTP200当业务成功。
- 人类会话令牌受众为air-agent/knowledge/iam-admin的注册访问范围；BFF换出的rt-v1严格限定langgraph-runtime。后者不能拿来访问知识库或用户管理。

### 4.2 已核验上下文模式

同一内部端点，Authorization仍为runtime客户端凭据；请求与subject_token互斥：

```json
{"execution_context":{"verification_ref":"55555555-5555-4555-8555-555555555555","expected_revisions":{"user_auth_revision":0,"tenant_auth_revision":5,"membership_revision":3,"session_ctx_version":7}},"audience":"langgraph-runtime"}
```

- 首次引用只能由合法token核验产生；登记在IAM，不能由客户端自造session/user/tenant换取引用。引用本身不具有Bearer能力，不可用于创建新运行、签票或一般/me；必须匹配内部客户端、已核验主体、受众及原修订。
- 返回200 `{active:true,context_matches:true,evaluated_at,revisions}`或`{active:false,context_matches:false,reason}`；不重新授予更大权限。每次查询实时读取原会话、当前账号/企业/成员及撤销/到期与修订，短入口票据60秒到期不影响原会话仍有效的运行。
- 仅引用标识及原修订可进入可信执行上下文，原subject_token、client credential、密钥不进入config/state/checkpoint/Trace。引用不是新的离线授权票据。异常/取消后资源有界清理，过期回收；TTL本身不能延长身份。
- 每次模型/MCP发送、重试、子Agent、辅助摘要前复查；不在run起点一次检查后整轮复用。恢复使用新入站票据再产生引用；原引用若修订变更拒绝。
- 凭据核验、准入和外部调用之间仍有已存在的检查/执行窗口；“即时”指撤销事务提交后开始的核验绝不读旧许可，不能撤回此前已经发出的网络调用。不得宣称分布式全局原子取消。

## 5. 运行签票与内部客户端迁移

POST `/v1/internal/runtime-tickets`，客户端scope `runtime-ticket:issue`及audience白名单：

```json
{"subject_token":"<当前用户会话令牌>","audience":"langgraph-runtime","target_tenant_id":"33333333-3333-4333-8333-333333333333","expected_ctx_version":7}
```

200 `{ticket,token_type:"Bearer",expires_at,audience,format:"rt-v1"}`。签发前实时身份；target/ctx不一致409，未选企业或停用403，密钥/依赖异常503。TTL保留60秒，并受会话有效期限制；含sid/uid/tid/mid/aud/iat/exp/crev/urev/trev/mrev及外层kid。

兼容：旧`/auth/runtime-ticket`继续由旧BFF传cookie+X-Internal-Token，门面提取会话后转IAM；不得由旧门面重新查旧库决定授权。新客户端采用分服务凭据。迁移期按显式issuer/format路由**一个权威核验路径**，不尝试“新失败再旧通过”；维护旧票据最长存活期与kid退役时间表。Java可保留rt-v1签名字节格式，但Python实时身份消费者仍要适配HTTP，不能称完全无改动。

`X-Internal-Token`只在兼容路由保留且按来源/速率限制；新IAM客户端用独立可撤销service-client令牌。Agent自己的台账确认、collector健康、归属认领等内部端点仍属于Agent，不随IAM迁移全部改由IAM持有。rs-v1只读校准与mt-v1单线程迁移保持独立scope/audience/密钥，不并入普通签票接口；使用者仍按业务权限处理。

## 6. 管理API与服务账号

- `/v1/admin/users`：分页建档/详情/更新/停用，`/{id}/password`重置，`/{id}/platform-roles`分配；只平台权限，可授范围与最后有效管理员保护保持，账号冲突409。
- `/v1/admin/tenants`：企业建档/状态；`/{id}/members`关联、停用、部门/角色变更；`/departments`与`/roles`按租户/平台scope区分；`/permissions`及`/permissions/grantable-tree`；`/audit-logs`分域有界。写接口需相同tenant/ctx声明、乐观revision与审计。
- `/v1/admin/service-accounts`：平台创建、企业限定、受众与permission allowlist；`/{id}/tokens`创建一次性返回秘密；`/tokens/{token_id}/revoke`立即撤销、轮换可设置不超过24小时的明确双token重叠。初始建议90天到期，硬上限365天，不能永久`-1`；不自动续期。
- 服务账号JSON：`principal.type="service_account"`、id/realm=`service`、`user=null`、`membership=null`、`session=null`、固定current_tenant、permissions按主体授权∩资源开通∩受众求交；附`service_account_revision`、`credential_id`、`credential_revision`、`expires_at`，人类四修订中不适用者为null。服务消费者必须按类型验证自己的必需字段；**不伪造membership来绕过Agent目前只接受人类身份的入口**。
- 首个业务服务令牌只授knowledge读权限与knowledge受众；无switch-tenant、改密、Agent运行或平台管理。internal service-client也是机器主体，但使用独立realm=`internal_client`和internal scopes，不与知识库脚本通用。
- 服务账号、凭据与企业状态从权威库每次读，停用企业同样拒绝；密码变化不影响独立服务账号，撤销个人会话也不能变成服务账号的续命机制。令牌数据库只存摘要，不将原始secret带入审计或证据。

## 7. 通用资源契约（紧邻独立实施包）

- resource_catalog：`id,code,name_zh,sort,status(active/disabled),kind,parent_code,revision`，code全局唯一且不复用。首个叶资源`knowledge:lib:air`，父级可为knowledge；库中文名与顺序在此，真实workspace地址不在此。
- tenant_entitlements：`tenant_id,resource_code,status(active/revoked),revision,granted_at,revoked_at,updated_by`；唯一tenant×resource_code。未开通按拒绝。父目录不是自动授所有子库的通配许可。
- Permission新增`resource_code`显式关联；`knowledge:lib:air:read`必须关联`knowledge:lib:air`且grantable_to_tenants=true；不依赖运行时字符串拆分猜资源。普通无资源IAM权限resource_code=null，继续按原可授边界。
- 平台`GET /v1/admin/resources`与`PUT /v1/admin/tenants/{tenant_id}/entitlements/{resource_code}`，写入需`entitlement:manage`平台专属权限与expected_revision。企业只读`GET /v1/tenant/resources`和自己的可授权限目录，不能自开通。
- 有效权限=当前角色/主体已授码∩启用权限∩当前有效资源/entitlement∩受众；企业授予还需操作者可授集合。菜单祖先补齐不能引入按钮授权或其他资源。全局资源停用须推进受影响企业修订或独立资源策略修订并纳入实时核验；初版选择同事务推进相关tenant.auth_revision。
- 回收保留role_permissions、service-account grants及企业偏好，标记当前失效；不再允许新授/扩张，仅允许清除旧配置。重新开通默认恢复仍保留的授权，平台界面先列出影响数量并明确确认，审计记录此语义。
- 知识库远程适配按本地静态library_code→workspace目录映射有效read码，all展开有权且就绪集合；任何显式无权或未知库整体拒绝，record/file/cache各入口都复查，不把knowledge权限当Agent工具权限。

## 8. 浏览器SSO

- 推荐中央IAM登录站点 + 各应用同源BFF。中央`__Host-air_iam_session`与各应用`__Host-air_agent_bff`/`__Host-air_knowledge_bff` cookie：HttpOnly、Secure、Path=/、SameSite=Lax、**不设置Domain**。本机HTTP用不同的显式dev cookie名，不假装支持__Host-限制。当前`air_agent_session`为兼容名，只在过渡阶段接受，升级后清理。
- 跨站SSO采用注册客户端、严格callback白名单、state与一次性短ticket（30秒、一次消费、绑定client及callback），服务端HTTP兑换；浏览器URL只经过一次性ticket，不出现用户Bearer。可用Sa-Token SSO模式三/NoSDK封装，HTTP数据契约与本项目会话/撤销规则仍需自定义验证；不声称Python直接套Java SDK。
- SSO建立应用BFF到中央会话的映射，多个前端共享中央会话的ctx_version，A→B→A同样使旧页冲突。注销通知用于及时清UI，授权仍靠实时introspection。
- 首个IAM包提供中央登录与同源BFF；跨前端SSO兑换在消费者切换前验收。域名未定不影响协议，但上线前必须填写精确站点/回调/Origin，默认空白即禁止外部跳转。
- 推荐不启跨站cookie/CORS。若部署必须浏览器跨origin直调，CORS精确origin+credentials=true+明确方法/headers、写操作CSRF；只有确需cross-site才用SameSite=None;Secure并接受第三方cookie限制。不得配置`*`+credentials或凭据共享父域作为通用方案。


SSO封装接口草案：`GET /v1/sso/authorize?client_id&redirect_uri&state`由中央host-only会话认证，登录后向精确注册回调返回一次性ticket与原state；`POST /v1/internal/sso/exchange`用client Bearer认证并提交`{ticket,redirect_uri}`，原子一次消费、校验ticket绑定的client/callback与中央会话，返回仅BFF可见的应用令牌及identity。state由BFF生成、服务端绑定浏览器并在回调恒时比对；错误state不得发起兑换。ticket过期/重放拒绝且不签发新会话；原始用户Bearer不经浏览器URL。底层Sa-Token路由由门面封装，不把Java插件内部协议暴露为Python业务依赖。
