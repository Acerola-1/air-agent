# 匿名运行05交接

状态：规划就绪，产品未实施。产品基线2d6f7c84d7fab42f38cc91cbb81f75fdeaa0e0c9；IAM/清理协调提交06b411f96e9598c2938c87908ae67114fde63d46。请从**包含本交接包的dev提交**创建独立工作区，记录实际HEAD，而非只checkout产品基线丢掉计划。目标仅add-skill-tool-selection；3/5已归档，原匿名03/04分数不改。

## 阅读顺序与范围

AGENTS.md、frontend/AGENTS.md、DESIGN.md、UX-CONTRACT.md → 本change proposal/specs/design/contract-extension/tasks/acceptance → 3/5接管报告与现有身份/MCP契约 → docs/flash-blind-evaluation.md。OpenSpec正文用中文。不要把未实现计划或作者测试当验收通过。

可改区域：frontend聊天输入/选择状态/BFF、src/common的Skill路由/加载、MCP scoped factory/授权、business_graph/SDK三图、事件台账/观测/原生Auth的选择接线；admin的受理schema/业务服务/新增模型迁移；共享事件登记和三validator/生成副本；相关测试、配置示例与文档。共享文件由本change统一负责，禁止让两个对话编辑同一checkout。

身份会话/实时撤销/出站业务身份为稳定依赖，只增加选择校验与可替换依赖，不重写身份域。/Users/acerola/Dev/Java/air_agent_iam和/Users/acerola/Dev/Python/RAG/env-rag-final不在修改范围。不给知识库硬编码权限、不实施IAM切换，不顺带做Change6后台。

## 已核实的接线入口

- src/common/skill_router.py、skill_discovery.py：元信息/自动候选；business_graph/builder.py：ToolNode编译时工具集合，需动态约束。
- src/common/mcp_scoped.py：tools_for按身份发现、call实时复核绑定；部分名称缓存存在同名服务风险。
- deep_research/intelligent_report/intelligent_tracing/graph.py：SDK工具、/skills后端和子任务，不能只限制顶层schema。
- src/common/artifact_http.py::_authenticate_custom：custom app鉴权参考；原生catalog不会自动继承LangGraph平台Auth。
- src/common/identity/langgraph_auth.py：create_run载荷包含metadata和kwargs.config，还兼容顶层config，所有副本应一致。
- admin/api/app/schemas_ledger.py及services/ledger.py：受理/claim和v1摘要；src/common/event_ledger：运行事实、删除/outbox。
- src/common/observability/skill_adapter.py：各阶段来源与loaded→executed关联；不能覆盖manual来源。
- frontend/app/api/[..._path]/route.ts、lib/langgraph-api.ts、lib/chat-store.ts与ChatPanel；按实际路径再定位组件。
- docs/contracts/agent-events-v1、src/admin/TS validator与生成副本。基线迁移head=0012o_delivery；必须在真实环境复核当前head，无并发已发布迁移才能追加，不重写历史。

## 独立环境

本项目使用Apple container+container-compose，非Docker Desktop。此前aa-feedback03/aa-observe04/aa-takeover35及probe测试容器已删除，不要引用旧端口或以旧服务仍在为前提。正式Agent、知识库与共享Langfuse数据保留，禁止全局prune或清共享卷。

优先建立独立worktree/checkout与codex/前缀分支；目标原工作区不接受另一Agent并行写入。建议admin8104、frontend3104、native2032，先检查占用，冲突时选新端口并记录。新建自有PG/Redis，名称aa-selection05-<短随机后缀>，明确所有权，容器仅回环发布；使用独立数据库/角色/Redis命名空间。不得复用正式库后清表或重置角色。仅清理自己新建资源；需要备份的本地dump不入Git。

依赖遵循根与admin各自锁文件；根Python环境与admin环境依赖不同，先按现有uv工作流安装、检查，再决定是否需要组合临时环境，不能以某个venv缺包为由修改锁或删除测试。既有运行核实版本为Python3.13、langgraph-api0.15.1、OpenTelemetry1.42.1、LangfuseSDK4.15.6；这是交接环境观察，不要求无理由更新依赖。若旧说明与实际锁冲突，以当前锁与已验收协议为准，不随意改版本。

真实模型/网关采用现有私有配置，不切模型冒充成功。环境变量值仅本地注入，报告只列变量名。未采集Token不估算。仅回环、失败关闭、立即撤销与租户隔离不可降低。测试域名暂未定不影响本项既有BFF同源实现。

## 完成与报告

先完成Gate0，再按tasks实施，边做边测。交付必须包含A01–A24证据、六图矩阵、实际MCP/SDK边界、数据库迁移/RLS、UI和性能。实际使用原模型的路径失败时保留错误和未完成格子，不能将待验证改成完成。详细回归基线见acceptance；全仓9历史spec格式失败不在本项修复范围。

最终留工作区diff待独立盲评，不commit/push/合并/归档。报告实际起点/结束、耗时来源、外部等待、工具计量可得性和未完成项；用户实测为正式时长。不要猜模型名、预填评分或把协调者后续补修计入自己的完成。遇到阻塞继续完成无依赖部分并清楚汇报，不用重复问已授权的实现步骤。
