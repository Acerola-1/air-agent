# G0 0.7.28 接线 spike

环境：`/tmp/air-agent-tracing-g0`，Python 3.13.3、deepagents 0.7.28、langchain 1.4.6、langgraph 1.2.16、checkpoint 4.3.0、quickjs 0.3.9。数据为 `FixtureDataSource`，模型为 `ScriptedChatModel`；不是真实模型/MCP。

## 已覆盖

- 原生 `task` schema 只有 `description` / `subagent_type`；子图从父 state 的冻结 `request` 读取站点/时间/半径，不依赖 description 映射。
- 四专员：station-data / grid-data 为确定性 `CompiledSubAgent`（零模型），只写各自 findings；meteo-analyst 为隔离声明式专员，数值由确定性中间件计算；evidence-reviewer 只输出 `EvidenceReview`。
- 并行：station-data 与 grid-data 在同一轮 task 调用中并发，测试记录到两个 fetch 都在任一结束前开始；反序完成仍各自写回。
- 失败：第二个专员失败时返回显式 `*_data_unavailable` findings，报告仍可生成并保留限制。
- 结构回传：`structured_response` 变为父 `ToolMessage`；父 findings 由子图显式返回，二者分别验证。
- 修订：request/findings reducer 保留新 revision，旧 revision 迟到结果不发布；发布前再次校验 findings revision。
- 工具边界：lead 仅 `freeze_tracing_request`、`get_analysis_policy_summary`、`build_validated_report`、`publish_tracing_canvas`、`task`；无 GP、文件、execute、MCP、create_artifact、轨迹/PSCF。meteo 仅两个受限工具 + 结构化输出；reviewer 仅 `EvidenceReview`。
- 真实运行 scope：`freeze_tracing_request` 从可信 runtime config 生成 `RuntimeScope` 并写入 `request`/`scope`；无身份时失败关闭。已用“只给 config/identity、不预注入 scope”的全图测试证明 thread/context_version/epoch/analysis_id/revision 来源。
- 共享快照：`CachedDataSource` 按可信 scope/analysis/revision/epoch 缓存，并发专员共享一次 fetch；缓存命中前执行实时身份守卫。已测两个并发 fetch 只触发一次底层调用。
- 证据来源：fixture/controlled/mcp 快照按 bundle 来源写入证据；fixture 不再标 observation。目标站序列不派生自基线；O3/PM2.5 评价证据派生自目标序列。
- 空间语义：有效邻站不足时 pattern=unknown（保留 valid_neighbors_insufficient），不把缺测写成 isolated；只有至少 2 个有效邻站且无同步异常才给 isolated；1 个同步邻站不会给 multi_station_sync。
- 气象时间：`clean_weather` 统一时区、整点对齐、窗口过滤和重复保留最后一条，并记录 flags；P09 已覆盖 UTC 气象与本地浓度配对。
- 子任务：四个 child execution 均关联同一 `attempt_id`/`run_id`，未新建 request/submission/attempt。
- 预算：已实现模型调用数、task 数、task 并发、总超时（每次调用硬取消）和瞬时工具重试的执行点；超限时停止追加调查，发布带 `budget_exhausted` 限制的部分 ValidatedReport。`max_supplemental_queries` 尚未接入补查计数，属未完成项。
- 门禁：伪造 claim ID、reviewer 新增 claim、跨 scope/旧 revision 证据、存在但不支持命题的证据、缺固定限制、画布 content 旁路、回读 digest 篡改均失败；合法完整率百分比和“无法计算贡献率”通过。
- 发布：所有模型 AIMessage 标记 `ui_visible=false`；唯一可见 AI 消息是 ValidatedReport 正文；模型自由文本不进入正文或历史。
- 三图 cold import 无网络尝试（`pytest-import-guard.txt`）。

## 未覆盖 / 未执行

- 真实模型、真实 MCP、真实 IAM 撤销、正式持久化/checkpoint、真实浏览器、G1 迁移/回滚、`httpx2` 实际追踪。
- `ScopedMCPDataSource` 只有骨架和假 executor 的零调用测试；真实 MCP 工具名/响应映射未验证。
- 固定编排 A0/A1 与 DeepAgents B0/B1 只跑 fixture/fake model；调查任务与真实成本未采集。
- `include_tools` / `fork` 未执行（默认关闭，不影响本次验收）。

## 证据文件

- `pytest-tracing.txt`：定向测试 102 passed（tracing 82 + 既有图/台账回归）。
- `pytest-unit-full-g0.txt`：全量 6 failed / 513 passed / 73 skipped；5 个与 G0 旧基线相同，另 1 个为既存 flaky（`pytest-flaky-check.txt` 有 pass/fail 交替，本次最终运行失败）。
- `g0-tool-boundaries.json`：工具集合、task 描述、child executions。
- `factorial-fixture.json`：A0/A1/B0/B1 各 3 次，12 行同一 report digest。
- `policy-v1.json` / `policy-v1.md`：冻结策略。
