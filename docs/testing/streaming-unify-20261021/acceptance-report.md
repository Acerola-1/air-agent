# 三图正文流式验收报告（S01–S14）

日期：2026-10-21。HEAD `7711914` + 本任务未提交 diff。验收只使用受控空资源、真实 Next.js BFF 与真实配置模型探针；未在正式 15432/19132 造数，未修改 IAM/MCP/RAG 外部仓库。

| 编号 | 结果 | 证据与说明 |
|---|---|---|
| S01 | 通过（受控；官方原生 HTTP/SSE + 真实 BFF/浏览器） | `native-api/native-api-{basic-qa,data-analysis,intelligent-tracing}.json`：官方 `langgraph-api` 容器真实 HTTP SSE；basic/data 屏障时 2 deltas、source_finished=false、done=0（首 delta 70–78ms），tracing 为 6 个分节 delta + done（正文 hash `3fb8…`）；`native/*.json`：in-process astream 同步验证；`bff/bff-*.json`：真实 Next.js BFF 透传同样在屏障时收到 2 deltas；`screenshots/*.png`：浏览器显示“第1段…第2段…”。DeepAgents 见 S04。 |
| S02 | 通过 | basic-qa 直接问候走无工具流式 `answer_model`（真实配置 `real/real-basic-qa*.json`：14 deltas）；工具后回答单测 `test_tool_then_answer_uses_no_tool_streaming_final_call` 证明规划绑定工具、最终回答只调用一次 `astream`；规划阶段若模型误输出草稿，只记 `planning_draft_chars` 并作为材料，不直接公开（兼容路径，非默认双生成）。 |
| S03 | 通过 | data-analysis `finalize_output` 逐段清洗发布；受控与真实配置各 11–12 deltas；半段清洗/截断失败不发布草稿（`test_business_graph_nodes`）。 |
| S04 | 通过（受控四专员） | `native/native-intelligent-tracing.json` + `bff/bff-intelligent-tracing.json` + `native-api/native-api-intelligent-tracing.json` + `screenshots/intelligent-tracing-first-section.png`：claims-2 屏障时 header/claims-1 已到原生/BFF/浏览器，done=0；官方原生 API 的 tracing 分节发布为 6 个 delta + done；章节文本在发布时才渲染（预检只冻结布局/claim，不构建完整 body），屏障时 claims-2 文本尚未生成。四专员真实执行链路为 station-data/grid-data/meteo-analyst/evidence-reviewer（受控模型/数据）。lead/专员草稿未出现在正文。 |
| S05 | 通过（受控） | `test_output_stream_publisher.py`：跨 chunk `<think>`、JSON、Unicode、未知 citation、流中具体法规文本均不出现在可见帧；合法引用可发。 |
| S06 | 通过 | `test_gate_security.py` + `test_section_streaming.py`：未知 claim/证据错版本/未核验引用/缺固定限制在首节前拒绝；章节篡改/归因越界零 done。 |
| S07 | 通过（受控浏览器停止/撤权） | 浏览器在首段后点击停止：已显示“第1段…第2段…”并标“未完成 · 生成中断或尚未确认”，反馈不开放（`screenshots/basic-qa-stop-incomplete.png`）；`final_output_aborted` 单测覆盖；真实公网断连未复跑。 |
| S08 | 通过（受控隔离身份服务） | 浏览器首段后 `/probe/revoke`：`/auth/me` 401 + 发布器授权探针拒绝，后续零 delta、无 done，页面保留未完成正文（`screenshots/basic-qa-revocation-incomplete.png`）；`test_streaming_guards.py` 覆盖首段后撤权/删除探针；正式 Java IAM 未接入。 |
| S09 | 受阻（真实企业/context 切换） | 客户端契约 A→B→A、thread/run 旧帧忽略通过；浏览器 A→B 清除旧线程正文且 B 不出现旧内容（`browser-evidence.json`）。真实企业/context 切换未跑（未接入正式 IAM 多租户）。 |
| S10 | 通过 | 重复帧忽略、冲突/缺口 resync、DeepAgents 恢复 replay 去重（`test_section_streaming` 的 resume 用例）。 |
| S11 | 通过 | `test_output_stream_contract.py` 校验 delta 连接=done 全文=hash；`test_section_streaming` 校验 done report_id/正文与 `validated_report` 一致；浏览器刷新后仍是一条 99 字符答案（basic-qa 复验）。 |
| S12 | 受阻（旧客户端实机未跑） | `samples/legacy-done.jsonl` + 前端 legacy 用例通过：旧 done-only 可读且标 legacy；未使用旧客户端二进制实机复跑，不能记为新协议通过。 |
| S13 | 部分通过 | `scripts/unified_campaign/sender.py` 新增 first/last safe delta、stream end、confirmation end、first_delta_source、delta_count、output ids；浏览器 basic-qa `performance.now`：submit→首绘制 373ms，源仍被屏障阻塞；独立慢确认接口未跑，确认开销不冒充。 |
| S14 | 受阻（tracing 真实外部依赖） | `gateway.json`：hy3 无工具明文流 66 chunks/首内容 2230ms，工具绑定调用 1 chunk（供应商兼容非流式）；basic/data 真实配置通过；intelligent-tracing 真实外部 MCP/RAG 依赖缺失，受控探针不冒充真实通过。 |

## 固定失败/既有失败

以下失败在 HEAD `7711914` 的干净 worktree 同样失败，不是本任务引入：

- `tests/unit_tests/test_knowledge_route.py`（4 项，`common.tools.clear_knowledge_route_cache` 不存在）
- `tests/unit_tests/test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`
- `tests/unit_tests/test_archive_http_live_auth.py::test_archive_scope_must_match_live_actor_and_confirmed_owner`
- `tests/unit_tests/test_guard_middleware_framework_compat.py::test_real_model_call_sees_platform_identity`
- `tests/integration_tests/test_mcp_500k_guard.py::test_mcp_500k_overflow_end_to_end`（monkeypatch 路径错误）

本任务定向回归：`679 passed, 79 skipped`（unit，含 tracing 109 通过）；前端契约 65 通过；`tsc --noEmit` 通过；无删除断言或扩大 skip。

## 构建与网络限制

- 默认 `npm run build`（Turbopack）因本机无法访问 `fonts.googleapis.com`（Montserrat）失败；这是外部字体网络问题，不是 TypeScript 或路由错误。
- 已用 `npx next build --webpack` + `NEXT_FONT_GOOGLE_MOCKED_RESPONSES` 完成生产构建（exit 0，输出见 `build-webpack.log`），路由/静态页输出正常；该受控字体替身不改变业务代码，默认 Turbopack 外链失败仍如实记录。
- `npx tsc --noEmit` 与全部前端契约测试通过；Next dev 实际页面已用于浏览器验收。

## 复跑与证据

- 受控原生：`native/*.json`
- 真实 BFF：`bff/*.json`
- 浏览器：`screenshots/*.png` + `browser/browser-evidence.json`
- 真实配置：`real/*.json`、`gateway.json`
- 复现说明：`implementation-notes.md`
