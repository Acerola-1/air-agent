## 背景

当前项目使用单层 DeepAgent + semantic_router 意图识别架构。graph.py 有 6 个节点：`permission_agent → permission_eval → chat_mode_init → deepagent_executor → expand_question → END`。`deepagent_executor` 是唯一执行节点，通过 `DeepAgentWrapper` 包装 `create_deep_agent()` 运行。

现有问题：
- `intent_match_tool` 作为 LLM 工具暴露给 DeepAgent，意图匹配由 semantic_router 17 路路由实现，但匹配结果 `intent_name` 未写入 state，`output-hints` 机制失效
- 仅 `broadcast-hour` 有 SKILL.md，其余 16 路无 Skill 文件；`skills_expert/` 无任何 Skill
- Skill 目录结构为 `skills_fast/` / `skills_expert/`，不符合设计文档的三层文件体系
- `_extract_rich_outputs_from_message()` 仅处理 `rich_outputs[]`，MCP 返回的 `chart_data` 格式无法提取推送前端
- `nodes.py` 中 `extract_mcp_data()` 等函数为死代码

设计文档要求：4 个 SubAgent 模块化架构 + Skill 三层文件体系 + `[module:XXX][mode:XXX]` 标记路由 + 统一模型 + Middleware 横切关注点。

## 目标 / 非目标

**目标：**
- 实现 Main Agent 路由 + 4 SubAgent 模块化架构（basic、analysis、interactive、knowledge）
- 实现 Skill 三层文件体系：SKILL.md（总纲，SkillsMiddleware 自动注入）+ SKILL_FAST.md（运行时读取）+ SKILL_EXPERT.md（运行时读取）
- 创建基础问题模块 7 种 Skill 的 SKILL.md 总纲文件
- 保留 `broadcast-hour` Skill，归属到 `analysis` 模块，补充三层文件
- 修复 chart_data 提取推送，兼容 MCP 返回的 ECharts 数据
- 移除 `intent_match_tool` 和 semantic_router 路由系统
- 清理死代码（`extract_mcp_data`、`process_text2sql_content` 等）
- 简化 state.py，新增 `module`、`mode` 路由字段

**非目标：**
- 不实现 analysis/interactive/knowledge 模块的完整 Skill 内容（SKILL_FAST.md / SKILL_EXPERT.md），这些模块后续逐步补充
- 不实现自定义 Middleware（ContentFilterMiddleware、LoggingMiddleware 等），留到后续迭代
- 不改动前端代码和 API 层，前端参数格式变更由前端团队自行适配
- 不切换模型（保持当前 SiliconFlow DeepSeek-V3.2 / Volcengine ARK 配置）
- 不实现数据权限校验中间件化（保留当前 tool-based 权限校验方式）

## 决策

### 1. SubAgent 划分维度：按模块而非按模式

**选择**：4 个 SubAgent 按业务模块划分（basic-agent、analysis-agent、interactive-agent、knowledge-agent）

**理由**：模块是业务隔离的自然边界，4 个 SubAgent 数量可控。如果按模式划分（fast-agent、expert-agent），SubAgent 数量会膨胀到 4x2=8，且 fast/expert 区别在 prompt 深度而非模型，用 Skill 三层文件在 Skill 层解决更合理。

**替代方案**：按模式 x 模块矩阵（8 SubAgent）——拒绝，因模式差异是 Skill 层差异而非架构层差异。

### 2. 模式信号传递：task() description 注入 `[mode:XXX]` 标记

**选择**：通过 `task(subagent_type="basic-agent", description="[mode:fast] 北京空气质量怎么样")` 传递模式信号

**理由**：`task()` 的 description 参数是 SubAgent 运行时的唯一信息通道。SubAgent model 是静态的（编译期绑定），无法运行时切换模型，因此模式信号只能通过 description 注入让 SubAgent 自行选择 Skill 变体。

**替代方案**：通过 state 字段传递——拒绝，因 DeepAgent 子图状态隔离，state 不会自动传递到子图。

### 3. Skill 三层文件体系 vs 单文件 Skill

**选择**：SKILL.md 总纲（自动注入）+ SKILL_FAST.md / SKILL_EXPERT.md（运行时读取）

**理由**：
- SkillsMiddleware 只扫描 SKILL.md，将 name+description 注入 system prompt 的 Skills 列表（渐进式披露）
- SubAgent 根据 `[mode:XXX]` 标记用 `read_file` 工具主动读取对应变体文件
- 模式分流在 Skill 层解决，不需要拆 SubAgent，加新模式只改 Skill 内容

**替代方案**：单文件 Skill 内嵌模式分支——拒绝，单文件过大不利于维护，且 SkillsMiddleware 无法按需披露。

### 4. intent_match_tool 移除 vs 保留

**选择**：移除 `intent_match_tool` 和 semantic_router 路由系统

**理由**：
- SkillsMiddleware 的渐进式披露机制（元数据注入 → 模型自行识别 Skill）已能替代意图识别
- `intent_match_tool` 作为 LLM 工具暴露给 DeepAgent，增加了不必要的工具调用轮次
- 意图匹配结果 `intent_name` 未写入 state，导致 `output-hints` 机制失效，移除可消除此 bug
- semantic_router 的 17 路路由本质上与 SkillsMiddleware 的 Skill 元数据列表功能重叠

**替代方案**：保留 `intent_match_tool` 但修复 state 写入——拒绝，冗余且增加 LLM 调用成本。

### 5. chart_data 推送方案：MCP 返回兼容提取

**选择**：在 `_extract_rich_outputs_from_message()` 中增加对 MCP 返回 `chart_data` 格式的兼容提取

**理由**：当前 MCP 工具返回 `{data: {llm_data: ..., chart_data: ...}}` 格式，部分 MCP 服务已能返回渲染好的 ECharts 数据。`rich_outputs[]` 是新格式但尚未全面覆盖，需要兼容旧格式以确保图表数据能推送前端渲染。

**替代方案**：要求所有 MCP 服务统一返回 `rich_outputs[]` 格式——拒绝，MCP 服务改造范围大且非本项目可控。

### 6. broadcast-hour 归属模块

**选择**：broadcast-hour 归属到 `analysis-agent`（数据分析模块）

**理由**：用户明确指出 broadcast-hour 属于数据分析，其功能是"小时播报数据分析查询"，核心是统计分析而非基础实况查询。

### 7. 保留 Skill YAML frontmatter 中的 output-hints

**选择**：保留 `output-hints` 结构，作为图表渲染的声明机制

**理由**：output-hints 声明了 MCP 工具返回数据的 display_mode 和 chart_subtype，配合 chart_data 推送机制，能指导前端如何渲染图表数据。这是 Skill 层与前端渲染的契约。

## 风险 / 权衡

- **[SubAgent 调用成本增加]** → Main Agent 需先路由到 SubAgent，SubAgent 再执行 Skill，增加了 1 次 LLM 调用。缓解：Main Agent 使用轻量模型仅做路由，实际业务由 SubAgent 处理，总成本可控。
- **[Skill 文件覆盖不全]** → analysis/interactive/knowledge 模块的 SKILL_FAST.md / SKILL_EXPERT.md 尚未创建，SubAgent 无模式分流指引。缓解：先创建 SKILL.md 总纲，SubAgent 默认走快速模式流程；后续逐步补充。
- **[状态字段精简可能影响现有功能]** → 移除 `intent_name` 等字段可能影响依赖这些字段的代码路径。缓解：这些字段在当前流程中已不被写入（bug），移除是清理而非破坏。
- **[MCP chart_data 兼容提取的稳定性]** → 不同 MCP 工具返回的 chart_data 格式可能不一致。缓解：提取逻辑采用宽松匹配，缺失 chart_data 时静默跳过而非报错。
- **[前端参数格式变更]** -> 前端需传递 `{module, mode}` 参数。缓解：这是非目标，前端团队自行适配；可设默认值（module=None 由 Main Agent 智能判断， mode=fast）。