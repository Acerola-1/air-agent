# 共享 IAM 服务抽取评估

## Why

Agent 的身份与业务存储、事务及页面已耦合，知识库另有本地身份注册表；继续分别扩展将产生两套用户和撤销语义。需要以独立 IAM 统一用户建档、企业组织与权限，通过 HTTP 契约服务两个 Python 消费方，同时保持即时撤销和租户隔离。

## What Changes

- 本轮交付**待用户评审的方案**：现状审计、完整身份 HTTP 契约、分阶段迁移/回退、风险和验收计划；不创建 Java 工程、不改消费者、不拆库。
- 目标采用 Spring Boot 4 + JDK 21 + Sa-Token 有状态不透明令牌，显式接 Redis；Python 不解析 Sa-Token 的内部格式，不直连 IAM 库。
- IAM 接管账号、企业、部门、成员、角色权限、会话与身份审计及相应后台页面。业务偏好、线程、台账、反馈、观测、价格和 MCP 连接授权配置继续由 Agent 拥有。
- **BREAKING（未来切换阶段）**：身份 PG join 改为经认证的 HTTP introspection；跨身份库外键/行锁改为业务侧引用约束/锁，旧 `/auth/*` 由兼容门面过渡，不保留双身份源。
- 保留四修订号、企业切换 CAS、每次新受保护操作的实时核验、503失败关闭、票据受众/轮换与窄服务主体；服务账号令牌作为独立主体预留。
- 一并冻结通用资源目录与 entitlement 契约，知识库权限采用 `knowledge:lib:<library_code>:read`；资源开通实施独立紧邻包，不强塞到第一次 Java 身份迁移。
- 调整原路线图：先收尾3/5，再评审IAM契约；Change4提示词/派发暂缓，后续能力与后台按新身份适配边界推进。

## Capabilities

### New Capabilities

- `shared-iam-http-contract`：与实现无关的登录、实时身份、切换、执行复查、续期、服务账号和内部票据协议。
- `iam-domain-transition`：唯一身份权威、管理界面归属、业务隔离与分阶段迁移/回退。
- `tenant-resource-entitlements`：通用资源目录、企业开通与可授权限、回收后配置保留及知识库映射契约。

### Modified Capabilities

无主规格直接改写。已阅读主规格 `agent-runtime-performance`；本次只继承其无导入期外呼等承诺。身份相关规格目前在未归档的 `add-tenant-identity-access`，本方案继承其可撤销会话/成员/资源/MCP/迁移场景，新增**跨服务协议与抽取过渡**，不复制一份替代身份规格。后续归档/同步须先解决在飞依赖，不能用本提案覆盖旧验收缺口。

## Impact

- Agent：`admin/api/app/{models,security,deps,db}.py`、身份/管理路由、迁移和种子；`src/common/identity/` 与模型、工具、归档、台账、观测等17个引用文件；`frontend/lib/admin-api.ts`、`admin/web/` 的身份页面和会话壳。
- 知识库：`/Users/acerola/Dev/Python/RAG/env-rag-final/src/env_rag_final/access/` 的本地注册表/授权上下文/逐边界复查；只在该仓库后续实现 remote 适配，本地模式不在此改动。
- 评审附件：[现状核实](source-audit.md)、[设计与取舍](design.md)、[完整HTTP契约](http-contract.md)、[验收矩阵](acceptance.md)、[阶段任务](tasks.md)。IAM新目录已确认为`/Users/acerola/Dev/Java/air_agent_iam`；部署域名尚未指定；不因此放宽回环绑定或默认开启跨站凭证。
- 3/5接管基线：admin183通过/3跳过、根362通过/75跳过/5既有失败、前端41通过；不能沿用旧“98项”计数。Change3剪贴板实机抽查、真实企业MCP及生产负载门禁继续保留。
