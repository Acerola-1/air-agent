# 多企业统一身份与访问隔离

## Why

聊天前端仍使用本地 JSON 账号，而既有后台使用 PostgreSQL 用户和 RBAC；cookie 格式兼容但身份源未统一，Agent/MCP 也没有完整企业授权链。用户确认第一版支持多企业、统一业务平台分企业授权，需要先建立可验证的身份和资源隔离基础，后续统计才能可信归因。

## What Changes

- 复用 `admin/api` 与 `admin/web`，添加企业、部门、成员关系和成员级角色；将 PG 用户域作为唯一登录身份源。
- 采用服务端可撤销会话，加入企业上下文切换、成员/企业停用与角色收敛。**BREAKING**：切换后旧无撤销能力的 HMAC cookie 失效，用户重新登录；聊天端停止写入 JSON 用户库与公开自助注册。
- 区分全局用户、租户成员和平台管理权限，租户管理员只能管理本企业成员与可授予角色，不能跨企业修改全局账号或授予平台权限。
- 通过 LangGraph 原生 Auth 和可信运行上下文授权线程、运行、状态、历史和辅助资源；自有租户表采用 RLS、约束及连接池事务隔离。**BREAKING**：企业模式不接受 noop、客户端自报身份和无可信租户的历史资源。
- 扩展归档签名、存储和回读为租户/用户/线程/运行作用域，保持已有完整数据、过期与删除语义。
- 为统一 MCP 平台建立企业/成员到外部授权主体的绑定与凭据引用，在 discovery、调用、重试和缓存中隔离身份。六个图均落实执行前确定性授权；协议未核验或授权缺失时拒绝企业 MCP 调用。
- 提供只读预检、显式归属清单、幂等迁移、冲突隔离和恢复说明；不按同名账号合并身份或把未知数据默认分配到企业。
- 在现有两个前端增加企业上下文和最小企业/成员管理流程；本次不开发统计看板、热门问题或反馈采集。

## Capabilities

### New Capabilities

- `tenant-membership-access`: 企业、部门、成员与租户角色授权，平台管理边界及审计。
- `unified-revocable-session`: 统一 PG 身份、可撤销会话、企业切换与可信运行身份。
- `tenant-resource-isolation`: 六图、线程/运行、平台辅助资源与自有归档的端到端隔离。
- `tenant-mcp-authorization`: 统一业务平台的企业授权映射、凭据引用、确定性工具许可和并发隔离。
- `tenant-identity-migration`: 两套历史用户与旧资源的显式映射、预检、幂等迁移及恢复。

### Modified Capabilities

- `agent-runtime-performance`: 将 MCP 初始化去重、缓存与懒可用边界从全局服务改为授权身份作用域，保留并发保护与有界降级。

## Impact

影响 `admin/api/app/{models,security,deps,db,routers,services}`、Alembic 和种子/迁移脚本，`admin/web` 与聊天 `frontend` 的认证、企业上下文及代理，`src/common` 身份、MCP、工具、归档模块和六图接入点。新增跨租户数据库/API/浏览器/MCP 并发与撤销测试；保持消息流、预算、完整数据计算和现有业务结果契约。

按实际需求更新各子项目自己的 `pyproject.toml/uv.lock` 或前端依赖锁，不默认全量升级。必要的 LangGraph Auth 配置同时落在 `langgraph.json`、Dockerfile、compose 与启动就绪探针，保留 Apple Container/现有 PG 卷。不修改外部 MCP 服务实现，不向 Langfuse 发送凭据；新建服务不是本次范围。

详细决策、接口和外部验收条件见 `design.md`；总体路线见 `docs/enterprise-agent-roadmap.md`。外部授权协议和当前镜像 Auth 支持是具体联调检查项，不能用推测替代通过证据。
