## ADDED Requirements

### Requirement: Main Agent 路由调度
系统 SHALL 提供 Main Agent 作为路由调度器，接收格式为 `[module:XXX][mode:XXX] 实际问题` 的用户消息，解析 module 标记路由到对应 SubAgent，将 mode 标记透传到 task() 的 description 参数。

#### Scenario: 基础问题模块路由
- **WHEN** 用户消息为 `[module:base][mode:fast] 北京空气质量怎么样`
- **THEN** Main Agent 调用 `task(subagent_type="base-agent", description="[mode:fast] 北京空气质量怎么样")`

#### Scenario: 智能分析模块路由
- **WHEN** 用户消息为 `[module:intelligent-analysis][mode:expert] 分析绍兴市小时播报数据`
- **THEN** Main Agent 调用 `task(subagent_type="intelligent-analysis-agent", description="[mode:expert] 分析绍兴市小时播报数据")`

#### Scenario: 未指定 module 时智能判断
- **WHEN** 用户消息为 `[mode:fast] 北京空气质量怎么样`（无 module 标记）
- **THEN** Main Agent 根据问题内容智能判断 module 并路由到对应 SubAgent

#### Scenario: 未指定 mode 时默认 fast
- **WHEN** 用户消息为 `[module:base] 北京空气质量怎么样`（无 mode 标记）
- **THEN** Main Agent 默认注入 `[mode:fast]` 到 description 参数

### Requirement: 6 SubAgent 模块化定义
系统 SHALL 定义 6 个 SubAgent：base-agent、intelligent-analysis-agent、data-analysis-agent、intelligent-report-agent、deep-research-agent、intelligent-tracing-agent，使用统一模型。

#### Scenario: base-agent 配置
- **WHEN** SubAgent base-agent 被调用
- **THEN** base-agent 加载 `skills/base/` 下的 Skill 元数据，使用基础问题模块专属工具（query_city_daily_air_data 等）执行业务

#### Scenario: 预留模块配置
- **WHEN** intelligent-analysis-agent、data-analysis-agent、intelligent-report-agent、deep-research-agent、intelligent-tracing-agent 被创建
- **THEN** 这些预留模块的 system prompt 为空，skills 目录为空，暂不绑定业务工具

#### Scenario: 新模块英文命名
- **WHEN** 模块配置被读取
- **THEN** 智能分析、数据分析、智能报告、深入研究、智能溯源分别命名为 intelligent-analysis-agent、data-analysis-agent、intelligent-report-agent、deep-research-agent、intelligent-tracing-agent

### Requirement: AgentState 路由字段
AgentState SHALL 包含 `module` 和 `mode` 字段，用于存储路由参数。

#### Scenario: state 路由参数传递
- **WHEN** API 层格式化用户消息为 `[module:base][mode:fast] query`
- **THEN** Main Agent 将 `module="base"` 和 `mode="fast"` 写入 AgentState

### Requirement: 移除 intent_match_tool
系统 SHALL 移除 `intent_match_tool` 工具定义和 `intent_config.py` 中基于 semantic_router 的 17 路路由系统。意图识别由 SkillsMiddleware 渐进式披露机制替代。

#### Scenario: intent_match_tool 不在工具列表中
- **WHEN** SubAgent 被创建并配置工具列表
- **THEN** `intent_match_tool` 不出现在任何 SubAgent 的 tools 配置中

#### Scenario: intent_config.py 不被引用
- **WHEN** 项目代码被编译运行
- **THEN** 无任何代码引用 `intent_config.py` 模块或 `match_route()` 函数
