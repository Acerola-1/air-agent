## 动机

当前项目仍使用单层 DeepAgent + semantic_router 意图识别的架构，存在以下问题：
1. **意图识别冗余**：`intent_match_tool` 基于 semantic_router 的 17 路由方案与 DeepAgent 的 SkillsMiddleware 渐进式披露机制功能重叠，且意图匹配结果（`intent_name`）未被写入 state，导致 `output-hints` 机制失效
2. **Skill 覆盖不足**：17 路路由中仅 `broadcast-hour` 有对应 SKILL.md，其余 16 个无 Skill 文件；`skills_expert/` 目录下无任何 Skill
3. **架构未对齐设计文档**：设计文档要求 4 个 SubAgent 模块化架构 + Skill 三层文件体系（SKILL.md 总纲 + SKILL_FAST.md / SKILL_EXPERT.md），当前仅是单层 agent + 单文件 Skill
4. **图表数据推送断裂**：`_extract_rich_outputs_from_message()` 仅处理 `rich_outputs[]` 格式，MCP 工具返回的 `chart_data` 格式无法被提取推送给前端

升级到 Deep Agent 设计文档定义的模块化架构，能让系统具备模块隔离、模式内聚、运行时路由、渐进式增强等核心能力，同时清理冗余组件。

## 变更内容

- **BREAKING**: 移除 `intent_match_tool` 工具及 `intent_config.py` 中的 semantic_router 路由系统，意图识别由 SkillsMiddleware + SubAgent system prompt 内路由替代
- **BREAKING**: 重构 graph.py 从单层 agent 升级为 Main Agent + 4 SubAgent 模块化架构（basic-agent、analysis-agent、interactive-agent、knowledge-agent）
- **BREAKING**: 重构 Skill 体系从单文件 SKILL.md 升级为三层文件体系（SKILL.md 总纲 + SKILL_FAST.md + SKILL_EXPERT.md）
- **BREAKING**: 重组 Skill 目录结构：`skills/basic/`（7种）、`skills/analysis/`（含 broadcast-hour）、`skills/interactive/`、`skills/knowledge/`
- 保留 `broadcast-hour` Skill 并归属到 `skills/analysis/` 模块
- 修复 chart_data 提取推送：增加 `_extract_rich_outputs_from_message` 对 MCP 返回 `chart_data` 格式的兼容处理，确保 ECharts 渲染数据能推送前端
- 保留 Skill YAML frontmatter 中的 `output-hints` 结构用于图表渲染声明
- 移除 `nodes.py` 中已失效的 `extract_mcp_data()` 和 `process_text2sql_content()` 死代码
- 新增 SubAgent 配置文件 `config/subagents.py`
- 新增统一模型配置 `config/models.py`
- 简化 `state.py`：移除不再需要的 `intent_name` 等字段，新增 `module`、`mode` 路由字段
- 重构 `deepagent_integration.py` 为 Main Agent + SubAgent 调度器
- 清理 `prompts.py` 中已迁移到 Skill 的提示词模板
- 更新 `CLAUDE.md` 反映新架构

## 能力

### 新增能力

- `subagent-architecture`: 4 个 SubAgent 模块化划分（basic/analysis/interactive/knowledge），Main Agent 路由调度，[module:XXX][mode:XXX] 标记注入
- `skill-three-layer-system`: Skill 三层文件体系（SKILL.md 总纲自动注入 + SKILL_FAST.md / SKILL_EXPERT.md 运行时按需读取），模式分流在 Skill 层解决
- `skill-content-basic`: 基础问题模块 7 种 Skill 的 SKILL.md 总纲定义（air-quality-realtime、ranking-assessment、compliance-feasibility、comparison-composition、station-extreme、regional-benchmark、trend-analysis）
- `chart-data-push`: MCP 返回的 ECharts chart_data 兼容提取与前端推送机制

### 修改的能力

<!-- 无现有 spec 需修改 -->

## 影响范围

- **核心文件重构**：`graph.py`、`state.py`、`deepagent_integration.py`、`tools.py`、`nodes.py` 均需重大改动
- **删除文件**：`intent_config.py`（semantic_router 路由系统）、`intent_match_tool` 工具定义
- **新增目录**：`config/`（subagents.py、models.py）、`skills/basic/`（7个子目录）、`skills/analysis/`、`skills/interactive/`、`skills/knowledge/`
- **Skill 文件迁移**：现有 `skills_fast/broadcast-hour/` → `skills/analysis/broadcast-hour/`，需补充 SKILL_FAST.md 和 SKILL_EXPERT.md
- **API 层影响**：前端需传递 `{module, mode, user_id, query}` 参数格式，API 层需格式化为 `[module:XXX][mode:XXX] query`
- **依赖变更**：移除 `semantic_router` 依赖，`deepagents` 包已是现有依赖
- **死代码清理**：`nodes.py` 中 `extract_mcp_data()`、`process_text2sql_content()` 等已失效函数