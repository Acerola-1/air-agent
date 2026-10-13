# air_agent admin API

统一身份与企业后台服务：**企业/部门/成员/角色 RBAC**、可撤销会话、运行票据、
MCP 授权绑定、行政区划关注城市、操作审计。聊天前端（`frontend/`）与本后台共用同一
PostgreSQL 身份源；Agent（langgraph-api）用运行票据 + 身份库实时核对做资源授权。

## 为什么是独立服务

不挂载进 langgraph-api、也不写在前端 Next 里：

- **爆炸半径**：后台是低频非关键路径，agent 是关键路径，两者不应共享进程生死；
- **安全边界**：后台需要独立的端口与暴露面控制；
- **生命周期**：后台要跑数据库迁移，改动频率远高于图逻辑；
- **职责**：用户域逻辑（认证、授权、行政区划）集中在一处，前端只做 UI。

数据落在**平台同一个 PostgreSQL**（线程/checkpoint/store 也在这个库），因此 agent 侧
可以用只读角色直接查询关注城市，无需跨服务调用。

## 技术栈

FastAPI · SQLAlchemy 2.x（async）· psycopg 3 · Alembic · Pydantic v2 · bcrypt

## 目录

```
app/
  config.py          配置（复用仓库根 .env）
  db.py              异步引擎与会话
  models.py          RBAC 五表 + regions + user_followed_regions + audit_logs
  schemas.py         Pydantic 模型
  security.py        bcrypt；不透明会话 token（服务端只存 SHA-256 摘要）；
                     运行票据 rt-v1（独立密钥，kid 有界轮换）；旧 HMAC 仅识别拒绝
  db.py              迁移角色(owner_engine，仅脚本/迁移) 与运行角色
                     (runtime_engine = RUNTIME_DATABASE_URI，RLS 生效) 双连接；
                     TenantContext/set_config 事务本地 GUC
  deps.py            get_identity（实时）/ require_permission / require_platform /
                     require_active_tenant / CSRF Origin / 内部端点凭据
  routers/           auth(登录/登出/me/tenants/switch-tenant/change-password/
                     runtime-ticket), tenants(企业+成员), departments, users(平台),
                     roles(平台/企业双作用域), permissions, regions, audit,
                     bindings(MCP 授权，平台管理)
  services/          identity(会话解析), tenant_context, tenant_seed(企业内置角色),
                     followed_regions(含成员作用域), permission_tree, audit
migrations/          Alembic：0001 基础 → 0002 审计列 → 0003 企业身份域+RLS →
                     0004 归档租户列（收编 DDL）
datasets/            行政区划数据与来源记录
scripts/             seed.py, bootstrap_tenant_db.py, import_regions.py,
                     migrate_identity.py(预检/apply/report), agent_e2e_fixture.py,
                     drill_recovery.sh(恢复演练)
tests/               统一会话/RLS/企业 API/迁移 共 98 项
```

## 快速开始

前置：PostgreSQL 已运行（根目录 `container-compose up -d postgres`）。

```bash
cd admin/api
uv sync --extra dev                    # 建 venv 并装依赖

.venv/bin/alembic upgrade head         # 建表
.venv/bin/python scripts/seed.py       # 权限/角色/root 账号
.venv/bin/python scripts/import_regions.py   # 导入 3347 条行政区划

.venv/bin/uvicorn app.main:app --port 8100 --reload
```

接口文档：<http://127.0.0.1:8100/docs>

默认管理员：**`root` / `root`**（由 `scripts/seed.py` 创建，可通过环境变量覆盖）。
> 这是本地开发用的初始凭据。任何对外部署前请务必改掉。

## 环境变量

`pydantic-settings` 会依次读 `.env`、`../../.env`（仓库根），因此本地无需重复配置。
单独部署时用本目录的 `.env`（见 `.env.example`）。

| 变量 | 说明 |
|---|---|
| `DATABASE_URI` | 平台共用的 PG。宿主机上是 `localhost:15432`，compose 网络内是 `postgres:5432`。**不要加 `+psycopg` 前缀**——服务内部会自动转换，而容器里的 Go core-server 只认裸 `postgresql://` |
| `AUTH_SECRET` | session token 签名密钥。**必须与 `frontend/.env.local` 完全一致**，否则前端 `middleware.ts` 验不过签发的 cookie |
| `HOST` / `PORT` | 监听地址，默认 `0.0.0.0:8100` |
| `COOKIE_SECURE` | 本地 http 下必须为 `false`；生产 https 置 `true` |
| `CORS_ORIGINS` | JSON 数组，如 `["http://localhost:3100"]` |
| `ROOT_ACCOUNT` / `ROOT_PASSWORD` / `ROOT_DISPLAY_NAME` | 种子脚本创建的初始管理员 |

## 测试

```bash
.venv/bin/python -m pytest tests/ -q          # 需要 PG + 已导入区划
./tests/check_session_compat.sh               # session token 跨语言一致性
.venv/bin/ruff check .
```

`check_session_compat.sh` 会拿 Python 实现与 `frontend/lib/session-token.ts` 的等价 JS
实现做逐字节比对。同一个 token 格式有前端 edge middleware、前端 node 侧、Python 后台
三处消费者，任何一处漂移都会让用户登录态莫名失效，所以用固定向量锁死。

## 主要接口

| 分组 | 端点 |
|---|---|
| 认证 | `POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`GET /auth/menus` |
| 我的资料 | `GET/PUT /auth/me/followed-regions` |
| 用户 | `GET/POST /users`、`GET/PATCH/DELETE /users/{id}`、`PUT /users/{id}/roles`、`PUT /users/{id}/password`、`PUT /users/{id}/followed-regions` |
| 角色 | `GET/POST /roles`、`GET/PATCH/DELETE /roles/{id}`、`PUT /roles/{id}/permissions` |
| 权限 | `GET /permissions`、`GET /permissions/tree` |
| 行政区划 | `GET /regions/children`、`GET /regions/search`、`GET /regions/{code}` |
| 审计 | `GET /audit-logs` |

## 设计要点

**授权每次现查，不信 token。** token 只承载身份（`user_id` + `account`），权限码每次
从数据库解析（`services/rbac.py`）。这样后台改权限立刻生效，不需要用户重新登录。

**`super_admin` 不做旁路。** 种子数据已显式授予它全部权限码，统一走权限码校验，
"谁能做什么"只有一个真相来源。

**关注城市最多 5 个，且不限制层级。** 省/市/区县都可选，区县是最深层。上限在服务层
事务内用行锁保证（见 `services/followed_regions.py`）。

**中文只存在于数据库。** `account` 被数据库 CHECK 约束限制为 ASCII——session token 里
用 `btoa` 编码，遇到非 Latin1 字符会直接抛异常。中文放 `display_name`。


## 统一身份协议（add-tenant-identity-access）

- cookie `air_agent_session` = `<session_id>.<opaque token>`：HttpOnly、SameSite=Lax、
  30 天；服务端只存 token 的 SHA-256 摘要。旧 HMAC cookie 一律 401（迁移工具可识别）。
- 登出/改密/账号停用/成员停用/企业停用/角色撤销 → 下一次请求即时失效（每请求实时解析）。
- 企业切换：`POST /auth/switch-tenant {tenant_id, expected_ctx_version}`；版本不符 → 409。
- 运行票据：`POST /auth/runtime-ticket`（仅 `X-Internal-Token` 内部调用；浏览器不可得），
  claims 只含标识与修订号。LangGraph 侧 custom auth 验签后再实时核对身份库。
- 授权模型：全局账号(users) 与企业成员(tenant_memberships) 分离；平台角色经 user_roles，
  企业角色经 membership_roles；`grantable_to_tenants=false` 的权限不能授予企业角色。
- RLS：租户表只对运行角色启用；GUC `air_agent.tenant_id/user_id/session_id/platform/login`
  事务本地，缺失即不可见（失败关闭）。owner_engine 只用于迁移/seed/恢复脚本。

## 部署顺序（本地开发）

```bash
# 1) PG 就绪（compose）；2) 引导运行角色（一次性，幂等）：
export TENANT_RUNTIME_DB_PASSWORD='<与 .env 一致>'
.venv/bin/python scripts/bootstrap_tenant_db.py
# 3) 迁移：
.venv/bin/alembic upgrade head
# 4) 种子（幂等；生产禁默认 root/root）：
.venv/bin/python scripts/seed.py
```

`.env` 关键项：`ADMIN_RUNTIME_DATABASE_URI`（本服务运行角色）、
`RUNTIME_TICKET_KEYS`（JSON 数组）、`INTERNAL_API_TOKEN`、
`ALLOW_DEFAULT_ROOT_BOOTSTRAP`（仅本地）。


### 隔离恢复演练（0006）

在已配置专用 `TENANT_TEST_OWNER_URI` 的环境，从 `admin/api` 执行 `.venv/bin/python scripts/drill_recovery.py --output /tmp/air-agent-identity-recovery.json`。需本地 Apple Container 的 `air-agent-postgres` 和创建测试库权限。脚本仅接受本机测试库前缀，随机创建全新演练库，验证真实 dump/restore、原密码、已有新写保全与前向修复/RLS，随后仅删除自己创建的库；不 downgrade 业务库。备份临时文件权限600，秘密只从环境读取。

归档签名 `ARCHIVE_SCOPE_KEYS` 必须在聊天BFF与LangGraph服务端一致配置；本地容器从 gitignored `.env.langgraph` 读取。签名不是会话授权替代品，归档路由还实时验证运行票据、已确认归属和当前身份；登出后旧scope应返回401/403。
