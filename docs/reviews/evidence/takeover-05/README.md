# 接管收尾验证原始证据清单 (takeover-05)

生成时间：2026-10-15（Asia/Shanghai）  
分支：`codex/change-04-skill-tool`  
基准提交：`8620cf78b22cb7234fe650fa5c9f032359e0d70d`  
对应验收报告：`docs/reviews/skill-tool-selection-takeover-acceptance.md`

## 1. 证据文件说明

| 文件名 | 类型 | 对应项 | 说明与关键结论 |
|---|---|---|---|
| `production-sdk-graphs.json` | JSON | G01 / A12 | 三张生产图 SDK（deep-research, intelligent-report, intelligent-tracing）使用受控模型直接 ainvoke 真实执行结果，全部成功返回 `controlled success`。 |
| `migration-verification.json` | JSON | G02 / A23 | 临时库从 0001 -> 0012o_delivery -> head (0013s) 升级全过程。`selection_source` 列完整存在，表 19 字段齐全，FORCE RLS 生效。 |
| `process-boundary.json` | JSON | G03 / A01 | 真实独立 HTTP 管理服务与运行时 custom app 进程联调。目录 200 返回 5 工具，合法 MCP 选择 200 成功，模型外呼计数为 0。 |
| `accepted-vs-server.json` | JSON | G04 / A21 | 编译运行实际 TypeScript 客户端。服务端回读与客户端 `selection_id` 与 `selection_digest` 完全匹配；S2 草稿修改期间当前运行锁定显示 S1。 |
| `new-regressions.json` | JSON | G05, G06 / A03, A10 | 单射转义 `svc_a -> svc_u_a` 与 `svc--a -> svc_h__h_a`；别名代理工具在生产 ToolNode 中被准确识别为 MCP 工具；显式关闭 fixture 开关后即便连接串含 test 也阻断。 |
| `live-replay.json` | JSON | G07 / A07, A14 | 同键恢复请求省略选择时，首次受理 200，同键重放 200（`reused: true`，有效选择与摘要一致）；修改选择严格 409。伪造目录版本 409。 |
| `overhead-benchmark.json` | JSON | G08 / A24 | 5 层全链路基准测试数据（35 样本 × 2 轮 + 5 预热）：本地 helper、ASGI+PG 受理、native 保护、受控整图运行；含 50 笔请求故障注入实测统计（100% 捕获，0% 漏判）。 |
| `core_28_tests.txt` | 日志 | 回归基线 | 28/28 passed。覆盖六图导入与能力目录约束。 |
| `admin_tests.txt` | 日志 | 回归基线 | 44 passed, 1 failed（唯一失败为既有基线 `test_browser_events_all_reserved_types_rejected`）。 |
| `frontend_tests.txt` | 日志 | 回归基线 | 48 passed, 0 failed。包含新增的 React 探测与权威回读校验。 |
| `root_unit_tests.txt` | 日志 | 回归基线 | 361 passed, 75 skipped, 6 failed（6 失败为既有知识库路由与 checkpointer 基线）。 |
| `frontend_build.txt` | 日志 | 构建基线 | Next.js 生产客户端构建日志，473ms 干净编译通过，0 错误。 |

## 2. 测量环境与资源声明

- 数据库：专用 Apple Container 容器 `aa-sel05-pg`（宿主端口 15434，库名 `air_agent_selection05`）。
- 缓存：专用 Apple Container 容器 `aa-sel05-redis`（宿主端口 16381）。
- 生产与共享栈：生产端口 15432 与 16379 严禁触碰，本轮未发生任何交互。
- 权限与密钥：未操作任何生产 IAM 账号或知识库卷，无密码或私有密钥进入日志或证据。
- 未完成标记：5.4（物理无头环境无 GUI/Playwright）与 7.1（无外网商用 LLM 流式密钥）在 `tasks.md` 中如实保留 `[ ]` 未勾选状态。
