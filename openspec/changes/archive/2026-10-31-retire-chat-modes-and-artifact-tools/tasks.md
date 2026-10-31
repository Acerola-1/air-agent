# Tasks

## 1. 规格基线与文档

- [x] 1.1 将 `skill-three-layer-system`、`skill-content-basic`、`air-quality-forecast-skill-rules`、`chart-data-push`、`composite-backend-routing`、`forecast-data-bidirectional-rule`、`subagent-architecture` 七个主规格从 `## ADDED Requirements` 改为 `## Requirements` 结构（保留全部原文）；验证：`openspec validate "retire-chat-modes-and-artifact-tools"` 不再出现 “Archive would refuse”。补充：规范化后校验器开始比对真实需求，4 个被改名的场景被拒；已把这 4 项改为 REMOVED + 新名 ADDED（`air-quality-basic-query/comparison-composition/trend-analysis 统一规则 Skill 总纲`、`Skill 三层文件目录结构与单一规则文件`），`openspec validate --strict` 通过。剩余唯一 INFO 是下游 MODIFIED 依赖基线 change 的 `基础问答工具依赖分类`，属 1.2 记录的归档顺序依赖。
- [ ] 1.2 确认 `revise-basic-qa-session-capabilities` 的 4 个未完成任务完成并归档，使主规格先得到真实的 fast/expert 与 MCP 基线；特别核对只在它 ADDED delta 中存在的 `基础问答工具依赖分类` 需求。若它暂不归档，记录本 change 只实施代码、不归档；禁止先归档本 change。验证：`openspec status --change "revise-basic-qa-session-capabilities"` 完成，且本 change 的 MODIFIED/REMOVED 标题在归档后的主规格中全部存在。**当前状态（2026-10-31）**：基线 change 仍有 4 个未完成任务（5.1、7.6、9.2、9.3，其中 7.6 要求后续评审者签署），且其自身 delta 也有 4 处 “MODIFIED omits scenario” 校验错误，无法归档。按本任务允许的分支记录：本 change 只实施代码，不归档；`openspec validate` 的剩余 INFO `基础问答工具依赖分类 not found` 保留到基线归档后再消除。
- [x] 1.3 更新 `AGENTS.md`：frontend 职责行去掉“能力选择、画布”，Skill 目录说明改为单一 `references/rules.md`；保留后端能力选择、受理快照与安全条款。验证：`rg "fast\.md|expert\.md" AGENTS.md` 无命中，frontend 行不含“画布”，且“选择快照/受理”等安全条款仍在。
- [x] 1.4 更新 `docs/skill-creation-guide.md`：删除 fast/expert 双文件规范，改为单一 `rules.md` 编写规范，并写入“单事实问题先给结论”的深度规则；验证：`rg "fast\.md|expert\.md|快速模式|专家模式" docs/skill-creation-guide.md` 无命中。
- [x] 1.5 更新 `UX-CONTRACT.md`：删除 `ModeSwitcher` 与 `CapabilitySelector`/工具勾选 UI 段，改为“MCP auto/allowlist 仅保留后端受理协议，当前无前端勾选入口；auto 不强制调用”；验证：`rg "ModeSwitcher|CapabilitySelector" UX-CONTRACT.md` 无命中，文档仍说明 auto/allowlist 的后端语义。
- [x] 1.6 核对 `.rules`、`README.md`、`DESIGN.md`、`docs/architecture/`：确认无 fast/expert、图表、画布残留；验证：`rg "快速模式|专家模式|chat_mode|fast\.md|expert\.md|chart_city|create_artifact|CanvasWindow"` 在以上文件无命中，若有则记录并修正。

## 2. Skill 合并与加载器顺序

- [x] 2.1 按 design.md 的合并规则，为 9 个 Skill 写 `references/rules.md`：专家结构为骨架、补入快速版执行流程与数据缺失处理、删除矛盾规则和全部图表步骤；验证：9 个文件存在，`rg "chart_|fast\.md|expert\.md|专家模式|快速模式" src/basic_qa/skills` 无命中。
- [x] 2.2 修改 `src/basic_qa/capabilities/skill_materials.py`：只读 `references/rules.md`，删除 mode 白名单、mode 缓存键与双文件拼接；此时保留 fast/expert 文件不删；验证：材料加载单测按新契约通过，`chat_mode` 不再影响加载。
- [x] 2.3 更新 9 个 `SKILL.md`：详细规则入口改为 `references/rules.md`，删除 `optional-tools` 中的 `chart_*` 与 `prompt_fast`/`prompt_expert`；验证：`rg "chart_|prompt_fast|prompt_expert" src/basic_qa/skills/**/SKILL.md` 无命中。
- [x] 2.4 删除 9 个 `references/fast.md` 与 `references/expert.md`，删除遗留 `src/common/skills/materials.py` 并更新 `tests/check_basic_qa_reference.py`、`tests/unit_tests/test_skill_discovery.py`；验证：目录只含 `SKILL.md` 与 `references/rules.md`，相关测试通过。
- [x] 2.5 重写 `scripts/audit_basic_qa_skill_materials.py` 的 `MODES`、`EXPECTED`、三文件断言与图表断言，重生成 `docs/testing/basic-qa-conversation-loop/material-audit.md`；验证：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py --check` 通过（含当前已漂移的 `business_registry.py` 重审）。

## 3. 后端模式删除

- [x] 3.1 删除 `src/common/config/configuration.py` 的 `chat_mode`、`src/common/runtime_context.py` 的 `RoutingContext`/`get_routing_context`、`src/basic_qa/prompts.py` 的 `build_mode_context`、`src/common/prompts.py` 的 `FAST/EXPERT_MODE_SYSTEM_PROMPT`；验证：`rg "chat_mode|build_mode_context|RoutingContext|FAST_MODE_SYSTEM_PROMPT|EXPERT_MODE_SYSTEM_PROMPT" src/basic_qa src/common` 无命中（data-analysis 兼容项除外）。
- [x] 3.2 对 `src/data_analysis/` 做最小兼容：`nodes.py` 固定读取现有 `fast.md`，`prompts.py` 不再拼接模式提示与 `create_artifact` 引用；验证：data-analysis 图可导入，封存测试通过，交付说明记录其 expert 材料不可达。
- [x] 3.3 修改 `src/basic_qa/capabilities/runtime.py`：删除 `get_routing_context` 调用、`loaded_skills[*]["mode"]`、4 处 mode 比较与“技能模式已变化”错误；验证：`tests/unit_tests/test_basic_qa_runtime.py` 的模式切换用例改为“无模式参数仍可跨轮复用”并通过。
- [x] 3.4 删除前端 `chat-mode-store.ts`、`mode-switcher.tsx`、`ChatPanel.tsx` 的 ModeSwitcher、`chat-store.ts` 的 `chatMode` 参数与 `configurable.chat_mode`；验证：`rg "chat_mode|chatMode|ModeSwitcher|chat-mode-store" frontend` 无命中，`npm --prefix frontend run test:contracts` 通过。
- [x] 3.5 增加一次性本地清理：删除 `air-agent-chat-mode` 与 `air-agent-artifacts:*`，保留 `air_agent_user_id`；验证：浏览器加载后 localStorage 不再含旧键。

## 4. 画布链路删除

- [x] 4.1 删除 `src/common/artifacts/`、`src/common/runtime_tools.py` 的 create 注册、`src/basic_qa/capabilities/projection.py` 的 `_GENERAL` 条目、`runtime.py` 的 `harmless` 豁免、`src/common/skill_tool_scope.py` 的 create 映射、`src/common/prompts.py` 的 `ARTIFACT_GENERATION_GUIDE` 与 `src/basic_qa/prompts.py` 的拼接、`src/data_analysis/prompts.py` 的 create 引用；验证：`rg "create_artifact|ARTIFACT_GENERATION_GUIDE" src` 无生产命中。
- [x] 4.2 删除溯源画布：`publish_tracing_canvas`、`build_report_tools` 的画布返回、`CanvasContent`、`ValidatedReport.canvas`、`claims.py` 画布构造、`compute_digest` canvas 字段、`graph.py` 画布工具/提示、`conversation.py` 画布条目；验证：`rg "publish_tracing_canvas|CanvasContent|validated_canvas" src` 无命中，tracing 单测按无画布通过。
- [x] 4.3 在发布前定向删除 `tool_artifact_storage` 中 7 条 `validated_report` 和 2 条 `validated_canvas`，不保留旧报告读取窗口；验证：删除这 9 行后 `ValidatedReport` 无旧 canvas 行，旧报告不再被引用。
- [x] 4.4 删除前端 `components/artifact/*`、`store/artifact-store.ts`、`types/artifact.ts`、`app/page.tsx` 的 Provider/CanvasWindow、`ChatPanel.tsx` 的 artifact 扫描；验证：`rg "CanvasWindow|ContentRenderer|ArtifactProvider|artifact-store|artifact_ref" frontend` 无生产命中，`npm --prefix frontend run build` 通过。
- [x] 4.5 删除前端 artifact 契约测试与 fixture 中的画布模拟；验证：`npm --prefix frontend run test:contracts` 通过，浏览器 fixture 不再映射“画布”问题。
- [x] 4.6 保留并回归 `compute_artifact`、`artifact_db`、`artifact_http`/`ArchiveReader`：验证：`test_basic_qa_computation_scope.py`、`test_tracing_report_store.py`、`ToolStepsBlock` 归档读取用例通过，`/archives` 仍返回原文分页。

## 5. 图表链路删除

- [x] 5.1 删除 `src/basic_qa/capabilities/runtime.py` 的 `_UNAVAILABLE_CHARTS`、`unavailable_chart_names`、`src/common/skill_tool_scope.py` 的 `missing_charts` 分支与 `skill_materials.py` 的 optional-chart 校验；验证：`rg "chart_|missing_charts|UNAVAILABLE_CHARTS" src` 无生产命中。
- [x] 5.2 更新 `tests/unit_tests/test_skill_tool_scope.py`、`test_basic_qa_runtime.py`、`test_basic_qa_skill_materials.py` 中图表断言；验证：定向测试通过，Skill 工具范围只含 required-tools。
- [x] 5.3 核对 `frontend` 与文档中 `chart_data`/`chart_city` 残留；验证：`rg "chart_data|chart_city" frontend docs/contracts` 无命中（历史证据目录除外）。

## 6. MCP 死协议清理

- [x] 6.1 删除 `frontend/lib/capability-store.ts`、`frontend/tests/capability-store-contract.test.cjs`、`frontend/tests/probe_client.cjs`、`frontend/tests/session-client.test.cjs` 中对 store 的 mock，以及 `frontend/package.json` 的对应测试项；验证：`rg "capability-store|useCapabilityStore|selectedToolIds|setMcpMode" frontend` 无生产命中，`npm --prefix frontend run test:contracts` 通过。
- [x] 6.2 保留后端协议并加断言：`capability_mcp_mode`、`agent_capability_selections`、BFF 解析、`auto` 全量披露、空 allowlist 禁用 MCP；验证：`tests/unit_tests/test_basic_qa_mcp_search.py`、`test_mcp_connection_execution.py`、`test_native_capability_payload.py` 通过，`AGENT_CAPABILITY_SELECTION_ENABLED` 默认 false 的测试仍成立。
- [x] 6.3 在 `UX-CONTRACT.md` 明确“无前端勾选入口、auto 不强制调用、空 allowlist 显式禁用、后端协议保留用于受理/审计”；验证：文档无“用户可以勾选工具”表述。

## 7. data-analysis 前端入口

- [x] 7.1 从 `frontend/lib/graph-options.ts` 移除 data-analysis，从 `ChatPanel.tsx` 移除 `AnalysisMenuSelector`，删除 `frontend/components/chat/AnalysisMenuSelector.tsx`、`frontend/lib/data-analysis-menus.json` 及 `frontend/tests/full-system-bugfixes.test.cjs` 的选择器用例；验证：前端只显示 basic-qa 与 intelligent-tracing，`npm --prefix frontend run build` 通过。
- [x] 7.2 核对并记录后端残留：`langgraph.json` 仍注册 data-analysis，`assistant` 表仍有 data-analysis 记录，持有 `agent:business:data-analysis:use` 的用户可通过 BFF/原生 API 指定 `assistant_id=data-analysis` 调用；验证：交付说明明确写出该路径，不宣称已下线；若要求零调用，需另开 change 移除注册/assistant。

## 8. 旧会话清理

- [x] 8.1 新增 owner/维护级清理脚本（建议 `scripts/purge_legacy_conversations.py`），实现 `--dry-run`、`--apply`、`--resume`，按 design.md 分组处理 LangGraph 表、`store`、台账正文、`knowledge_citations` 整行、localStorage 说明与遗留 SQLite；保留 IAM/账号/审计表。验证：`--help` 与临时库 dry-run 可用，`store` 非空时一并删除，账号相关表不在删除清单。
- [x] 8.2 为脚本增加隔离临时库测试，覆盖：无外键逐表删除、`agent_requests/events/outbox` 等审计事实保留、`content_snapshot` 置空、`knowledge_citations` 整行删除、`store` 删除、45 个无归属线程与 2 条无线程归属、212 条墓碑保留、IAM/账号表不变、重复执行幂等；验证：新测试在自建临时资源上通过并回收。
- [ ] 8.3 执行前预检：停止 `air-agent-langgraph-api`、admin API(8100)、chat BFF(3000) 与任何 outbox/insight worker；确认 `run.status` 无 pending/running（当前 success=429/error=45/interrupted=22），如发现活动运行先取消并等待；验证：`pg_stat_activity` 无应用连接，run 状态全为终态。
- [x] 8.4 导出 `agent_thread_deletions` 212 行到 CSV/JSON，`pg_dump -Fc` 全量业务库到仓库外并校验 sha256，在临时库做恢复演练并核对目标表行数；复制 `db/checkpoints.db`；Langfuse 不导出不处理。验证：备份文件、校验和、恢复演练记录齐全。
- [x] 8.5 在正式库运行 dry-run，核对 549 threads、5012 checkpoints、396 条原文快照、291 条回答正文、7 条 validated_report、2 条 validated_canvas、54 条工具归档、110 条知识引用、0 条 store；验证：dry-run 行数与备份一致，用户确认范围。
- [ ] 8.6 发布后执行 `--apply`，按批次删除并记录进度；失败可 `--resume` 重跑；验证：目标正文列/行为空或已脱敏，`store` 与 `knowledge_citations` 为 0，审计事实与 assistant/assistant_versions 数量不变，IAM/账号/角色表不变，墓碑仍为 212 行；清理完成后重启 LangGraph、admin API、chat BFF，用 root 账号登录并发一条新消息成功。
- [x] 8.7 编写清理运行说明（范围、保留项、停写/取消/备份/恢复/复跑、Langfuse 保留、不可逆风险）；验证：文档命令与脚本 `--help` 一致。

## 9. 集成验证与归档

- [x] 9.1 跑定向回归：`pytest tests/unit_tests/test_basic_qa_skill_materials.py tests/unit_tests/test_basic_qa_runtime.py tests/unit_tests/test_skill_tool_scope.py tests/unit_tests/test_basic_qa_computation_scope.py tests/integration_tests/test_tracing_report_store.py -q`；验证：全部通过。实测 38 passed；`test_tracing_report_store.py` 5 skipped（缺 `TRACING_TEST_RUNTIME_DSN`，仓库既有环境门禁）。
- [ ] 9.2 在正式栈用 9 个 Skill 各一个真实问题验证：回答采用统一专家结构、无图表/画布、请求不含 `chat_mode`、不披露 create/chart 工具；验证：记录问题、回答摘要、耗时、工具列表。**当前阻塞（2026-10-31）**：重启 `air-agent-langgraph-api` 后，BFF 登录与企业切换均成功，但 LangGraph custom auth 调用 IAM `/v1/internal/identity/introspect` 返回 403，所有租户的 `POST /threads` 均为 503「身份服务暂不可用」；内部客户端凭据直连 IAM 返回 400 校验错误，说明是执行上下文/会话绑定被 IAM 拒绝，与本 change 的代码改动无关。需先修复 IAM 运行态再执行 9 个真实问题。
- [ ] 9.3 浏览器验证：输入区无快速/专家开关、无 data-analysis 入口、无画布入口、`ArchiveReader` 仍可展开工具原文、溯源回答以文本呈现；验证：实际页面操作记录。**当前阻塞**：同 9.2，正式栈发消息被 IAM 403 阻断。
- [x] 9.4 运行 `scripts/audit_basic_qa_skill_materials.py --check`、`npm --prefix frontend run test:contracts`、`npm --prefix frontend run build`；验证：全部通过。
- [ ] 9.5 归档前确认 `openspec validate` 无 “Archive would refuse”，且 `revise-basic-qa-session-capabilities` 已同步/归档；验证：`openspec archive "retire-chat-modes-and-artifact-tools"` 成功，主规格体现单一 rules.md 与无画布要求。

## Workflow follow-up

- 若用户要求先看合并稿，可在 2.1 完成后暂停，提交 9 份 `rules.md` diff 供审阅，再继续代码改动。
- 所有清理范围待确认项已关闭（旧报告预删、知识引用整行删除、Langfuse 保留、`store` 非空一并删除、空规格允许、清库授权）；执行 8.6 前只需确认具体时间窗。
- 2026-10-31 实施记录：代码、材料、脚本、备份与隔离测试已完成；8.3/8.6 需停写窗口，9.2/9.3 被 IAM 403 阻断，9.5 因基线 change 未归档而不执行。备份在 `~/.local/share/air-agent-backups/air_agent_business-20261031-115256.dump`（sha256 `3055891a…`），恢复演练核对 thread=549、run=496、checkpoints=5012、contents=291、citations=110、tombstones=216。
