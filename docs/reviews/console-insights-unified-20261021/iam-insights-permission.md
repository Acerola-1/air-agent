# Change7 IAM 权限目录与授权验收

时间：2026-10-21。只用本任务自建隔离 IAM（`127.0.0.1:25441/25445`），未连接正式 `19132`，未修改正式数据或 root。两仓均未 commit/push。

## 1. 目录迁移

- 隔离 IAM 实际 head 为 V27（`agent feedback manage`），不是交接里假设的固定值。
- IAM 仓库新增 `server/src/main/resources/db/migration/V28__agent_insights_manage.sql`：只登记权限码 `agent:insights:manage`，不 GRANT 给任何企业、角色或 root。
- 重新构建 IAM jar 后正常启动，Flyway 在隔离库执行到 `28 | agent insights manage`，权限行存在。

## 2. 发现并修复的真实 RLS 问题

正常管理 API（IAM `/web-api/admin/roles/{id}/permissions`）给企业角色写权限时，`role_permissions` 的 INSERT 返回 PostgreSQL `42501`，接口表现为 403「无权执行该操作」。

根因：`RoleAdminServiceImpl.loadRoleRow` 对同时持平台写权限的操作者按**平台上下文**（`app.tenant_id` 为空）读取角色；而 `role_permissions` 的 WITH CHECK 只允许「企业角色 + 当前企业」写入，平台标记不能替代企业上下文。

修复：识别出目标角色是企业角色后，在同一个事务内把上下文切回该企业，再执行后续写入。改动只涉及这一处上下文选择，不修改鉴权协议，不放开 RLS 策略，不授予 root。

修复后，正常管理 API 返回 200，角色权限码为 8 项并包含 `agent:insights:manage`。

## 3. 授权/撤权序列

| 步骤 | 操作 | 结果 | 证据 |
|---|---|---|---|
| 未授权拒绝 | 有效会话但角色无 insights 码，创建立即作业/重试 | 403 `permission_denied`，Embedding 调用增量为 0 | `change7-live/race-revoke-before-outbound.json` |
| 正常 API 授予 | 平台管理员经 IAM Web 管理 API 全量替换企业角色权限 | 200，返回 8 个码 | 本文第 2 节；隔离库角色行 |
| 旧会话失效 | 授予/撤权推进授权代次后，旧管理会话 | `/auth/me` 401；浏览器刷新被重定向到 `/login` | `change7-live/browser-flow.md`；浏览器实测 |
| 合法成功 | 重新登录后创建立即作业 | 202，worker 正常消费 | `change7-live/v3-job.json`、`overview.json` |
| 运行中撤权 | 作业外呼期间撤掉 insights 码 | 作业 `paused_authorization`，已处理 0，后续外呼停止 | `change7-live/race-renew-paused-authorization.json` |
| 恢复授权后续期 | 重新授予并经 `renew` 换新委托 | 作业 pending→completed，处理 3 个来源 | `change7-live/race-renew-completed.json` |
| 恢复后页面 | 浏览器重新 SSO 登录 | 洞察页恢复，榜单/改名仍在 | `change7-live/browser-flow.md` |

## 4. 边界与未执行

- 未使用 owner 直接插入角色权限来冒充管理流程；上述授予/撤权均走 IAM 正常管理 API。
- 未给 root 或平台内置角色授予该业务码，未改正式 IAM。
- IAM Java 测试套件未运行：其集成测试默认指向 `iam_test` / 19132，本轮禁止连接正式端口；改用隔离实例的真实 API 序列验证，并保留 `mvn -DskipTests package` 构建结果。
- 隔离 IAM 内部端点限流实测为 600/分钟；更快的发送器会把冒烟打成 503。已把**隔离实例**限额提高并给冒烟加可配置间隔，正式配置未改。
