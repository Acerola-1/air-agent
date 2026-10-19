# Agent 业务授权独立验收与清理报告（2026-10-19）

Change：`simplify-agent-business-authorization`。本报告只记录本轮实际执行与原始证据；
不以任务勾选、GraphLoad 或 HTTP 200 代替真实模型/MCP 运行。

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| 业务授权模型 | 已完成：稳定业务目录 + IAM 精确权限求交；平台管理员无业务码同样 403 |
| root 正常登录/切企业 | 已完成：正式 IAM 4 个 active 企业全部读回 3 项业务 + 3 项运营权限 |
| root 真实运行矩阵 | 12/12 请求 200；9 项原始回答有效，3 项 data-analysis 原始回答为可读失败；3 项重试全部有效 |
| root Agent 数据查看 | 运营明细、指标、受控原文均 200 |
| 撤权反例 | 撤掉智能溯源后 403，基础问答仍 200；恢复后正对照 200 |
| 运行中撤权 | 流已开始后撤权：出现“授权状态已变化/本次运行已停止”，attempt=failed，确认回答=0 |
| 旧表清理 | `mcp_identity_bindings` 已删除（150 行）；`to_regclass` 为空 |
| IAM 旧授权清理 | `enterprise:mcp`、`mcp:binding:read/manage` 均不存在，角色边/服务账号边均为 0 |
| 旧接口/旧码 | `/mcp-bindings` 404；活跃代码残留仅为说明文字与“表不存在”断言 |
| 无备份 | 本次未生成新的 dump/快照/备份表/影子库/配置 `.backup`；数据库备份对象为空 |
| 截止状态 | 正式库 `alembic_version=0022_retire_mcp_bindings`；所有 Agent 服务健康 |

## 2. root 权限与数据查看

- root UUID：`dd5ca3c0-a454-466e-b2f7-a7fc5191867e`。
- 正常登录 IAM 后读回 4 个 active 企业：`alpha-env`、`beta-env`、`gamma-env`、`root-acceptance`。
- 每企业精确权限：`agent:business:basic-qa:use`、`agent:business:data-analysis:use`、
  `agent:business:intelligent-tracing:use`、`agent:operations:read`、`agent:metrics:read`、
  `agent:content:read`。未使用用户名特判、未重置口令、未创建替身。
- 证据：IAM `docs/reviews/evidence/run/agent-business-20261019/formal-root-readback.json`；
  Agent `root-agent-permissions.txt`。
- root 在 `alpha-env` 的目录：basic-qa 9 Skills/21 工具/ipp available；data-analysis 24 Skills
  （重载期间曾显示 ipp temporarily_unavailable，业务仍为 200）；无业务身份
  `platform_boot` 目录返回 403 `business_not_permitted`。
- 运营明细 `/agent/runs`、指标 `/agent/metrics/summary`、受控原文
  `/agent/requests/{id}/content` 均 200；证据 `root-data-view.txt`。

## 3. 真实运行矩阵

正式 root、正式三图、正式服务；请求与回答摘要见
`root-matrix.jsonl`、`root-matrix-retry.jsonl`、`root-matrix-summary.json`。

| 业务 | BFF stream | BFF wait | native stream | native wait |
|---|---|---|---|---|
| basic-qa | 200 / 78.1s / 有效 | 200 / 123.3s / 有效 | 200 / 75.6s / 有效 | 200 / 32.5s / 有效 |
| data-analysis | 200 / 74.4s / 可读失败 → 重试 18.3s 有效 | 200 / 72.4s / 有效 | 200 / 86.0s / 可读失败 → 重试 15.9s 有效 | 200 / 83.0s / 可读失败 → 重试 16.3s 有效 |
| intelligent-tracing | 200 / 261.3s / 有效 | 200 / 237.9s / 有效 | 200 / 269.1s / 有效 | 200 / 260.2s / 有效 |

- 12/12 请求均返回 200 并产生真实模型调用；3 项 data-analysis 原始回答为
  “分析结果已生成，但最终答案整理未完成”，属于模型答案整理失败，不是授权拒绝。
  以同一合法页面数据重试后 3 项均给出完整回答，因此有效回答覆盖 12/12。
- data-analysis 使用合法页面数据（`cityMonitoringData`）作为输入；未把缺少页面数据
  的请求当作业务失败。
- native 智能溯源 wait 的回答明确记录轨迹/PSCF 工具在本环境存在服务端接口缺陷，
  基于已成功获取的污染物谱、气象、空间梯度和站点差异作答；这是外部工具限制，
  不是本系统授权放行，也不宣称外部全数据能力。
- 负例：basic-qa 预留 + intelligent-tracing assistant → 409；伪造 `ledger_graph_id` →
  409；无台账预留 → 403。均在模型/工具外呼前拒绝，证据 `native-negative.txt`。
- 运行中撤权：流已开始后撤销 basic-qa，流出现“授权状态已变化/本次运行已停止”，
  attempt=`failed`，确认回答=0；证据 `inflight-revocation-after-fix.txt`。
- 撤权正反例：root-acceptance 撤销 `intelligent-tracing:use` 后该业务 403，
  `basic-qa` 仍 200；恢复后 intelligent-tracing 200。证据 `root-revocation.txt`。

## 4. 清理与无备份

- 迁移 `0021_business_authorization` 已应用到正式库；迁移 `0022_retire_mcp_bindings`
  在维护窗口执行并提交。
- 正式库：`alembic_version=0022_retire_mcp_bindings`；`mcp_identity_bindings` 的
  `to_regclass` 为空；public 表 56 个；无 `%_backup%`/`%_shadow%`/`%_bak` 对象。
- 旧接口：`GET /mcp-bindings` 返回 404；旧模型/路由/种子/权限菜单已从活跃代码删除。
- IAM：清理后 `enterprise:mcp`、`mcp:binding:read`、`mcp:binding:manage`
  均 `exists=false`，`total_role_edges=0`、`total_service_account_edges=0`；
  证据 `iam-final-cleanup-20261019T114535Z-*.json`。
- 无备份：本次 release 前后无新增 `.dump/.sql/.backup/.bak`、无备份 schema/表、
  无旧表改名；`.local/i4-live` 中仅有更早（02:14）已有的 `native.env.backup-*`，
  未被本次使用或删除。
- 启动器新增 `AGENT_RELEASE_NO_BACKUP=1` 无备份路径；本次 preflight/cleanup
  均通过该路径启动。
- 证据：`release-preflight.log`、`release-cleanup.log`、`pre-cleanup-objects.txt`、
  `post-cleanup-root-query.log`、`../agent-business-20261019/post-agent-cleanup-inventory.json`。

## 5. 回归与基线

- 根单元套件：440 passed / 7 failed / 73 skipped；7 项均为 HEAD 基线失败子集
  （`test_knowledge_route` 4、`test_checkpointing` 1、`test_guard_middleware_framework_compat`
  1、`test_artifact_db` 1，后者为全量顺序相关）。
- 管理端套件（自建空库跑到 0022）：207 passed / 12 failed / 5 skipped；12 项失败与 HEAD
  基线逐项一致（反馈采集开关与浏览器事件基线），无新增失败。
- 前端契约 63 项通过；`frontend` `tsc --noEmit` 通过；`admin/web` `tsc` 通过。
- 前端 `next build` 在当前离线环境因 Turbopack Google 字体解析失败；改用
  `next build --webpack` 成功生成 `BUILD_ID`，并用于本次正式重启。该失败是构建环境
  路径问题，不是业务代码失败。
- 正式切换后：`run_business_release.py verify` 报告四服务健康、迁移 0022、旧表不存在、
  无备份对象/文件。

## 6. 未完成/限制（如实记录）

1. **data-analysis 3 项原始回答整理失败**：同一合法页面数据重试后成功；保留原始失败
   记录，不把它们改写成一次通过。
2. **外部 MCP 数据隔离未证明**：生产连接是匿名共享只读，真实查询成功（109 工具，
   `helper_get_latest_time` 返回真实数据），但没有服务端企业数据隔离协议证据；
   本报告只声明“业务授权 + 本系统数据查看 + 实际查询范围”，不宣称外部全数据授权。
3. **resume 真实长运行未构造**：当前三张公开图没有可用的真实中断样例；resume/重新生成/
   旧快照缺失由管理端选择持久化测试与 native create_run 预留校验覆盖，未伪造中断运行。
4. **测试数据**：root 验收在正式库产生真实 request/attempt/answer 记录；未删除台账事实，
   未为普通用户造内容行。临时 `platform_boot` 测试授权已撤销；root 口令文件已删除。
5. 历史 benchmark/probe 脚本仍保留旧绑定模型文字，已标注为历史材料，不是活跃运行路径。

## 7. 证据索引

`docs/reviews/evidence/run/agent-business-authorization-20261019/`（Git 忽略）：

- 基线/扫描：`baseline.md`、`formal-db-inventory.txt`、`residual-scan-final.txt`
- 回归：`unit-tests.txt`、`admin-tests.txt`、`admin-baseline-failures.txt`、
  `admin-current-failures.txt`、`frontend-contracts.txt`、`frontend-tsc.txt`、
  `admin-web-lint.txt`
- root：`root-verify.log`、`root-agent-permissions.txt`、`root-data-view.txt`、
  `root-revocation.txt`、`inflight-revocation-after-fix.txt`、`native-negative.txt`
- 矩阵：`root-matrix.jsonl`、`root-matrix-retry.jsonl`、`root-matrix-summary.json`
- 清理：`pre-cleanup-objects.txt`、`release-preflight.log`、`release-cleanup.log`、
  `post-cleanup-root-query.log`
- 浏览器：`browser-business-data-analysis.png`、`browser-retired-mcp-404.png`
- IAM 侧：`/Users/acerola/Dev/Java/air_agent_iam/docs/reviews/evidence/run/agent-business-20261019/`
  与 `iam-final-cleanup-20261019T114535Z-*.json`
