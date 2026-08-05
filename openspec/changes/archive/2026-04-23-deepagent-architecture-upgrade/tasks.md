## 1. 项目结构调整与配置文件

- [x] 1.1 创建 `src/agent/config/` 目录，新增 `models.py`（统一模型配置 UNIFIED_MODEL）、`settings.py`（系统设置）
- [x] 1.2 创建 `src/agent/config/subagents.py`，定义 4 个 SubAgent 配置（BASIC_AGENT、ANALYSIS_AGENT、INTERACTIVE_AGENT、KNOWLEDGE_AGENT），包含 name、description、system_prompt、model、skills、tools、middleware
- [x] 1.3 创建 Skill 目录结构：`skills/basic/`（7 个子目录）、`skills/analysis/`、`skills/interactive/`、`skills/knowledge/`
- [x] 1.4 迁移 `skills_fast/broadcast-hour/` 到 `skills/analysis/broadcast-hour/`，补充 SKILL_FAST.md 和 SKILL_EXPERT.md

## 2. State 与 Graph 核心重构

- [x] 2.1 修改 `state.py`：移除不再需要的字段，新增 `module: Literal["basic","analysis","interactive","knowledge"]` 和 `mode: Literal["fast","expert"]` 路由字段
- [x] 2.2 重构 `graph.py`：从单层 agent 升级为 Main Agent 路由架构，permission 流程保留，chat_mode_init 改为解析 `[module:XXX][mode:XXX]` 标记写入 state
- [x] 2.3 重构 `deepagent_integration.py`：将 DeepAgentWrapper 替换为 Main Agent + SubAgent 调度器，Main Agent 使用 `create_deep_agent()` + `subagents=[...]` 配置 4 个 SubAgent

## 3. Skill 三层文件体系

- [x] 3.1 创建 `skills/basic/air-quality-realtime/SKILL.md` 总纲文件（YAML frontmatter + 模式选择指引 + 通用说明）
- [x] 3.2 创建 `skills/basic/ranking-assessment/SKILL.md` 总纲文件
- [x] 3.3 创建 `skills/basic/compliance-feasibility/SKILL.md` 总纲文件
- [x] 3.4 创建 `skills/basic/comparison-composition/SKILL.md` 总纲文件
- [x] 3.5 创建 `skills/basic/station-extreme/SKILL.md` 总纲文件
- [x] 3.6 创建 `skills/basic/regional-benchmark/SKILL.md` 总纲文件
- [x] 3.7 创建 `skills/basic/trend-analysis/SKILL.md` 总纲文件
- [x] 3.8 为 broadcast-hour 补充 SKILL_FAST.md 和 SKILL_EXPERT.md
- [x] 3.9 为基础问题模块 7 个 Skill 各创建基础的 SKILL_FAST.md（精简查询流程模板）

## 4. 移除冗余组件

- [x] 4.1 从 `tools.py` 移除 `intent_match_tool` 工具定义
- [x] 4.2 删除 `intent_config.py` 文件（移除 semantic_router 路由系统）
- [x] 4.3 从 `nodes.py` 移除 `extract_mcp_data()` 死代码函数
- [x] 4.4 从 `nodes.py` 移除 `process_text2sql_content()` 死代码函数
- [x] 4.5 从 `deepagent_integration.py` 移除对 `intent_match_tool` 的引用和 `_get_current_skill_hints()` 中读取 `intent_name` 的逻辑
- [x] 4.6 清理 `skills_fast/` 和 `skills_expert/` 旧目录下的 AGENTS.md 和空目录

## 5. chart_data 推送修复

- [x] 5.1 在 `_extract_rich_outputs_from_message()` 中增加对 MCP 返回 `{data: {chart_data: ...}}` 格式的兼容提取逻辑
- [x] 5.2 当检测到 ToolMessage 包含 `chart_data` 时，结合 output-hints 的 display_mode/chart_subtype 声明推送 rich_output 事件
- [x] 5.3 确保 `rich_outputs[]` 格式的提取逻辑保持不变（向后兼容）

## 6. SubAgent System Prompt 与模式路由

- [x] 6.1 编写 MODE_ROUTING_INSTRUCTION 模板（所有 SubAgent 共享的模式路由指令）
- [x] 6.2 编写 BASIC_AGENT_PROMPT（基础问题模块 system prompt，含模块特有指引和 7 种 Skill 列表）
- [x] 6.3 编写 ANALYSIS_AGENT_PROMPT（数据分析模块 system prompt）
- [x] 6.4 编写 INTERACTIVE_AGENT_PROMPT（智能交互模块 system prompt）
- [x] 6.5 编写 KNOWLEDGE_AGENT_PROMPT（知识问答模块 system prompt）
- [x] 6.6 编写 MAIN_AGENT_PROMPT（主调度器 system prompt，含路由规则表）

## 7. 提示词迁移与清理

- [x] 7.1 清理 `prompts.py` 中已迁移到 Skill 的提示词模板（保留仍被 permission 等节点使用的模板）
- [x] 7.2 清理 skills_fast/AGENTS.md 和 skills_expert/AGENTS.md 中对 `intent_match_tool` 的引用

## 8. 文档更新

- [x] 8.1 更新 `CLAUDE.md` 反映新架构（4 SubAgent 模块化 + Skill 三层文件体系 + Main Agent 路由）
- [x] 8.2 更新 `langgraph.json` 确保与新的 graph.py 入口兼容