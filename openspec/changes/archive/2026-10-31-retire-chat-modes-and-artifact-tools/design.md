# Design

## Context

Basic QA 的模式只影响 `build_mode_context` 与 Skill 规则文件选择（`src/basic_qa/prompts.py:63-72`、`src/basic_qa/capabilities/runtime.py:106-107`），模型、工具、容量与并发一致。`chart_city_*` 在 `_UNAVAILABLE_CHARTS` 中被剔除（`src/basic_qa/capabilities/runtime.py:27-39,93-104`），但 7 个 Skill 仍声明必须调用。画布由 `create_artifact`（模型手写 HTML）和 `publish_tracing_canvas`（服务端发布已验证报告）两条路径组成，前端共用 `CanvasWindow`/`ContentRenderer`/artifact store。`compute_artifact` 是确定性计算，`artifact_db` 是大结果归档与报告持久化，均不是画布。MCP 的 `capability_mcp_mode` 是工具披露选择，不是聊天模式。旧会话分散在 LangGraph 表、台账正文表、Langfuse、localStorage 和遗留 SQLite；本次只清理本地存储，Langfuse 按用户决定保留。详见 proposal.md。

## Goals / Non-Goals

**Goals:**

- 删除 Basic QA 的快速/专家模式及其全部状态机残留，只保留一套回答契约。
- 9 个 Skill 合并为单一 `references/rules.md`，以专家语义为准并保留快速版中更完整的操作性内容。
- 彻底删除图表与画布链路，Basic QA 只输出文本与 Markdown 表格。
- 删除 MCP 选择的前端死代码，保留后端受理/审计协议且不暴露 UI。
- 移除前端 data-analysis 入口，后端注册与 assistant 保留。
- 一次性清理全部旧会话，保留审计事实、图定义、墓碑和验收证据。
- 修正规格基线与文档残留，使 change 可归档。

**Non-Goals:**

- 不实现新的图文/画布机制；后续单独设计。
- 不合并或重构 data-analysis 的 22 个 Skill；只做删除共享依赖后的最小兼容。
- 不改变模型、容量、工具并发、重试和身份授权策略。
- 不删除 `compute_artifact`、`artifact_db`、`artifact_http`/`ArchiveReader`。
- 不做数据库 schema 迁移，不删除 `tool_artifact_storage`/`artifact_computations` 表。

## Decisions

### 1. 模式删除是彻底删除，不做兼容层

删除 `Configuration.chat_mode`、`RoutingContext`/`get_routing_context`、`build_mode_context`、`FAST/EXPERT_MODE_SYSTEM_PROMPT`、材料加载的 `mode` 参数、`loaded_skills[*]["mode"]`、缓存键中的 mode，以及前端 store/switcher/runConfig 参数。旧客户端继续提交 `chat_mode` 时，平台 `configurable` 不校验未知键，Basic QA 直接忽略。

替代方案：保留 `chat_mode` 空转或加 feature flag。两者都会留下“看起来能切、实际无效”的僵尸状态，正是本次要删除的对象。

**data-analysis 兼容**：该图封存且前端入口移除，但仍注册在 `langgraph.json`。为避免它依赖已删除的共享模式 API，做最小兼容：`src/data_analysis/nodes.py` 固定读取其现有 `fast.md`，`src/data_analysis/prompts.py` 不再拼接模式提示；其 `expert.md` 保留为不可达文件并记录。不合并其 22 个 Skill，不改变菜单策略。

### 2. Skill 合并以专家语义为准，但保留快速版的操作性内容

每个 Skill 按以下顺序重写一份 `rules.md`：

1. 以 expert.md 的角色、输出结构和深度要求为骨架；
2. 从 fast.md 补入 expert 缺失或更弱的执行流程：Step 1–4、数据缺失处理、日期/单位/统计口径、表格行列上限、时效性标注；
3. 删除两者冲突的快速规则（如“绝对不输出成因/建议”）；
4. 删除所有图表步骤与 `chart_city_*` 调用；
5. 保持“单事实问题先给结论+数值+来源，分析类问题再展开专家结构”，避免统一结构把简单查询拉长。

替代方案：机械取并集（会产生矛盾规则和重复步骤）、保留双份文件（模式已不存在）、只留 expert 原文（6/9 的 expert.md 没有独立的“数据缺失处理”段落，只留指针）。三种都不采用。

**实施顺序**：先写 `rules.md`（fast/expert 暂留）→ 改加载器只读 `rules.md` 并忽略 `chat_mode` → 更新 SKILL.md → 删除 fast/expert → 重基线审计。加载器先于材料删除，避免部署窗口内材料缺失；最终提交只观察一次材料版本变化。

### 3. 画布链路整体删除，计算与归档保留

删除：

- `src/common/artifacts/`（仅 `create_artifact_tool.py`，无其他消费者）；
- `ARTIFACT_GENERATION_GUIDE` 及 Basic QA 提示拼接、`_GENERAL` 披露、`harmless` 豁免、`skill_tool_scope` create 映射、data-analysis `always_visible` 引用；
- `publish_tracing_canvas` 工具、`build_report_tools` 的画布返回、`CanvasContent`、`ValidatedReport.canvas`、`claims.py` 的画布构造、`compute_digest` 的 canvas 字段、`graph.py` 的画布工具与提示、`conversation.py` 的画布条目；
- 前端 `components/artifact/*`、`store/artifact-store.ts`、`types/artifact.ts`、`app/page.tsx` 的 Provider/CanvasWindow、`ChatPanel.tsx` 的 artifact 扫描、`air-agent-artifacts:*` localStorage。

保留：

- `compute_artifact`：对已归档结构化数据执行确定性计算，消费者见 `src/common/runtime_tools.py:26`、`src/common/data_manifest.py:71`、`src/common/data_governance.py:51`、`src/basic_qa/tool_runtime.py:467`、`src/data_analysis/tool_runtime.py:462`。
- `artifact_db`：大结果归档、计算配额、溯源报告持久化，消费者见 `src/basic_qa/context/tool_results.py:93`、`src/intelligent_tracing/report_store.py:68-105`、`src/common/artifact_http.py:246-270`。
- `artifact_http`/`ArchiveReader`：工具结果原文分页读取，不是画布，见 `frontend/components/chat/ToolStepsBlock.tsx:131`、`frontend/components/chat/ArchiveReader.tsx:8`。

**旧报告处理**：`ValidatedReport` 是 `extra="forbid"` 且读取时校验 digest（`src/intelligent_tracing/schemas.py:23-26`、`report_store.py:115-123`）。删除 canvas 字段后，7 条 `validated_report` 和 2 条 `validated_canvas` 旧归档不可回读。按用户决定，**发布前定向删除这 9 行**，不保留旧报告读取窗口；随后再移除 `ValidatedReport.canvas`。

### 4. MCP auto/allowlist 只删前端死代码，保留后端协议

事实：

- `frontend/components/chat/CapabilitySelector.tsx` 不存在；`frontend/lib/capability-store.ts` 无生产导入，只被 `frontend/tests/*` 使用；`frontend/lib/chat-store.ts:310` 发送只带 `chat_mode`。
- BFF 仍接受 `capability_selection` 并注入 `capability_mcp_mode`（`frontend/app/api/[..._path]/route.ts:307-337,417-428`）；后端 `normalize_capability_selection(None)` 默认 `auto`（`admin/api/app/services/ledger.py:247-260`）。
- 显式 allowlist 受 `AGENT_CAPABILITY_SELECTION_ENABLED` 控制，默认 false（`src/common/config/settings.py:211`、`admin/api/app/config.py:41`），当前 `.env`/`.env.langgraph` 未设置；显式选择会被拒绝。
- 语义：`auto` = 保持全量已授权 MCP 披露，模型可自行搜索且可以零调用；`allowlist` 空集合 = 移除全部 MCP（`src/common/capabilities.py:714-724`）。`capability_mcp_mode` 不是聊天模式。

决定：删除前端 `capability-store.ts`、其契约测试/probe/mock 与 `UX-CONTRACT` 的勾选 UI 段；保留后端 schema、`agent_capability_selections` 表、BFF 解析与 `capability_mcp_mode`。不写“用户可以勾选工具”，只描述后端受理/审计协议。替代方案是连后端协议一起删，但会破坏既有受理记录与审计，超出本次范围。

### 5. data-analysis 只隐藏前端入口

`langgraph.json` 保留 `data-analysis` 注册，`assistant` 表保留其记录（实测 6 个 assistant，含 data-analysis）。前端从 `graph-options.ts` 移除选项、从 `ChatPanel` 移除 `AnalysisMenuSelector`，删除仅由选择器使用的 `data-analysis-menus.json` 与对应测试。隐藏后，持有 `agent:business:data-analysis:use` 的用户仍可通过 BFF/原生 API 指定 `assistant_id=data-analysis` 调用；BFF 不按图名允许列表，服务端 `grant_for_graph` 只校验业务权限。该残留是“保留后端注册”的直接结果，记为已知限制，不宣称已下线。若要求零调用，必须移除 `langgraph.json` 注册并下线 assistant，需另行确认。

### 6. 旧会话清理：停写、备份、dry-run、可重入

不采用逐个删除 API（549 threads、45 个无归属线程、212 条墓碑，API 覆盖不到），也不采用 `TRUNCATE` 或删除 `agent_requests` 级联（会销毁审计事实）。

清理分组：

1. **LangGraph**：`thread`、`run`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_delete_queue`、`thread_ttl`、`cron`、`thread_ownership` 与 `store`。按 thread_id 枚举逐表删除；这些表之间无外键，不能只删 `thread` 指望级联。`store` 当前 0 行，按用户决定：清理时若非空，一并删除全部跨线程记忆。
2. **台账正文**：`agent_answer_contents.content_text/summary`（删除行，metadata 级联）、`agent_content_refs.content_snapshot`（清空并置 `deleted_at`）、`agent_feedback_comments.comment_text`、`agent_feedback_case_notes.note_text`、`agent_feedback_case_actions.result_snapshot`、`knowledge_citations`（整行删除，含 110 条 `excerpt`）、`tool_artifact_storage.raw_payload`（含 7 条 validated_report、2 条 validated_canvas、41 条 raw、4 条 derived）、`artifact_computations`、`agent_question_intent_contents.normalized_text/summary/embedding`、`agent_question_intents`、`agent_question_sources`、`agent_question_source_gaps.detail`、`agent_question_clusters` 相关文本。当前后四类表为空，脚本仍需处理非空情况。
3. **保留**：`agent_requests`、`agent_submissions`、`agent_run_attempts`、`agent_answers`、`agent_events`（payload 实测只有元数据，无正文）、`agent_outbox`（无 payload）、能力选择与工具调用事实、`agent_thread_deletions` 墓碑、`assistant`/`assistant_versions`、`audit_logs`（detail 只含身份/管理元数据）、`agent_iam_sessions`、`agent_iam_ticket_bindings`、`iam_reference_*`、成员/角色/权限与账号表。清库不得影响登录。
4. **Langfuse**：按用户决定不处理，保留历史 trace/observation 作为已知残留；不在本次脚本中导出或删除。
5. **前端**：一次性删除 `air-agent-artifacts:*` 与 `air-agent-chat-mode`；`air_agent_user_id` 保留。
6. **遗留**：确认无进程打开后删除 `db/checkpoints.db`。

执行前置条件：

- 停写：停止 `air-agent-langgraph-api`、admin API(8100)、chat BFF(3000) 及任何 outbox/insight worker；确认 `pg_stat_activity` 无应用连接（IAG/管理读除外需停）。
- 取消运行：`run.status` 只允许终态；当前实测 `success=429/error=45/interrupted=22`，无 pending/running；脚本仍须在发现活动运行时先取消并等待。
- 导出墓碑：`agent_thread_deletions` 212 行导出 CSV/JSON，保留表行。
- 备份：`pg_dump -Fc` 全量业务库到仓库外，校验 sha256；在临时库做恢复演练并核对目标表行数。SQLite 复制文件。Langfuse 不处理。
- 可重入：按 thread_id 分批，进度写仓库外 JSON；`DELETE ... WHERE thread_id = ANY(...)` 幂等；失败重跑跳过已完成批次。

### 7. 规格基线先同步，再归档本 change

主规格 `skill-three-layer-system`、`skill-content-basic`、`air-quality-forecast-skill-rules`、`chart-data-push`、`composite-backend-routing`、`forecast-data-bidirectional-rule`、`subagent-architecture` 均以 `## ADDED Requirements` 开头，`openspec validate` 能过但 archive 会拒绝，需求对工具不可见。`revise-basic-qa-session-capabilities` 有 4 个未完成任务，且其 delta 修改了 `skill-three-layer-system`、`skill-content-basic`、`composite-backend-routing` 的重叠需求。

决定：**先完成并归档基线 change**。`revise-basic-qa-session-capabilities` 还有 4 个未完成任务，手工把它的 delta 同步到主规格会留下一个“已应用但未归档”的 active change，后续归档会重复应用。正确顺序是：完成其剩余任务并归档 → 主规格得到 fast/expert 与 MCP 会话能力的真实基线 → 再应用本 change 的 delta（删除模式、改为 `rules.md`、撤除画布）。本 change 可以先实施代码，但不得先归档；若基线 change 长期不归档，本 change 只能保持 active，不能写入主规格。

### 8. 验证命令不使用裸 `mode`

`capability_mcp_mode` 是保留的 MCP 披露协议，裸 `rg "mode"` 会误伤。验证使用：`rg "chat_mode|build_mode_context|RoutingContext|FAST_MODE_SYSTEM_PROMPT|EXPERT_MODE_SYSTEM_PROMPT"`，并单独确认 `capability_mcp_mode` 仍存在。

## Risks / Trade-offs

- [合并后回答变长，值班速查体验下降] → 每份 rules 明确“单事实问题先给结论+数值+来源”；用 9 个真实问题对比合并前后长度与正确性。
- [专家规则丢失快速版的严格约束] → 按“expert 骨架 + fast 操作性内容”清单逐项核对，审计脚本重基线时人工复核。
- [删除画布后 7 条 validated_report / 2 条 validated_canvas 不可读] → 发布前定向删除这 9 行，或接受短暂不可读窗口；清理脚本同时删除。
- [删除 create_artifact 破坏现有 fixture/测试] → 先改测试再改代码；浏览器 fixture 不再模拟画布问题。
- [旧会话清理不可逆] → dry-run、导出墓碑、全库备份与恢复演练；不删审计事实、IAM/账号表与证据目录；Langfuse 按决定保留，不作为“已清空”宣传。
- [data-analysis 直连 API 仍可调用] → 记录为保留注册的已知残留；若要零调用需下线 graph/assistant，另行确认。
- [MCP 前端死代码删除影响测试] → 保留后端协议；删除仅前端 store/契约/probe，后端 `capability_mcp_mode` 语义测试继续通过。
- [审计脚本当前已因 business_registry 哈希漂移失败] → 本次重基线时一并重审该文件，不把旧失败当成新问题。
- [材料版本全部变化导致旧引用失效] → 与旧会话清理同批执行，不保留版本映射。

## Migration Plan

1. 规范化主规格结构，同步 `revise-basic-qa-session-capabilities` 基线；确认归档顺序。
2. 写 9 份 `rules.md`；加载器改读单一文件并忽略 `chat_mode`；保留 fast/expert 直到切换完成。
3. 删除 fast/expert 与图表声明；更新 SKILL.md 与审计基线。
4. 删除模式代码与前端开关；data-analysis 最小兼容。
5. 删除画布链路（后端 create/tracing + 前端组件/store），保留 compute/artifact_db/archive；发布前定向删除 9 条旧报告/画布归档。
6. 删除 MCP 前端死代码；移除 data-analysis 前端入口。
7. 更新文档与规格，跑定向测试、9 个真实问题、浏览器验证。
8. 发布后停写、取消运行、导出墓碑、备份与恢复演练；dry-run 后执行清理。
9. 验证清理结果；归档本 change（必须在 `revise-basic-qa-session-capabilities` 之后）。

回滚：代码与材料可按提交回退；已清理会话不可恢复，只能依赖执行前全库备份。

## Open Questions

无。旧报告预删、`knowledge_citations` 整行删除、Langfuse 保留、`store` 非空一并删除、空规格允许、清库授权均已确认。
