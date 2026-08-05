## 新增需求

### 需求：Main Agent 路由调度
系统 SHALL 提供 Main Agent 作为路由调度器，接收格式为 `[module:XXX][mode:XXX] 实际问题` 的用户消息，解析 module 标记路由到对应 SubAgent，将 mode 标记透传到 task() 的 description 参数。

#### 场景： 基础问题模块路由
- **WHEN** 用户消息为 `[module:basic][mode:fast] 北京空气质量怎么样`
- **THEN** Main Agent 调用 `task(subagent_type="basic-agent", description="[mode:fast] 北京空气质量怎么样")`

#### 场景： 数据分析模块路由
- **WHEN** 用户消息为 `[module:analysis][mode:expert] 分析绍兴市小时播报数据`
- **THEN** Main Agent 调用 `task(subagent_type="analysis-agent", description="[mode:expert] 分析绍兴市小时播报数据")`

#### 场景： 未指定 module 时智能判断
- **WHEN** 用户消息为 `[mode:fast] 北京空气质量怎么样`（无 module 标记）
- **THEN** Main Agent 根据问题内容智能判断 module 并路由到对应 SubAgent

#### 场景： 未指定 mode 时默认 fast
- **WHEN** 用户消息为 `[module:basic] 北京空气质量怎么样`（无 mode 标记）
- **THEN** Main Agent 默认注入 `[mode:fast]` 到 description 参数

### 需求：4 SubAgent 模块化定义
系统 SHALL 定义 4 个 SubAgent，每个 SubAgent 绑定模块专属 skills 和 tools，使用统一模型。

#### 场景： basic-agent 配置
- **WHEN** SubAgent basic-agent 被调用
- **THEN** basic-agent 加载 `skills/basic/` 下的 Skill 元数据，使用基础问题模块专属工具（query_city_daily_air_data 等）执行业务

#### 场景： analysis-agent 配置
- **WHEN** SubAgent analysis-agent 被调用
- **THEN** analysis-agent 加载 `skills/analysis/` 下的 Skill 元数据（含 broadcast-hour），使用数据分析模块专属工具执行业务

#### 场景： interactive-agent 配置
- **WHEN** SubAgent interactive-agent 被调用
- **THEN** interactive-agent 加载 `skills/interactive/` 下的 Skill 元数据，使用智能交互模块专属工具执行业务

#### 场景： knowledge-agent 配置
- **WHEN** SubAgent knowledge-agent 被调用
- **THEN** knowledge-agent 加载 `skills/knowledge/` 下的 Skill 元数据，使用知识问答模块专属工具执行业务

### 需求：AgentState 路由字段
AgentState SHALL 包含 `module` 和 `mode` 字段，用于存储路由参数。

#### 场景： state 路由参数传递
- **WHEN** API 层格式化用户消息为 `[module:basic][mode:fast] query`
- **THEN** Main Agent 将 `module="basic"` 和 `mode="fast"` 写入 AgentState

### 需求：移除 intent_match_tool
系统 SHALL 移除 `intent_match_tool` 工具定义和 `intent_config.py` 中基于 semantic_router 的 17 路路由系统。意图识别由 SkillsMiddleware 渐进式披露机制替代。

#### 场景： intent_match_tool 不在工具列表中
- **WHEN** SubAgent 被创建并配置工具列表
- **THEN** `intent_match_tool` 不出现在任何 SubAgent 的 tools 配置中

#### 场景： intent_config.py 不被引用
- **WHEN** 项目代码被编译运行
- **THEN** 无任何代码引用 `intent_config.py` 模块或 `match_route()` 函数