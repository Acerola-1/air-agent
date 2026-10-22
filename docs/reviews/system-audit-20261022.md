# 2026-10-22 正式环境全面复盘

范围：Agent、IAM、RAG 三仓的本机正式运行态，以及 Agent 运营后台的 root 权限与迁移状态。

## 结论

系统可以启动并完成主要运营查看；此前报告的两类 P0（Qdrant/Neo4j 局域网暴露、无持久卷）已于 2026-10-22 处理完毕。剩余 P1/P2 项如下。

## 已修复

- 业务库 Alembic 从 `0023_knowledge_citations` 升级到 `0029_insight_retention`，补齐运营读模型、反馈处理与需求洞察表。
- root 在 4 个活跃企业均获得 `root_all_access` 企业角色，每个角色装配 45 个企业权限；平台侧已有 29 个权限。总计每个企业 74 个权限码，已通过 IAM API 读回验证。
- Agent 后台删除“概览”“统一身份管理”“业务管理”菜单；`/` 重定向到 `/operations`。
- IAM 管理台地址改为 `IAM_ADMIN_URL=http://127.0.0.1:19101`，避免跳到 19100 API 的未构建 SPA。
- 业务库异常与 IAM 异常分开返回，不再把数据库迁移问题报成“身份依赖暂不可用”。
- 私有桥与 `.env.langgraph` 的当前网段地址已校正。
- 启动器新增 admin API Alembic head 预检：业务库不是代码 head 时拒绝启动，并提示执行 `alembic upgrade head`。
- 三个 LightRAG 实例已恢复：新增 `wa_net_proxy.py`，在 `192.168.66.1` 上把 8787/5432/6333/7687 中继到宿主回环的模型网关、PG、Qdrant、Neo4j；实例通过健康检查，并在既有数据上完成 hybrid 检索（air 实例返回 17 entities/142 relations/13 chunks，水、沿海实例分别返回 28/143/13 与 63/68/8）。
- Qdrant 与 Neo4j 已迁移到 Apple Container 持久卷 `lightrag_qdrant_data`（`/qdrant/storage`）和 `lightrag_neo4j_data`（`/data`）。删除并重建两个容器后，Qdrant 的 11,431/129,706/227,434 个点与 Neo4j 的 130,274 个节点完全一致。迁移备份保存在 RAG 仓 `.local/backups/20261022-data-volumes/`。
- Qdrant/Neo4j 端口已从 `0.0.0.0` 收敛到 `127.0.0.1`，局域网无法再直接访问；LightRAG 改走 `192.168.66.1` 内部中继。Qdrant 仍未配置 API key，但已不对局域网开放。

## 未修复（P1/P2）

### P1：启动与健康检查

- 启动器现在会检查 admin API 业务库 head；IAM Flyway 当前 28/28，RAG `env_rag.schema_migrations` 9/9，均与代码一致。
- `agent-admin-api /health`、`rag-api /health` 仍只检查进程，不检查数据库、IAM、LightRAG 依赖。
- Apple Container 网段变化时，`.env.langgraph` 的 IAM/model/Langfuse/知识库地址不会自动重写；本次已手工校正，未来仍可能重现。

### P2：数据与仓库卫生

- IAM 有 220 个 suspended 测试企业、341 个 active 用户、256 条 active 成员关系、76 个 active 角色；大量为历史测试数据。
- `/business/*` 旧页面仍编译在 Agent 后台构建产物中，菜单已隐藏，但直接 URL 仍可访问。
- `lightrag-water`/`lightrag-coastal` 使用 `latest` 镜像标签，Qdrant 使用 `latest`，Neo4j 使用 `5`；与 `lightrag-air` 的 digest 固定策略不一致。
- root 全权限通过 IAM 数据库直接装配，未经过 IAM 管理 API 的审计路径；若要求完整审计链，应在 IAM 管理台重新提交一次角色权限替换。

## 当前验证

- 7 个应用/网关进程均健康：IAM、IAM Web、Agent Admin API、Agent 聊天、Agent 后台、RAG API、LangGraph。
- root 在 alpha-env、beta-env、gamma-env、root-acceptance 四个活跃企业均为 74 个权限码。
- 运营总览返回真实数据；各运营页面可打开。
- 业务库 `alembic_version=0029_insight_retention`；IAM Flyway 最新 28；RAG schema migrations 9/9。
- LightRAG 三个实例均 running，`wa_instances.sh health` 全部通过；既有数据计数未变：PG 444 文档/11,431 chunks，Qdrant 11,431 chunks/129,706 entities/227,434 relationships，Neo4j 130,274 节点；`route=lightrag&mode=hybrid` 经 8099 网关返回 200，air/water 两个库 `status=retrieved`、346 条证据。
- Qdrant/Neo4j 已挂载持久卷；删除并重建容器后数据计数与迁移前一致。端口监听仅为 `127.0.0.1:6333/7474/7687`，LAN 地址不可达。`wa_net_proxy` 在 `192.168.66.1` 提供 LightRAG 内部访问。

## 2026-10-22 追问修复

- **系统提示词未生效的根因在模型网关**：`wb2api` 上游会丢弃 `system` / `developer` 角色；应用侧 prompt 组装本身正常。曾尝试在 `deployment/local_host_bridge.py` 做 system→user 兼容层，但这属于供应商特定 workaround，不符合正常 LangGraph/OpenAI 消息语义，已删除。当前 `hy3` 网关仍会忽略 system；换用遵守协议的服务商后可恢复标准 `SystemMessage` 行为。
- **身份问题误入数据查询**：`你好，请问你是谁？`、`你能做什么工作？` 之前不匹配整句问候白名单，落入 `data_query` 后被向量路由器误配到 `ranking-assessment`。已新增 `_SELF_IDENTITY_PATTERN`，两类问题现在走 `chitchat`，且“你好，帮我查保定今天空气质量”仍走 `data_query`。
- **技能选择 409 的根因**：`AGENT_CAPABILITY_SELECTION_ENABLED` 默认 false，任何显式选择都会被 admin API 以 409「能力选择功能已停用」拒绝；BFF 丢掉了 `code`，前端误显示为 `submission_conflict`。
- **基础问答改为全自动**：前端删除技能/MCP 选择组件与提交字段；BFF 对 `basic-qa` 丢弃任何客户端选择；admin API 对该图强制归为 `auto`。Skill 仍只由 `resolve_skill` 的向量语义路由器选择 top1，已移除 `load_skill` 工具与候选摘要，模型不能自行换 Skill。
- 前端合同测试 70/70 通过，`next build` 通过；后端 bridge/节点定向测试 23/23 通过。
