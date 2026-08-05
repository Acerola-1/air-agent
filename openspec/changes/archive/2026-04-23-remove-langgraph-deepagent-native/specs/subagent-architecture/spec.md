## 修改需求

### 需求：四个 SubAgent 各自拥有模块专属技能和工具
系统 SHALL 定义 4 个 SubAgent（basic-agent、analysis-agent、interactive-agent、knowledge-agent），直接在 `create_deep_agent(subagents=...)` 中配置。每个 SubAgent MUST 拥有独立的技能目录、系统提示词和工具列表。工具列表 SHALL 通过延迟函数（`get_all_subagents()`）构建，通过模块属性访问（`nodes.xxx`）引用 MCP 工具，以确保使用初始化完成后的值。

#### 场景：MCP 初始化后的 SubAgent 工具构建
- **WHEN** `get_all_subagents()` 在 `ensure_mcp_tools()` 填充 MCP 工具列表之后被调用
- **THEN** 每个 SubAgent 的工具列表包含完整的 MCP 工具 + 静态工具（text2sql、get_beijing_time 等）

#### 场景：SubAgent 路由到正确的技能目录
- **WHEN** basic-agent 收到关于空气质量排名的查询
- **THEN** SubAgent 从 `skills/basic/ranking-assessment/` 加载 SKILL.md，然后根据 mode 标记读取 SKILL_FAST.md 或 SKILL_EXPERT.md