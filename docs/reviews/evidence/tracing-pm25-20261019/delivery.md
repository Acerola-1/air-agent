# 首阶段交付状态（2026-10-19）

## 改动与原因

- 将 `intelligent-tracing` 从通用 DeepAgents 图改为“冻结请求 + 四专员 + 确定性证据/门禁 + 统一发布”的溯源图。
- 新增 `src/intelligent_tracing/`：请求/证据/发现/claim/报告 schema、analysis-policy-v1、字段级质控、异常、空间、气象、风场/滞后、过程解释、allowlist 门禁、ValidatedReport、内存/artifact 报告存储、四专员、lead 工具和发布中间件。
- 共享接线：`TenantRuntimeGuardMiddleware` 增加可选 `inject_mcp_tools=False`（默认保持原行为）；`test_graph_import_no_network` 的证据路径改到新 run 目录，旧 takeover 证据恢复 HEAD。
- 更新 `test_business_graph_config` 对 tracing 的结构断言；新增 `tests/unit_tests/tracing/` 夹具、fake model、P01–P18、阶段二和析因测试。
- 升级 change 内更正现行规格为三图并追加当前基线补验计划；旧六图/旧端口/事故记录保留。

## PM2.5 首阶段

已完成并在 G0/fixture 覆盖：TracingRequest 校验/冻结/scope 注入、站点消歧、PM2.5 QC/异常/空间/气象/风场/过程、四专员、并行/反序/失败、证据注册、claim allowlist、门禁、ValidatedReport、聊天/画布共用一个验证版本、revision/旧结果/删除/epoch 抑制、预算部分结果、P01–P13 业务契约。P14–P18 只有部分 G0 契约覆盖，真实 IAM/MCP/浏览器/持久化未执行，详见 `p14-p18-coverage.md`。

未完成（真实验收）：G1 真实模型/MCP/IAM、正式持久化与 checkpoint、真实浏览器、真实 MCP 依赖交集零外呼、真实画布发布/持久化/事件、补查上限计数。PM2.5 只能称为“G0 fixture 闭环”，不能称为正式上线闭环。

## 阶段二

六个污染物画像、O3 日最大 8h（跨日/缺测/有效性）、PM2.5 24h 滑动补充、多污染物不可直接跨单位排序等代码与 fixture 测试已完成；`DEFAULT_POLICY` 仍为 phase=1，仅放行 PM2.5，其他五项须显式使用 `STAGE2_POLICY`（未进入正式验收）。阶段二真实路径、SKILL/模板的正式验收未完成。

## 升级关口

- G0 已使用 `/tmp/air-agent-tracing-g0` 的 0.7.28 锁/环境完成；主 `.venv` 未替换。
- G1 未就绪：升级 worktree 仍旧 HEAD/六图，`pyproject.toml` 仍 `>=0.7.5`；真实模型网关、真实 MCP、观测/出网、存量 checkpoint 迁移/回滚、镜像 digest 未复验。详见 `baseline.md` 和升级 change 第 5 节。

## 析因结果

A0/A1/B0/B1 fixture 各 3 次，12 行 report digest 全部相同：`83c187aa6b33d6f0131a103acc7f7265bc24e0da38476d09c4d411e72069612e`。这只证明共享内核/门禁/渲染在固定数据下确定性一致；真实模型调查收益、Token、延迟和成本未采集，不作统计显著性结论。

## 检查与证据

- `pytest-tracing.txt`：定向 102 passed（tracing 82 + 既有图/台账回归 20）。
- `pytest-unit-full-g0.txt`：最后一次全量单测 6 failed / 513 passed / 73 skipped；其中 checkpointing 1 + knowledge_route 4 与 0.7.28 旧基线相同，`test_total_deadline_discards_late_success` 为既存 flaky（`pytest-flaky-check.txt` 有 pass/fail 交替，本次最终运行失败）。未发现其他新失败。
- `pytest-import-guard.txt`、`three-graph-import-20261019/import-network-guard.json`：三图 cold import 零网络尝试。
- `ruff-check.txt` / `ruff-format-check.txt`：通过。
- `openspec-validate-tracing.txt` / `openspec-validate-upgrade.txt`：strict 通过。
- `policy-v1.json` / `policy-v1.md`、`g0-spike.md`、`factorial.md`、`stage2.md`、`p14-p18-coverage.md`、`g0-tool-boundaries.json`、`factorial-fixture.json`。

## 耗时与 Token

- 实施耗时：本轮会话从读取 handoff 到当前约 50–60 分钟（含外部等待与验证）；未单独拆分模型实施/验证/外部等待的精确秒表。
- Token：未采集；不以 0 代替。
- 首交/补修：本轮为首交；Lead 复核后的修正包含在首交内，尚未形成独立补修评分。

## 资源回收

- 未创建正式库/容器/卷/端口/外部凭据；未 seed/truncate/downgrade；未调用轨迹/PSCF。
- 保留 `/tmp/air-agent-tracing-g0` 供复核；`/tmp/air-agent-upgrade` 原证据与 diff 未覆盖。未删除共享资源。

## 未执行项（显式）

- 真实模型、真实 MCP、真实 IAM 撤销、正式持久化/回读、正式画布发布/事件、浏览器/键盘/窄屏、G1 迁移/回滚、`httpx2` 追踪、`include_tools`/`fork`。
- `max_supplemental_queries` 未接入执行点；`3.5`、`3.6`、`3.7`、`3.8`、`3.9`、`4.5`–`4.8`、`5.1`、`5.3`–`5.9`、`6.7` 等保持未勾选或部分。`5.2` 仅完成 fixture 四组析因，不代表真实调查收益验收。
