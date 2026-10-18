# I2 Agent接入共享IAM —— 首次实施交付报告（待独立验收）

- Change：`switch-agent-to-remote-iam`
- 实施者：本轮I2实施（匿名，无编号）
- 实施日期：2026-10-16
- Agent 基线：`319dab4`（dev 分支）；IAM 基线：`8da8ba1`（只读）
- 交付形态：**可审查 diff（未 commit/push/merge/archive）**；OpenSpec tasks 15/15 勾选（实现+验证同时完成）
- 证据目录：`docs/reviews/evidence/run/i2-20261016-224541/`（Git 忽略，含全部探针/联调脚本与原始输出）

---

## 1. 改动与理由

### 1.1 新增

| 文件 | 职责 |
|---|---|
| `src/common/identity/remote.py` | 显式 local/remote 后端选择（非法值失败关闭）；严格 IAM HTTP DTO（`iam.identity.v1` 全字段校验、bool/字符串修订拒绝、HTTP200≠授权）；总 deadline（默认 500ms，`IAM_INTROSPECT_DEADLINE_SECONDS`）；异常映射（网络/超时/内部client认证→503，主体无效→401，修订不匹配→409，协议非法→503）；进程级连接复用（配置绑定重建，防陈旧连接） |
| `src/common/identity/ticket_binding.py` | 签票门面（introspect含引用→IAM签票→本地验签→票据身份/四修订与引用快照完全一致否则409不登记）；绑定存储（票据SHA-256摘要→引用/身份/四修订/入口到期；BFF与native共享业务库表）；native复核（绑定查找+逐项比对+带内部client的execution_context实时复查） |
| `admin/api/app/services/remote_identity.py` | remote 身份解析（handle→token摘要核对→IAM实时复查→同UUID投影upsert→Identity）；应用会话登记/撤销；业务投影行技术写入（口令哨兵、无本地角色行） |
| `admin/api/app/services/remote_mode.py` | admin/api 侧部署模式读取（与 src 侧同语义） |
| `admin/api/app/routers/iam_remote.py` | remote 认证门面（仅 remote 时挂载且**先于** local auth 路由）：SSO start（一次性 state HttpOnly cookie）/ callback（state+ticket 一次性兑换→introspect→会话登记→投影→303干净路径）/ logout / switch-tenant（行CAS→IAM CAS→内省刷新→投影） |
| `admin/api/migrations/versions/001{4,5,6,7}_*.py` | `agent_iam_sessions`（服务端应用会话：handle/token摘要/四修订/引用/IAM会话id/受控token）、`agent_iam_ticket_bindings`（跨进程票据绑定）、iam_reference_* 引用投影 |
| `admin/api/scripts/sync_iam_projection.py` | 同UUID投影同步（dry-run 默认；同名不同UUID冲突报告阻止正式切换；不自动合并） |
| `frontend/lib/iam-api.ts` | 前端 IAM 面专用客户端（显式端点清单；业务面 admin-api.ts 不动） |
| `frontend/app/api/auth/iam/{start,callback}/route.ts` | 聊天前端 BFF 的 SSO 转发（ticket/state 仅此一跳；绝对重定向） |
| `tests/unit_tests/test_remote_identity_client.py` | A01–A04 客户端纯契约测试（27 项） |
| `tests/unit_tests/test_remote_runtime_guards.py` | A08 remote 逐发送守卫测试（6 项） |

### 1.2 修改（均为最小接线）

- `src/common/identity/runtime.py`：`RuntimeIdentity.verification_ref`（非秘密，local 恒 None）；`revalidate` remote 分支走执行引用复查。
- `src/common/identity/langgraph_auth.py`：authenticate remote 分支（票据→绑定→IAM实时复查→注入非秘密引用）；mt-v1/rs-v1 分支不受影响。
- `admin/api/app/deps.py`：`get_identity` 按模式分派（local 原语义零变化）。
- `admin/api/app/main.py`：remote 时 iam_remote 路由先注册（同名路由优先命中）。
- `admin/api/app/routers/auth.py`：remote 下本地登录 403。
- `frontend/lib/auth.ts`：会话 cookie 名按模式分派（`air_agent_iam_handle`）。
- 前端登录页（两处）：SSO 按钮（不重做 UI）。
- `.gitignore`：证据 run 目录与 remote 栈本地配置。

## 2. A01–A14 矩阵（摘要）

详见 `evidence/run/i2-20261016-224541/a-matrix.md`。

| 结论 | 项 |
|---|---|
| ✔ 通过 | A01–A12、A14 |
| ⚠ 部分受阻 | A13：18/24 格 ok；DeepAgents 三图 ×6 格受阻（图内 LLM 网关宿主未运行，Connection refused 实测） |

关键负例全部拒绝：非法后端模式、remote 缺配置、断网、伪造token、rt-v1当subject_token、伪造/错配执行引用、跨client复查、无绑定票据、错state、SSO重放、未发起回调、handle篡改、登出后旧handle、迟到CAS提交、未登记Origin。

关键正例：真实 IAM 62 秒撤销屏障（>60s 合法运行继续经执行引用；logout 后重试不绕过）；跨进程桥接（BFF 登记→native 复核同 ref 同修订）；A→B→A ctx 单调递增；投影同UUID FK 业务读写。

## 3. 基线与回归

| 套件 | 基线（实施前实测） | 交付时 | 说明 |
|---|---|---|---|
| `tests/unit_tests` | 6 failed, 365 passed, 75 skipped | 6 failed, 398 passed, 75 skipped | 失败集 diff **逐一相同**（6 个既有失败 nodeid：knowledge_route×4、checkpointing、guard_middleware） |
| `admin/api tests` | 191 passed, 5 skipped | 191 passed, 5 skipped | local 语义零回归 |
| frontend contracts | — | 57 pass | 通过 |
| frontend build | — | 成功 | 通过 |

未删断言、未弱化条件、未扩大 skip；原门禁不降低。

## 4. 迁移与配置影响

- 新迁移 0014–0017（接当前 head；隔离库 i2_business 空库全量升级 + downgrade/upgrade 往返演练通过，5 张新表回读确认）。
- 部署新增配置（remote 才需要；local 缺省零变化）：`AGENT_IDENTITY_BACKEND=remote`、`IAM_BASE_URL`、`IAM_ISSUER`、`IAM_INTERNAL_CLIENT_ID/SECRET`、`IAM_RUNTIME_AUDIENCE`、（SSO 门面）`IAM_SSO_CLIENT_*` / `IAM_SSO_ADMIN_CLIENT_*`、（可选）`IAM_INTROSPECT_DEADLINE_SECONDS`。
- local 部署：不注册 remote 路由、不发 IAM 请求、不建绑定——全部既有行为不变。

## 5. 实际地址与凭据位置

| 资源 | 地址/位置 | 所有权 |
|---|---|---|
| IAM 隔离副本 | HTTP 127.0.0.1:50100；凭据 `/Users/acerola/Dev/Java/i2-iam-copy/.env.local`（0600，gitignored） | 本轮I2实施（容器 i2-iam-pg17/i2-iam-redis7，卷 i2-iam-*） |
| 隔离业务库 | `i2_business` @ 共享容器 15432；运行角色 `i2_runtime`（口令在上述 .env.local） | 本轮I2实施 |
| admin api（remote/local） | 8101 / 8100（nohup uvicorn；env 经 nohup env 注入，不落盘） | 本轮I2实施 |
| 前端 dev | 3000（聊天）、3100（管理） | 本轮I2实施 |
| 容器栈 | `air-agent-langgraph-api`（基线 local 栈；`.env.langgraph` 已恢复原状，备份 `/tmp/env.langgraph.bak`） | 验收后恢复原部署 |
| 共享库运维记录 | `air_agent_runtime` 密码对齐 .env（ALTER ROLE）；本地测试账号 `i2e2_driver` 新建、`e2e_alice` 密码重置（24格驱动） | 本轮I2实施，验收后可删 |

秘密不进报告/命令回显/Git：证据脚本一律环境变量读取（g2.3 脚本曾硬编码隔离库口令，已改为 env 读取并确认 0 命中）。

## 6. 尚未验证 / 受阻 / 已知限制

1. **A13 六格受阻**：DeepAgents 三图（intelligent-report/deep-research/intelligent-tracing）的 wait 格真实模型终态与完整流——图内 LLM 网关 `192.168.64.1:8787` 宿主未运行（容器内 Connection refused 实测）。缺外部模型网关，不把受控/空输出冒充实配模型成功。
2. **remote 容器栈**：Apple `container-compose` 覆盖文件的 environment/volumes 合并语义不可靠（丢失 REDIS_URI / 挂载），remote 配置以 `.env.langgraph` 追加方式尝试时 Go core 对隔离库 ping 超时；remote 身份运行链路改由 G2.2（revalidate remote 分支）+ G3.2（BFF→票据→绑定→native remote 复查）进程级实证 + 基线栈 24 格（local 身份路径）覆盖。此为部署工具限制记录，不是身份适配代码缺陷。
3. **真浏览器视口**：桌面/390×844、Tab/Escape 键盘操作验证未执行（本轮无浏览器自动化授权环境）；G3.2 为真实 Next dev 进程 + 完整 HTTP 会话链路。
4. **mt-v1/rs-v1**：代码路径确认未触碰（TicketError 分支独立）+ 既有票据单测回归；未在 remote 部署下重放迁移脚本（属 I4/运维演练范围）。

## 7. 正式切换前置（未完成，单列声明）

以下不在本轮交付范围，缺少任何一项不得切换真实服务：

1. 共享 IAM 业务权限清单发布（agent:operations:read 等 10 码，见 g0-inventory §2.1；本轮仅在隔离副本受控迁移注册用于实证）；
2. 原身份历史数据同 UUID 导入证明（同名不同 UUID 冲突清单归零；本轮 dry-run 工具已交付）；
3. 维护窗口唯一写者与存量会话迁移；
4. 生产回退方案与现有用户浏览器切换；
5. 共享 IAM 的 internal client/SSO client 正式登记（本轮为隔离副本登记）。

## 8. 复核入口

- OpenSpec：`openspec validate switch-agent-to-remote-iam --strict` → valid；tasks 15/15。
- 证据：`docs/reviews/evidence/run/i2-20261016-224541/`（按 G0→G4 顺序的 md + scripts + 原始输出）。
- 复跑建议：按 scripts/ 内脚本顺序（g0 探针 → g1.3 e2e → g2.1 桥接 → g2.2 撤销屏障（62s）→ g4.2 矩阵 → g4.3 负载），凭据全部经环境变量注入。

---

**状态：首次交付，待独立验收。** 本报告不构成最终通过；独立验收按 acceptance.md 的 A01–A14 复跑，保留具体失败 nodeid 与原始证据。
