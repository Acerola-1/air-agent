# 通用 AI 实施交接

## 执行目标与读取顺序

在 `/Users/acerola/Dev/Python/air_agent` 实施 `add-tenant-identity-access`，进入项目已有 OpenSpec apply 工作流。总体路线图是背景，实施范围以本 change 的规格、设计和任务为准；不要顺带实现其他 change。

先读根与 `frontend/AGENTS.md`、`docs/enterprise-agent-roadmap.md`、`docs/reviews/2026-10-12-enterprise-planning-baseline.md`、本 change 的 proposal/design/全部 specs/tasks，以及 `docs/flash-blind-evaluation.md`。运行 `openspec status --change add-tenant-identity-access --json` 与 `openspec instructions apply --change add-tenant-identity-access --json`，读取其真实路径与任务。涉及前端实现时读本项目要求的当前 Next.js 文档与适用 UI 技能。

## 实现接缝

| 领域 | 当前入口 | 重点 |
|---|---|---|
| 既有身份/RBAC | `admin/api/app/{models,security,deps,db,schemas}.py`、routers/services | 复用 PG 身份；成员角色/平台职责分离 |
| 迁移与 bootstrap | `admin/api/migrations`、scripts、pyproject/uv.lock | 在 0002 后追加；显式归属与非 owner RLS |
| 后台前端 | `admin/web/lib/{api,session,types}`、app/dashboard、user-dialogs | 保留壳；企业上下文与成员管理 |
| 聊天认证/代理 | `frontend/lib/{auth,session-token,session-client,user-store}`、middleware、api/auth、api catchall | 退出 JSON 线上源；代理实时确认身份 |
| 会话切换/取消 | `frontend/lib/{chat-store,langgraph-api}` | 切换企业要处理当前 run、清理缓存、拒绝旧上下文 |
| Agent 认证 | `langgraph.json`、Dockerfile、compose、run-local、新增 common 身份接入 | 原生 Auth 真实生效，退出企业 noop |
| 工具执行 | `common/business_graph/{nodes,tool_wrappers,builder}`、runtime_tools、mcp_client、后三个图 | 实际调用拦截；六图授权，不只提示词 |
| 资料 | `common/{artifact_scope,artifact_http}`、storage/artifact_db、computation | 作用域签名与 DB 条件加 tenant；完整数据保持 |

不要直接 import 独立后台的 `app` 包到 Agent；共享无框架身份契约或通过明确服务/数据库访问接口接入。不要把整个后台依赖树装进根项目，也不要顺便全量升级锁文件。

## 验证方式

根据已有 venv/锁文件使用 `.venv/bin/python`；根 Makefile 可通过将仓库 `.venv/bin` 加入 PATH 执行。后台 API 在其目录使用自己的 uv 环境与测试。先 targeted，再完整检查；真实 PostgreSQL/RLS 的结果不能由 SQLite 或 Python mock 替代。

前端已有契约命令 `npm --prefix frontend run test:contracts`，两个前端分别运行自身类型与构建脚本；当前聊天生产构建使用 `npm --prefix frontend run build -- --webpack`。实际浏览器验证企业选择/切换、旧标签页、成员管理和聊天恢复，截图与日志脱敏。

根单元基线：196 passed、26 skipped、10 failed。失败为 checkpoint 连接关闭 1、旧 knowledge route 缓存 4、旧 datacenter MCP 接口 5。涉及 MCP 后需迁移对应失效测试的契约，不能删除测试来减少失败。保留关联治理测试和前端契约的行为，不机械追求固定测试数量。

Apple Container 使用项目 `container-compose`；不使用 Docker Desktop，不重建/删除现有 pgdata 或无关 Lightrag/Langfuse 卷。可以创建命名明确、可验证隔离的测试数据库/实例。所有真实身份/外部凭据只通过受保护配置传入，不输出到日志、报告、提示词或 Git。

## 自主推进与外部边界

此交接明确授权按已写计划实施和修复，不需逐任务请求确认。工程细节自行选择并保持契约；需要改变产品范围或身份/安全语义时先记录偏差。缺外部协议、A/B 凭据、业务归属清单或必要权限时完成不依赖它们的部分，保留受影响任务未完成并一次说明缺失材料，不虚构安全证明。

真实 MCP 契约的首个核验任务是确认认证机制及主体字段，而不是随意选择 Bearer header 或把 userId 当认证。纯 fixture 通过不能使 7.6 完成。默认端口探针失败时先核查实际配置/进程，不声称整个服务不存在。

## 交付与盲评

逐项更新 tasks，仅验证通过才勾选。给出 diff、可复跑命令、场景到证据映射、实测范围、遗留阻塞和迁移影响；最终报告写入 `docs/reviews/tenant-identity-access-acceptance.md`。报告不写模型名或供应商，实施轮次只用用户提供的匿名编号。无需猜测其他实现来自哪种模型。

完成表示“本 change 实施完毕，待独立验收”；不可替 Codex 声称已独立验收。保留可审查 diff，不自动提交/推送/合并/归档，不自动做下一 change。由用户返回此实现进行盲评，再决定主要实施模型与下一 change。

## 目标模式用法

提示词见 `docs/handoffs/tenant-identity-goal.md`。以执行工具实际支持的 `/goal` 或等价“目标”命令启动，正文可直接用于 ZCode、Antigravity 或其他支持持续目标执行的工具。前缀受客户端实际语法约束，不需要重写实施要求。

Antigravity 命令参考（核实于 2026-10-12）：https://www.antigravity.google/docs/slash-commands 。ZCode 的具体前缀以用户实际客户端为准；本交接不声称已验证其版本或语法。
