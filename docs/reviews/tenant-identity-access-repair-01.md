# 轮次01修复：租户身份 change 独立验收问题修复报告

日期：2026-10-12。实施标识：**本轮实现**（第二轮，修复轮；不提供也不推测模型身份）。
交付状态：**待独立复验**。保留全部 diff，未提交、未推送、未合并、未归档、未同步主规格。
本文件不改动首次评分、首轮报告与原始证据（`flash-blind-01-*`、`evidence/flash-01/**` 保持原样）；
首次 4 小时记录保持不变，**修复用时单列**（见文末）。

范围：仅 `add-tenant-identity-access`。未开始 `add-agent-event-ledger`；`docs/enterprise-agent-roadmap.md`
等第二项相关 Codex 规划文件与并行会话文件一律未动。

## 0. 总览

起点：与首轮交付同一工作区（源码基线 `cb09a06`、规划基线 `7b82c04`，首轮 diff 未提交），
本轮在其上叠加修复；环境依赖不变（Apple Container 1.5.0 / postgres 17-alpine / redis 7 /
官方 langgraph-api 镜像 3.13 = langgraph-api 0.15.1 + langgraph 1.2.11 / Node dev 前端）。
OpenSpec 文件版本：本轮仅追加 tasks.md 修复轮注记与 acceptance.md §9 勘误，未改规格正文。

| 项 | 结果 |
|---|---|
| R01–R11 | 11 项全部实现修复并有修复前失败/修复后通过的回归；逐项见下 |
| admin API 套件 | **107 passed / 0 failed**（清洁双专用库 `air_agent_repair_api` + `air_agent_repair_rls`，仅执行声明迁移链 0001–0005 + 项目内 conftest 前置目录，无业务库手工授权）——对应评审 94/4 的四项授权缺陷全部消除 |
| root 单元 | 266 passed / 26 skipped / **5 failed**——与基线完全同名同因（checkpoint 1 + knowledge_route 4），未删未跳任何测试 |
| root 集成 | 7 passed / 15 skipped / 1 failed——500k 用例缺依赖为既有基线；新增 `test_artifact_tenant_rls_pg.py`（4 测，配 `TENANT_TEST_*` 时运行）与 `test_live_tenant_claims.py`（4 测，需 `RUN_TENANT_LIVE_E2E=1`） |
| 真实联调（当前官方镜像 0.15.1 + 真实 PG + 聊天代理 3000） | **4/4 通过**：未知历史归属认领拒绝、pending 未确认不可读、内部端点鉴权与幂等、同 ID 并发单一归属、聊天代理旧标签页 409/403、票据 TOCTOU 409 |
| 聊天前端契约 | 13 passed（原 8 + R10 停止确认 4 + R02 代理头 1） |
| 两前端 | tsc 通过；聊天生产构建（`npm run build -- --webpack`）通过；admin/web 构建未跑（dev 服务器实测通过，见 R11） |
| 浏览器实测 | admin 双标签旧上下文 409→提示→收敛当前企业；企业管理员角色全流程（创建角色→可授予权限树→保存 0→1）；平台关联同名多企业 tenant_admin 成功且同企业约束成立；聊天端多企业选择页、运行中切换企业等待停止确认、旧标签页 409 后上下文刷新 |
| MCP 真实平台授权（7.6） | **保持未完成**。真实统一平台缺生产协议/A/B 凭据与授权数据；企业 MCP 生产保持安全禁用（白名单登记表无生产条目 + 生产环境无 `TENANT_MCP_FIXTURE_MODE`）。本报告不宣称任何真实平台授权通过 |
| 六图长运行/归档重对话浏览器/部署恢复 | 维持未完成：见 §13 未完成清单；模拟与实测逐条分开标注 |

复现纪律：所有修复前失败证据 = 评审探针（live/admin/runtime/empty 四件）先原样复跑确认缺陷，
再把同一复现输入转成项目内正式回归（下列每节给出"修复前断言 → 修复后断言"与可复跑命令）。
凭据全部来自受保护环境（`.env`/`REVIEW_FIXTURE_PASSWORD`/`INTERNAL_API_TOKEN`），未写入任何文件；
未重置业务卷，未执行任何不明确归属的历史迁移。

## 1. R01 历史线程认领越权（P1 安全）

**根因**：`on_thread_create` 无条件 `INSERT … ON CONFLICT DO NOTHING`——对"平台已存在但无归属行"
的历史线程，一次带 `if_exists=do_nothing` 的同 ID 创建即可把未知历史归属登记为本人；`thread.read`
随后按本人归属放行，暴露他人线程 metadata。官方镜像（0.15.1）实测无 after-create 钩子
（`Auth.on.after` 不存在），无法"创建后确认"，协议必须自证。

**修复**（`src/common/identity/db.py`、`langgraph_auth.py`、`src/common/artifact_http.py`、
`frontend/app/api/[..._path]/route.ts`、迁移 0005）：

1. **严格创建**：native 层拒绝 `if_exists` ≠ raise 的线程创建（403），平台层 409 天然裁决重复。
2. **原子认领 + 归属冲突**：`claim_thread_ownership` 以 `INSERT … ON CONFLICT DO NOTHING RETURNING`
   原子插行；无 RETURNING 时按 RLS 作用域复查——同归属=本人重试放行、他人/不可见=`conflict` 409。
3. **pending 语义**：新认领落 `source='native_pending'`（0005 扩展 CHECK），read/update/delete/
   create_run 对 pending 一律 404——**创建未被确认可信前，连创建者本人也不可见**；
   认领失败残留的 pending 行不改变未知历史归属的不可访问性。
4. **真实创建确认**：聊天代理在平台返回 2xx 后调用受限内部端点
   `POST /internal/threads/claim-confirmation`（X-Internal-Token + 票据身份三元组，
   `UPDATE … WHERE source='native_pending' AND tenant/user 匹配`，已确认重复=幂等 true、
   无匹配=false）升级为 `native`。代理持 409（上次成功+确认丢失）时走同一确认再回读，
   失败原样透传——未知历史不可能经此路径确认（无本人 pending 行）。
5. 归属登记失败/确认端点故障全部失败关闭（503），不匿名放行。

**修复前断言**（评审 live_probe 复跑，2026-10-12 18:0x）：`legacy_claim_create_status=200`、
`legacy_read_after_claim=200`、`other_user_synthetic_marker_exposed=true`。
**修复后断言**：do_nothing→403；同 ID 严格创建→409(conflict)；读→404；
`test_live_unknown_history_claim_denied` 通过（断言上述全部）。

**回归**：`tests/unit_tests/test_thread_claim_protocol.py`（7 测：do_nothing/update 拒绝、conflict 拒绝、
pending 不可读、migrated/native 可读、后端故障 503）；
`tests/integration_tests/test_live_tenant_claims.py::test_live_unknown_history_claim_denied /
test_live_pending_until_confirmed_and_confirmed_flow / test_live_same_id_concurrent_create_single_owner`
（真实 8100+2024+业务 PG；后者断言并发双请求恰好一成功）。

**范围**：真实 PG + 当前官方镜像（langgraph-api 0.15.1 / langgraph 1.2.11）实测。

## 2. R02 两前端旧企业上下文写入（P1 数据边界）

**契约**：两前端所有业务请求携带 `x-tenant-id` + `x-tenant-context-version`（非授权性提示），
服务端与实时会话逐一比对：写缺失→403 `context_missing`；不一致→409 `context_changed`
（admin 无企业上下文的管理写以 `none` 显式声明）。票据签发同样携带目标企业（TOCTOU 双保险，
admin API 409 时代理透传而非伪造票据）。

**聊天端**：`frontend/lib/langgraph-api.ts` 增加 `setTenantRequestContext`/SDK `onRequest`
统一注入 + `onTenantContextConflict` 订阅；`frontend/lib/session-client.ts` 在 me/switch/刷新后
登记声明并在 409 时"刷新会话→清空旧企业列表→重载当前企业图→提示"；代理 `route.ts` 校验后
才换票转发。admin 端：`admin/web/lib/api.ts` 同源注入、`session.tsx` 登记 + 409 刷新、
服务端 `require_tenant_context` 应用于 departments（整路由）、tenants、roles、
`PUT /auth/me/followed-regions`。

**修复前断言**（复跑评审探针）：`stale_tab_wrote_into_new_tenant=true`（200 落入新企业）、
`admin_stale_write_wrong_tenant=true`。**修复后**：两处均 409，浏览器实测（§12）看到
"企业上下文已被其他标签页变更"提示、旧列表清空、页面收敛当前企业；无头写→403。

**回归**：`admin/api/tests/test_tenant_api.py::test_stale_tab_write_rejected_with_context_conflict /
test_write_without_context_headers_rejected`（新增 `tests/ctx_client.py` 模拟前端声明，
全套件按新契约迁移）；`frontend/tests/chat-contract.test.cjs::proxy: stale-tab claim rejected…`；
`tests/integration_tests/test_live_tenant_claims.py::test_live_chat_proxy_stale_tab_write_denied`
（真实代理 3000 + 真实 admin/原生端）。

## 3. R03 归档/计算数据库防护（P1）

**根因**：Agent 归档以 `DATABASE_URI`（owner、无 RLS）连接；`tool_artifact_storage`/
`artifact_computations` RLS=false；计算表无租户列；Agent 侧自动建表与 Alembic 双真相源。
（**本轮自纠**：首版修复把计算表的"父归档同租户 EXISTS"写成第二条独立策略——PG 多条
permissive 策略按 **OR** 合并，会被"GUC 匹配"单独放行；已在 0005 中合并为单一 AND 策略，
并同步到业务/repair 库。）

**修复**：迁移 0005 对两表启用 RLS（`FOR ALL TO air_agent_runtime`，tenant+user 与事务本地
GUC 相等，计算表叠加同策略内父归档绑定）+ 运行角色表权限；`artifact_computations` 补
`tenant_id/user_id` 列并回填；`src/common/storage/artifact_db.py`：PG 模式请求路径只经
`RUNTIME_DATABASE_URI`（缺失→`ArtifactBackendUnavailable` 失败关闭，绝不回退 owner），每语句
事务本地 `SET LOCAL`；过期清理/回填改走受限维护角色 `MAINTENANCE_DATABASE_URI`
（0005 对其只授元数据列 SELECT + 状态列 UPDATE + GUC 策略，读不到载荷；bootstrap 可选创建）；
PG 模式 Agent 不再自动 DDL，缺表即失败关闭提示执行迁移；SQLite 本地开发路径保持。

**修复前断言**（评审 live_probe）：两表 `relrowsecurity=false`、计算表无租户列。
**修复后断言**：两表 RLS=true 且 owner 为 air_agent、计算表含租户列（空库/旧库两条路径均验证，
见 §5）；跨企业读写/漏条件扫描/计算认领越权在真实 PG 全部被拒。

**回归**：`tests/integration_tests/test_artifact_tenant_rls_pg.py`（4 测，真实 PG）——RLS 开启与
非 owner 断言、跨企业读/改/无 WHERE 扫描、计算同租户绑定 + 缺作用域 fail-closed、
RUNTIME/MAINTENANCE URI 缺失失败关闭。`claim_computation` 全调用点带租户上下文
（tool_wrappers / computation/tool / data_governance），旧测试按新签名迁移（未删除任何测试）。

## 4. R04 运行中撤权 + 真实 ModelRequest Runtime 取身份（P1）

**两处缺陷**：(a) `RuntimeIdentity` 不带修订快照，`revalidate` 只看 active 标志——角色撤销
（revision 前进、状态仍 active）与 A→B→A 切换不会中断已开始运行；(b) `TenantRuntimeGuardMiddleware._config_of`
读 `request.runtime.config/get_config()`，实测镜像内 `langgraph.runtime.Runtime`（1.2.11）
两者均无（字段仅 context/store/stream_writer/heartbeat/previous/execution_info/server_info/control），
镜像的 run 构造链也不注入 `server_info`——合法 DeepAgents 模型调用整体被误拒。

**修复**：(a) `authenticate` 把已核验的 `session_ctx_version/user_auth_revision/tenant_auth_revision/
membership_revision` 写入可信 `langgraph_auth_user`（非秘密），`RuntimeIdentity` 携带快照，
`revalidate` 逐项比较（快照缺失只降级为状态检查、不放宽）；A→B→A 靠 ctx_version 收敛。
(b) 中间件改用框架支持的可信来源逐级提取（`identity_from_request`）：`langgraph.config.get_config()`
环境（Runtime 官方文档给出的受支持访问方式）→ `ToolRuntime.config` → `server_info.user`，
绝不读客户端自报字段；DeepAgents 与显式图共用同一 `guard_runtime_call`。

**修复前断言**（评审 runtime_probe 复跑）：`revision_changed_runtime_denied=false`、
`model_runtime_config_extracted=false`。**修复后**：修订不一致全部拒绝（4 项单测）；
用**真实 langchain agent + 真实 Pregel 调用**（非 SimpleNamespace 伪造）断言模型节点能取到
可信身份、缺身份 fail-closed、撤权修订拒绝——并把模型调用次数钉死为 1（防"提取失败被误拒/
被旁路"两类回归）。

**回归**：`tests/unit_tests/test_runtime_identity_revisions.py`（6）、
`tests/unit_tests/test_guard_middleware_framework_compat.py`（3，真实 `create_agent` 调用）。
注：本组合曾在外部 LangSmith tracing 序列化 fake 模型时出现迭代器干扰，按评审 README 同口径
以 `LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false` 运行（§14）。
尚未重新执行三个 DeepAgents 图的完整"合法模型调用到真实 LLM"端到端（网关外部条件），如实记录。

## 5. R05 空库迁移（P1 部署）

**根因**：0004 空库分支字符串拼接 `…tenant ON` + `tool_artifact_storage(…)` 缺空格→
`ONtool_artifact_storage`，全新库 `upgrade head` 退出 1。**且评审只暴露了 SQL 缺陷；同分支
对"更早旧表（无 user_id/thread_id 列）"仍会因索引引用缺列失败**——已把存量库分支改为幂等
补齐全部治理列再建索引。0004/0003 未提交未发布，直接修正声明链（业务库已执行过的分支不受影响）。

**验证**（两条路径，均全新专用库、执行后删除临时库）：
- 纯空库 `CREATE DATABASE … ; alembic upgrade head` → **exit 0**，`ONtool` 缺陷串不再出现，
  两归档表 RLS=true；
- 旧库存量路径：预建无租户列的 `tool_artifact_storage` → `upgrade head` → **exit 0**，补齐
  tenant/user/thread 列且 RLS=true；业务库 `air_agent` 旧库升级至 0005 同样 exit 0。

命令见 §14 `R05 复验`。

## 6. R06 同名身份迁移 + 私有表 metadata 直改（P1 身份迁移）

**修复（一）同名核验**：`_validate_manifest` 接受显式 `canonical: pg|json` +
`resolve_pg_user_id`；preflight 对"manifest json 用户 account 已存在于 PG 且 UUID 不同"
未显式决议 → `same_account_conflict` 硬冲突（clean=false），canonical=pg 但指向不符 → 冲突；
apply 端三重防线：指纹命中台账 `entity_type='manifest'` 幂等短路（早于 preflight，解决首轮
apply 自建同名造成的复跑误报）、json 用户遇既有 PG 账号仅当 `canonical=pg` 且 id 一致或台账
`entity_type='user'` 证明本清单所建才复用、否则 SystemExit 停止——不再 `ON CONFLICT DO NOTHING`
静默复用；canonical=pg 路径不触碰原哈希（哈希保全断言验证），成员/角色绑定到规范账号并写
user 实体台账行。

**修复（二）受限受支持 API**：删除对平台私有 `thread` 表的 `UPDATE` 直写。新增 mt-v1 迁移票据
（admin `security.py::sign_migration_ticket` ↔ agent `ticket.py::verify_migration_ticket` 字节兼容，
单目的、绑定 thread、60s、独立受众 `langgraph-migration`）；迁移脚本改经原生
`PATCH /threads/{id}` 提交可信键；auth 侧 `authenticate` 识别 mt-v1 后 `_deny_migration`
封死其余全部资源路径，`on_thread_update` 的迁移分支要求：票据 thread==目标、归属行存在且
`source='migrated'`（台账登记）、metadata 可信键强制覆盖、平台专属权限不变。平台不可达时
记 `metadata_pending` 计数并复跑重试，绝不回退直写。

**修复前断言**（评审 admin_probe 复跑）：`same_name_preflight_clean=true`、
`same_name_silently_reused_pg=true`；脚本源码含私有表 UPDATE。**修复后**：preflight clean=false
（same_account_conflict）、apply 非零退出且 PG 原哈希可验/JSON 口令不可验、canonical=pg 复用
身份+成员绑定正确+台账映射行存在、metadata 更新走平台 API（stub 收到已验签票据请求）、
平台不可达时 `applied.threads=0, metadata_pending=1` 且 thread 表未被触碰。

**回归**：`admin/api/tests/test_tenant_migration.py::test_same_name_identity_requires_explicit_canonical /
test_canonical_pg_resolution_reuses_identity_preserves_hash /
test_metadata_migration_uses_supported_api_not_private_table`（真实独立 PG + 本地"平台替身"
uvicorn 端点验 mt-v1 后按平台语义合并——脚本侧真实签名、真实 HTTP，钩子拒绝面由 agent 侧
`tests/unit_tests/test_migration_ticket_auth.py`（5 测）覆盖）。mt-v1 跨项目字节兼容由两侧
对称实现 + agent 手工签名向量断言。**限制**：迁移在真实镜像内的端到端（真实钩子+真实私有表
禁写）未执行，见 §13。

## 7. R07 未核验 MCP 协议门禁与认证发现（P1）

**修复**：`VERIFIED_PROTOCOLS` 登记表（协议→provider/主体字段映射/fixture_only），
`resolve_binding` 白名单：`unverified`、任意未登记名（评审反例 `never-verified-protocol`）、
未实现 provider、`v1-fixture` 在非 fixture 环境一律拒绝——生产环境（compose/Dockerfile 不注入
`TENANT_MCP_FIXTURE_MODE`）下**所有企业 MCP 调用被拒，企业 MCP 安全禁用**；生产协议登记必须
等 7.6 材料核验，登记表当前无生产条目。`_discover` 改为认证发现：连接配置携带
`DEFAULT_PROVIDER.auth_headers(binding)`，initialize/tools/list 与 tools/call 同一绑定凭据；
缓存键含 binding revision，轮换自然重发现。fixture 服务器加协议门禁：全部 MCP 路径
（含匿名握手）无有效 Bearer → 401，控制面暴露已认证握手计数。admin `PUT /mcp-bindings`
落库前拒绝未登记协议名（只允许 `unverified`/已登记），杜绝管理面写入可执行未知协议。

**修复前断言**（复跑）：`unknown_protocol_denied=false`（resolve 通过并用默认 Bearer）、
fixture 允许匿名 tools/list。**修复后**：未登记协议拒绝（错误文案"未登记核验"）、fixture 模式
关闭时连 v1-fixture 也拒绝、发现后匿名握手计数=0 且已认证握手>0、匿名 tools/list=401、
未知协议绑定=400。

**回归**：`tests/unit_tests/test_mcp_scoped_authorization.py::test_unregistered_protocol_denied /
test_fixture_protocol_denied_outside_fixture_mode / test_discovery_is_authenticating`（真实
fixture 子进程服务器）+ admin `test_binding_unknown_protocol_rejected`。
**未做（如实）**：真实统一平台的 initialize/tools/list/call、A/B 凭据、无秘密 Trace 核对——
**7.6 保持未完成**，本报告任何位置不声称真实平台授权通过。

## 8. R08 受限数据库授权 + 清洁库复现（P1 管理可用性）

**根因**：0003 列权限缺 `users.is_active`、`user_followed_regions` 缺 UPDATE/DELETE——纯声明
迁移的独立库上账号停用 500、关注替换 500、带关注账号删除 500（评审 94/4 中的 4 项）。

**修复**：全部以**最小受限授权**落地（0005，GRANT 幂等；不 GRANT ALL、不 owner 连接、不动
策略语义——`user_update` 仍本人∪平台、`followed_user_scoped` 仍本人∪平台）。评审四例在
`air_agent_repair_api`（仅 0001–0005 + conftest 前置目录：seed 权限/super_admin/viewer/3347
区划，全部项目内 conftest 幂等声明，无业务库手工授权）复跑全绿；同时把此前散落在外部脚本
（setup_probe/seed_rls_probe）的前置目录**项目化**为 `admin/api/tests/conftest.py`，RLS/迁移
专项库（`TENANT_TEST_*`）同口径前置。测试客户端按 R02 契约携带声明头（`tests/ctx_client.py`）。

**额外修复**：regions 字典用例改为数据无关断言（旧断言 `count==0` 依赖"库未导数据"，与
前置目录冲突）。

**结果**：94/4 → **107/107**（含新增回归），清洁库两次独立复跑一致。

## 9. R09 稳定 key ID 轮换（P2）

**修复**：`RUNTIME_TICKET_KEYS` 推荐并实现显式 kid 映射（JSON 对象 `{"<kid>":"<secret>",…}`，
首条目签发）；两侧（`src/common/identity/ticket.py::parse_keys`、
`admin/api/app/config.py::runtime_ticket_key_pairs`）字节兼容同规则，kid 与密钥内容绑定——
前插新 key、重排、交错部署均不使旧票据失效；撤去 key 才失效；kid 空/重复/非字符串失败关闭。
旧字符串数组（索引 v1..vN）保留为**过渡兼容**并在 `.env.example`/config 文档标注缺陷，真实
`.env` 未强制变更（当前运行栈以既有值通过全部票据回归）。

**修复前断言**（复跑 admin_probe 轮换段）：`previous_ticket_valid_during_rotation=false`。
**修复后**：`test_old_ticket_valid_when_new_key_prepended`（agent）、
`test_ticket_key_pairs_object_format_stable_kids`（admin）双向通过，配套
`test_withdrawn_key_no_longer_verifies`、非法配置两侧 fail-closed。

## 10. R10 真正等待停止确认（P2 用户流程）

**修复**：`chat-store.stopStreaming` 改为 `Promise<{confirmed}>`：中断本地流后向服务端
`runs.cancel`，并轮询 `runs.get` 至终态（cancelled/success/failed/timeout 或 404 均视为已停止
=完成竞争安全）；`cancelRun` 409 视为对方已结束；未拿到 run_id 时用 `runs.list` 找回活动 run
再取消；请求异常/15s 超时返回 `confirmed:false`，**不谎称已确认**。TenantBadge 仅在
`confirmed:true` 时继续 `switch-tenant`，否则保留 busy 归位并给可读错误（修复前 await 一个
同步返回 void 的函数并把本地标志位当确认）。

**修复前断言**（源码级）：`await stopStreaming()` 立即 resolve、isStreaming 恒即时 false，
cancelRun 仍在异步执行。**修复后断言**：契约测试钉死"观察到 cancelled 才返回 confirmed=true、
取消 500→confirmed=false、cancel 409→true（完成竞争）、无 run_id 从列表恢复"。

**回归**：`frontend/tests/chat-contract.test.cjs` 新增 4 测；浏览器实测：运行中点切换企业→
确认框→停止等待→成功切换并清空（§12）。

## 11. R11 停用企业与企业角色/管理员关联流程（P1 企业管理）

**修复**：
- departments 路由挂 `require_active_tenant`（停用/未选企业的企业写入口 403）+ `require_tenant_context`；
- members 写入口按目标企业状态把关：link/update-member/roles 停用即 403（恢复性 remove 仅平台）；
- `_apply_member_roles`/`set_member_roles` 角色查找带 `scope=tenant + tenant_id`（双企业同名
  tenant_admin 不再取多行造成复合外键 IntegrityError/跨企业授予），未知角色码 400 拒绝；
- 新增 `GET /permissions/grantable-tree`（`role:assign_permission` 即可读的"本企业可授予目录"，
  非平台再收窄为操作者持有码——与角色写校验同源）；
- admin/web 企业角色页把"角色列表"与"权限树"解耦并改用可授予目录（修复前 Promise.all 被
  permissions/tree 403 拖垮整页）。

**修复前断言**（复跑评审 admin_probe）：`suspended_tenant_department_write_status=201`、
`enterprise_permission_tree_status=403`、`platform_link_tenant_admin_error=IntegrityError`。
**修复后**：403/403（停用企业登录侧为"尚未选择企业"，两口径均为有效拒绝，断言两者皆可）、
可授予树 200、关联成功。

**浏览器实测**（§12）：平台管理员在 e2e-a 关联 acc_bob + tenant_admin（该账号已在 e2e-b 持同名
角色，正对评审双企业 fixture）→ DB 断言 `same_tenant=true`；acc_alice 进入 e2e-a 企业角色页 →
列表渲染、新建 `repair_readonly`、勾选 `audit:read` 保存 → 权限数 0→1。
**遗留**：admin/web 生产构建未跑；浏览器仅实测本地 e2e 账号路径。

## 12. 浏览器实测记录（本地 dev 栈：3000 聊天、3100 后台、8100 admin API、2024 原生容器）

1. 后台：acc_admin 登录 → 企业列表 → e2e-a「关联成员」acc_bob+tenant_admin → 对话框成功关闭
   （修复前该动作在双企业同名角色下 IntegrityError）；psql 验证归属行同企业。
2. acc_alice：登录→多企业选择页（不自动落第一个，符合规格）→进入 e2e-a→企业角色页正常渲染
   →新建角色→权限对话框展示可授予目录→勾选保存成功（权限数 1）。
3. R02 双标签：tab2 部门页停留 A；tab1 切 B；tab2 新建部门 → 整页提示
   "企业上下文已被其他标签页变更，页面已重新加载"→重试→tab2 收敛 B（无部门读权限的正确 403），
   不再展示/写入 A 数据。
4. 聊天端：运行中切换企业→确认框→等待停止确认→切换成功、列表清空（R10）；另一标签持 A 上下文
   发送→409→`企业上下文已刷新` 提示、切换器显示 B、旧线程列表清空（R02）。
5. 截图/日志未落盘（页面文字快照为准）；会话 cookie/票据不外泄。

## 13. 保持未完成/未实测项（不假称）

- **7.6 真实统一平台 MCP**：缺生产协议文档、A/B 授权凭据、可区分授权数据；`docs/mcp-auth-contract.md`
  缺项清单不变；登记表无生产条目；企业 MCP 生产禁用。已完成的仅是受控 fixture 契约与门禁。
- **六图完整长运行**（含 DeepAgents 三图对真实 LLM 网关的合法模型调用）：网关外部条件未就绪；
  本轮以真实框架调用 + 契约测试覆盖守卫逻辑本身。
- **归档浏览器重对话全链路 / 部署与恢复演练（8.4 drill_recovery）本轮未重跑**：沿用首轮隔离库
  演练记录，但其结论须以本轮新 schema（0005）复跑后方为最新。
- **admin/web 生产构建**未执行（tsc 通过）。
- **500k 集成失败与 root 5 项失败**为既有基线，未掩盖未删除；`make test`（裸 python）失败与
  PYTHONPATH=src 差异按环境记录保留。
- 业务库中 e2e 验收资产新增：acc_bob 在 e2e-a 的 tenant_admin 成员关系、`repair_readonly`
  企业角色（浏览器实测产物，供复验；未删除以保留可复核性）。
- `air_agent_repair_api` / `air_agent_repair_rls` 两个专用验收库**保留**（清洁复现输入），
  临时空库/旧库探针库已删除。

## 14. 可复跑命令（复验入口；凭据经环境提供）

```sh
# 通用前提：Apple Container 栈运行（run-local.sh：postgres/redis/langgraph-api + :8100/:3000）；
# 原生服务改动后需 `container rm -f air-agent-langgraph-api && container-compose up -d --profile prod langgraph-api`
# （src 为挂载代码，重启即生效；勿 stop/start 以免脱离 compose 网络）。

# 1) root 单元/集成（外部 tracing 显式关闭——与评审 README 同口径；基线 5 failed + 500k 1 failed 为既有）
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false PYTHONPATH=src \
  .venv/bin/python -m pytest tests/unit_tests -q
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false PYTHONPATH=src \
  .venv/bin/python -m pytest tests/integration_tests -q   # 配 TENANT_TEST_* 时归档 RLS 4 测随之启用

# 2) 真实 PG 归档/计算 RLS（专用库）
export TENANT_TEST_OWNER_URI=… TENANT_TEST_RUNTIME_URI=…   # 指向 air_agent_repair_rls（或 tenant_test）
LANGFUSE_ENABLED=false PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration_tests/test_artifact_tenant_rls_pg.py -q

# 3) R01/R02 真实联调（原生 2024 + admin 8100 + 聊天代理 3000 + 业务 PG）
#    口令仅经环境提供；探针只创建/删除合成线程
RUN_TENANT_LIVE_E2E=1 REVIEW_FIXTURE_PASSWORD='<环境提供>' LANGFUSE_ENABLED=false \
  LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false admin/api/.venv/bin/python -c "
import os, subprocess
from dotenv import load_dotenv; load_dotenv('.env', override=False)
os.environ.setdefault('TENANT_E2E_OWNER_URI', os.environ['DATABASE_URI'])
subprocess.run(['.venv/bin/python','-m','pytest',
  'tests/integration_tests/test_live_tenant_claims.py','-q'])"

# 4) admin 套件（清洁双库复现；一次性建库脚本见 5)）
export DATABASE_URI=…ADMIN_RUNTIME_DATABASE_URI=…(= air_agent_repair_api owner/runtime)
export TENANT_TEST_OWNER_URI=…TENANT_TEST_RUNTIME_URI=…(= air_agent_repair_rls)
cd admin/api && LANGFUSE_ENABLED=false .venv/bin/python -m pytest tests -q   # 期望 107 passed

# 5) 清洁库从零复现（R05/R08 复验：建库→声明迁移链→跑套件）
#    空库与旧库两条路径各自 CREATE DATABASE（OWNER air_agent）后：
(cd admin/api && DATABASE_URI=<新建库 owner URI> .venv/bin/python -m alembic upgrade head)   # 期望 exit 0
#    （旧库路径：先按评审 setup_probe 同构预建 tool_artifact_storage 再 upgrade）

# 6) 两前端与规格
npm --prefix frontend run test:contracts            # 13 passed
(cd frontend && ./node_modules/.bin/tsc --noEmit)
(cd admin/web && ./node_modules/.bin/tsc --noEmit)
npm --prefix frontend run build -- --webpack
openspec validate add-tenant-identity-access --strict

# 7) 评审探针原样复跑（诊断输入，非项目回归）：docs/reviews/evidence/flash-01/README.md 命令组
```

## 15. 迁移与配置影响

- 新迁移 **0005**（幂等、非破坏）：thread_ownership CHECK 扩 `native_pending`；users 列权限+
  `is_active`；`user_followed_regions` UPDATE/DELETE；归档/计算表 RLS + 运行角色表权限；
  计算表租户列+回填+索引；可选维护角色受限授权（角色缺失自动跳过）。业务库与两专用库已应用。
- 0004 就地修正（未发布迁移）：空库分支空格缺陷 + 存量库分支列级防御。
- 线程创建协议变化：原生 `POST /threads` 只接受严格创建、新线程在代理确认前 pending 不可读；
  直连原生 API 的合法工具需按"create→(代理身份)claim-confirmation"两步走（本项目唯一浏览器
  入口是聊天代理，已内置确认与 409 补偿）。
- 新环境要求：Agent PG 模式必须提供 `RUNTIME_DATABASE_URI`（原已具备）；过期清理需
  `MAINTENANCE_DATABASE_URI` + bootstrap 维护角色，未配置时清理任务记日志并禁用（fail-closed，
  不影响读写）。新增可选 `TENANT_MCP_FIXTURE_MODE`（仅测试环境）与 `RUNTIME_TICKET_MIGRATION_AUDIENCE`
  （默认 `langgraph-migration`）；`RUNTIME_TICKET_KEYS` 推荐对象形态（旧数组仍兼容）。
- 请求头契约：两前端业务请求携带 `x-tenant-id`/`x-tenant-context-version`（管理面无企业=
  `none`）；缺失写=403、冲突=409 context_changed，前端已同步处理。
- `.env.example` 同步更新（不含真实值）；`langgraph.json`/Dockerfile/compose 无结构变化。

## 16. 修复用时（单列；首次 4 小时记录不变）

本轮（第二轮）实测自约 16:45 起至报告成稿 20:2x，**墙钟约 3 小时 40 分**，其中包含：
测试套件与构建等待（root/admin 全量各 3 轮，约 25 分钟）、镜像钩子能力探针与容器两次重启、
一次浏览器验证往返、以及三处"自纠"（RLS 策略 OR 合并缺陷、台账幂等、旧库分支列防御）的定位。
未采集 Token/工具次数。与首交 4 小时合并的"达验收总用时"待独立复验通过后回填。

## 17. 对首次交付记录的勘误（不改评审，只修自己的报告）

`docs/reviews/tenant-identity-access-acceptance.md` 首版中下列声明被轮次01 证伪、本轮修复后
才重新成立：2.1 空库 upgrade、3.2/9.2"普通操作旧上下文约束"（当时仅切换接口校验）、
5.4 运行时身份提取（镜像内恒 None）、6.2 归档数据库防护（当时 owner+WHERE 兜底）、
7.2/7.4 未核验协议默认禁用与认证发现（当时可被改名绕过、discovery 匿名）、
8.2"受限更新线程 metadata"（当时直写私有表）。已在该文件末尾追加勘误与本轮证据索引，
未改写其原始正文其余部分。
