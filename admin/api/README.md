# Agent 业务 API 与 IAM 消费门面

当前身份唯一权威为独立 Java IAM（19100，PG19132/iam_identity）；本服务运行于8100，通过HTTP内省核验身份，并管理15432/air_agent_business中的台账、归属、偏好、MCP绑定和观测数据。

旧public.users/tenants/roles等九张身份表已从正式业务库移除。`iam_reference_*`只是同UUID技术引用，不提供授权。旧身份管理路由返回409，账号、企业、成员与角色均在IAM19101维护；不得在业务库重新seed本地root。

## 启动与配置

回到Agent仓库根目录，使用唯一协调启动器：

```bash
.venv/bin/python deployment/run_separated_local.py start
.venv/bin/python deployment/run_separated_local.py status
```

| 配置 | 当前职责 |
|---|---|
| DATABASE_URI | 纯业务库owner连接，仅用于获准的迁移/维护 |
| ADMIN_RUNTIME_DATABASE_URI、RUNTIME_DATABASE_URI | 宿主同一个非owner业务连接；RLS和事务GUC生效 |
| AGENT_IDENTITY_BACKEND | 正式环境固定remote |
| IAM_BASE_URL、IAM_INTERNAL_CLIENT_* | IAM地址和独立登记的消费者凭据 |
| RUNTIME_TICKET_KEYS、INTERNAL_API_TOKEN | 运行票据与服务端BFF凭据，不能发给浏览器 |
| LEDGER_RECONCILE_KEYS | 独立只读校准密钥，不复用用户票据密钥 |

本机真实配置在Git忽略的根`.env`中；容器使用单独生成的原生配置，不能照抄宿主localhost地址。完整正式存储清单见[正式环境](../../docs/CURRENT-RUNTIME.md)。

## 主要接口与边界

- `/auth/login`、`/auth/me`、企业切换及SSO是IAM门面；应用cookie为不透明HttpOnly句柄，IAM令牌仅在服务端保存。
- `/auth/runtime-ticket`仅接受内部调用凭据和当前人类会话，票据与服务端执行引用绑定；原生运行时再次实时核验IAM。
- `/agent/*`管理业务请求、回答、反馈和观测；跨主体、跨企业、陈旧上下文及删除对象失败关闭。
- `/auth/menus`使用静态权限目录与IAM当前权限交集，不查询已移除的旧身份表。
- 偏好最多5项，业务侧锁和复合FK保证并发上限与归属，不将引用表当作身份权威。

## 开发与测试

安装依赖、运行格式检查可复用根虚拟环境。数据库/RLS回归必须另建空临时库、非owner测试角色及专用环境变量，绝不能直接用现用`.env`执行测试造数、TRUNCATE或seed；测试完成清理自建资源。旧常驻评测库已全部退役。

历史local身份实现、旧迁移与恢复脚本保留为显式隔离回归材料，不属于正式启动链，也不是重建已删原库的指引。独立评审报告与模型分数按原轮次保留。
