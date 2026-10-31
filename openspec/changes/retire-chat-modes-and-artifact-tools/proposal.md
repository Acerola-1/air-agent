# 撤回快速/专家模式并清理图表与画布

## Why

Basic QA 的快速/专家模式只改变系统提示和 Skill 规则文件：模型、工具、容量与并发完全相同（`src/basic_qa/prompts.py:63-72`、`src/common/prompts.py:224-236`、`src/basic_qa/capabilities/runtime.py:106-107`）。9 个 Skill 的 expert 规则不是 fast 的超集，且切换模式会让已加载 Skill 失效（`runtime.py:239-240`）。图表工具从未注册、加载时被剔除（`runtime.py:27-39,93-104`），但 7 个 Skill 仍写着必须调用。画布链路由模型手写 HTML 或服务端发布报告组成，缺少流式事件，旧报告与画布数据还会在删字段后无法回读（`src/intelligent_tracing/schemas.py:23-26`、`report_store.py:115-123`）。旧会话数据分布在 LangGraph 表、台账正文、Langfuse、浏览器 localStorage 和遗留 SQLite，现有只有单线程删除入口。继续保留这些表面只会增加材料、状态机和前端复杂度，而无法提供可感知的差异化服务。

## What Changes

- **BREAKING** 删除 Basic QA 的快速/专家模式：前端开关与 localStorage、发送参数 `chat_mode`、后端 `Configuration.chat_mode`、运行时模式上下文、系统提示分叉、Skill 材料 `mode` 参数、能力状态与缓存中的 `mode`、切换模式后的重载逻辑全部移除。旧客户端继续提交 `chat_mode` 时忽略该键。
- 将 9 个 Skill 的 `references/fast.md` 与 `references/expert.md` 合并为单一 `references/rules.md`：以专家语义和输出结构为准，补入快速版更完整的执行流程、数据缺失处理、日期/口径/表格约束，删除矛盾规则和全部图表步骤；回答统一采用专家结构，接受更长输出。
- 从 Skill 与运行时中彻底移除 `chart_city_*`：删除 `allowed-tools`/`optional-tools` 声明、规则中的图表生成步骤、`_UNAVAILABLE_CHARTS` 与 `missing_charts` 补丁机制；Basic QA 不再承诺任何图表输出。
- **画布整体按未完成功能撤除**：删除 `create_artifact`、`ARTIFACT_GENERATION_GUIDE`、`publish_tracing_canvas`、`CanvasContent`/`ValidatedReport.canvas`、前端 `CanvasWindow`/`ContentRenderer`/`ArtifactProvider`/artifact store/types、ChatPanel 的 artifact 扫描与 `air-agent-artifacts:*`。保留 `compute_artifact`（对已归档结构化数据的确定性计算）、`artifact_db`（工具大结果归档）、`artifact_http`/`ArchiveReader`（原文分页读取）和 `ValidatedReport` 报告本体（不含画布）。
- 清理 MCP 选择的前端死代码：`frontend/components/chat/CapabilitySelector.tsx` 已不存在，`frontend/lib/capability-store.ts` 无生产导入，发送参数只有 `chat_mode`；保留后端 `capability_mcp_mode` 受理/审计协议与 schema。`auto` 表示没有用户名单、模型在企业授权目录内自行搜索、可以零调用；空 allowlist 表示显式禁用 MCP，二者不混用；`capability_mcp_mode` 不是聊天模式，不在本次删除范围。
- 移除前端 data-analysis 图入口与 `AnalysisMenuSelector`；后端 `langgraph.json` 注册、assistant 记录和封存代码保留。隐藏入口后，持有 `agent:business:data-analysis:use` 的用户仍可通过 BFF/原生 API 指定 `assistant_id=data-analysis` 调用；这是保留注册的已知残留，不在本次假装不可达。
- 清理本地可读旧会话：LangGraph `thread/run/checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_delete_queue/thread_ttl/cron/thread_ownership` 与 `store`，台账正文（回答快照、原文引用、反馈意见、处理说明、知识引用整行、工具归档、意图正文），前端 localStorage 与遗留 `db/checkpoints.db`；保留审计事实、图定义、墓碑清单、IAM/账号表和验收证据。清理前停写、取消运行、导出墓碑、备份并做恢复演练；脚本先 dry-run，失败可重入。**Langfuse 按用户决定不处理**，历史 trace 保留为已知残留；清库不得影响账号登录。
- **规格基线**：主规格当前仍以 `## ADDED Requirements` 为顶层结构，`openspec` 无法解析需求，archive 会拒绝；`revise-basic-qa-session-capabilities` 仍有 4 个未完成任务且是本轮实现基线。本 change 以该 active change 的 delta 文本作为真实基线重写，并声明它必须先同步/归档，否则本 change 不归档。
- 清理文档残留：`AGENTS.md`、`UX-CONTRACT.md`、`docs/skill-creation-guide.md`；`composite-backend-routing`、`forecast-data-bidirectional-rule` 的 fast/expert 引用；退役 `chart-data-push` 与已无代码对应的 `subagent-architecture`。

## Capabilities

### New Capabilities

- `basic-qa-answer-presentation`: Basic QA 的单一回答契约与输出形态：统一专家结构、无运行模式、不产出图表或画布。

### Modified Capabilities

- `skill-three-layer-system`: Skill 规则改为单一 `references/rules.md`，删除 fast/expert 变体、模式读取要求与模式状态。
- `skill-content-basic`: 9 个 Skill 的天气、输出与工具依赖要求去掉快速/专家措辞，按统一结构执行；工具依赖不再区分必选与可选绘图。
- `air-quality-forecast-skill-rules`: 删除「专家模式输出结构」触发条件，按统一回答结构判断预报补充。
- `composite-backend-routing`: 删除「模式材料」与 fast/expert 文件引用，Basic QA 只读单一 `rules.md`。
- `forecast-data-bidirectional-rule`: 预报独立步骤改为在 `references/rules.md` 中呈现。

### Removed Capabilities

- `chart-data-push`: 撤除 chart_data 推送与 output-hints 要求（图表工具、材料声明与前端渲染一并删除）。
- `subagent-architecture`: `[module:XXX][mode:XXX]` 路由、6 SubAgent 与 AgentState 路由字段的旧架构已无代码对应，整项退役。

## Impact

- 后端：`src/common/config/configuration.py`、`runtime_context.py`、`prompts.py`、`runtime_tools.py`、`skill_tool_scope.py`；`src/basic_qa/` 的 prompts、nodes、capabilities、projection；`src/common/artifacts/` 删除；`src/intelligent_tracing/` 的 tools、schemas、claims、graph、conversation；`src/data_analysis/` 做最小兼容改动（去掉模式与 `create_artifact` 引用）。
- 前端：`chat-mode-store.ts`、`mode-switcher.tsx`、`ChatPanel.tsx`、`chat-store.ts`、`capability-store.ts`、`GraphSwitcher`、`AnalysisMenuSelector`、artifact 组件/store/types、相关契约测试与 localStorage 清理。
- 数据：新增 owner/维护级批量清理脚本（不做 schema 迁移，artifact 表保留）；清理 LangGraph 表与 `store`、台账正文、浏览器 localStorage 与遗留 SQLite；Langfuse 不处理。当前实测：549 threads、5,012 checkpoints、396 条原文快照、291 条回答正文、7 条 validated_report、2 条 validated_canvas、54 条工具归档、110 条知识引用、212 条墓碑；45 个线程无归属、2 条归属无线程、4 个已墓碑线程仍存在。
- 材料与测试：9 份 Skill 合并稿、`scripts/audit_basic_qa_skill_materials.py` 重基线、`material-audit.md` 重生成、单测/集成/前端契约测试。
- 依赖：`revise-basic-qa-session-capabilities` 必须先完成并归档，主规格才有可用的 fast/expert 与 MCP 基线；本 change 可以先实施代码，但不得先于它归档，否则会把模式材料要求写回主规格。
