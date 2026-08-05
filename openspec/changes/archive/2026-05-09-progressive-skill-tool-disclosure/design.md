## 背景

仓库已经使用了顶层业务 Graph。每个 Graph 拥有一个本地 `skills/` 目录和广泛的 MCP/本地工具列表。DeepAgents 默认的 SkillsMiddleware 扫描 Skill 元数据并将名称、描述、路径和 `allowed-tools` 注入提示词，但它并不强制执行这些工具约束。

仓库也已经包含 `src/common/skill_router.py`，它可以从 Skill 描述构建图本地的语义路由器。这为实现提供了良好的起点：新的变更应该扩展该路由器并将其连接到 Agent 循环，而不是引入一个独立的路由机制。

该计划受到 `docs/mashibing/PythonProject-test/src/agent/skills_agent.py` 的启发：Agent 可以只声明一个入口工具，而中间件预注册更大的业务工具注册表并在模型调用前动态过滤 `request.tools`。

## 目标 / 非目标

**目标：**

- 通过 `find_skill` 工具使 Skill 发现显式化。
- 返回并持久化 `allowed_tools` 作为匹配 Skill 结果的一部分。
- 通过中间件预注册业务工具，使动态披露的工具可执行。
- 根据所选 Skill 的 `allowed-tools` 过滤模型可见的业务工具。
- 保留 DeepAgents 内置工具和必需的系统工具。
- 在 `basic-qa` 中试点该行为并进行针对性测试。

**非目标：**

- 不在此变更中重新设计所有 Skill 内容。
- 不在 `basic-qa` 试点证明稳定之前将新流程应用于每个业务 Graph。
- 除非实现证明与新的流程冲突，否则不移除 DeepAgents 内置的 SkillsMiddleware。
- 不在第一次迭代中支持任意多 Skill 累积。
- 不改变面向调用者的 LangGraph API 或 Graph 名称。

## 决策

### 使用 `find_skill` 而非仅提示词的 Skill 发现

`find_skill` 将调用图本地的语义路由器并返回结构化的匹配结果。这使得所选的 Skill、路径、分数和允许的工具在工具输出和状态中可观察。

考虑过的替代方案：仅保留默认的 SkillsMiddleware 并加强提示词指令。这使失败模式保持不变：模型仍然需要自行发现并遵守 Skill 约束。

### 扩展 `skill_router.py` 作为元数据来源

`skill_router.py` 除了解析 name/description/path 外，还应解析 `allowed-tools`。它应使用足够健壮的 YAML frontmatter 解析来处理当前的 Skill 文件，并保留现有的规范化和缓存行为。

考虑过的替代方案：在 `find_skill` 内部独立解析 Skill 文件。那会重复元数据解析并创建两个事实来源。

### 保持业务工具已注册但隐藏

`create_deep_agent(tools=[find_skill], ...)` 保持公共 Agent 声明精简。注册中间件将通过 `middleware.tools` 暴露所有图业务工具，以便 LangChain ToolNode 可以执行它们。后续的过滤中间件控制这些已注册工具中哪些对模型可见。

考虑过的替代方案：在 `tools=` 中传入所有工具并稍后过滤。这在技术上可行，但它模糊了预期的入口工具架构，使 Graph 组装不够显式。

### 第一次迭代采用单一活跃 Skill

第一个版本在状态中存储一个选定的 Skill。新的业务问题可以再次调用 `find_skill` 并替换该选择。

考虑过的替代方案：累积多个已加载的 Skill。这有将可见工具集重新增长回原始问题的风险，并使调试复杂化。

### 业务工具过滤必须保留内置工具

过滤中间件只会隐藏不在 `selected_skill_allowed_tools` 中的已注册业务工具。它必须保留 `find_skill`、显式配置的系统工具以及 DeepAgents 内置工具（如文件读取和待办工具）。

## 风险 / 权衡

- [风险] `allowed-tools` 包含拼写错误或未加载的工具。→ 缓解措施：添加验证/日志记录和缺失名称的单元测试。
- [风险] 语义匹配选择了错误的 Skill。→ 缓解措施：保留分数阈值、记录分数，并允许重新调用 `find_skill`。
- [风险] 在 Skill 选择前隐藏业务工具会阻止合法的回退工作流。→ 缓解措施：保持非业务/系统工具可见，并从 `find_skill` 返回清晰的未匹配输出。
- [风险] 默认 SkillsMiddleware 仍然注入所有 Skill 元数据并鼓励模型端发现。→ 缓解措施：更新 Graph 提示词以要求 `find_skill`；如果试点后需要，引入更精简的 Skill 元数据中间件。
- [风险] 中间件顺序影响可见工具。→ 缓解措施：将注册中间件放在过滤中间件之前，并使用合成工具添加单元测试。

## 迁移计划

1. 实现共享路由、状态、工具和中间件支持。
2. 将 `basic-qa` 配置为使用 `find_skill` 作为声明的业务入口工具，同时通过中间件注册现有的 basic-qa 业务工具。
3. 运行解析、状态更新和过滤的单元测试。
4. 对代表性的 basic-qa 问题进行冒烟测试。
5. 如果稳定，在后续变更中对其他业务 Graph 重复该模式。

回滚策略：将 `basic-qa` 恢复为直接将完整的已解析工具列表传递给 `create_deep_agent`，并从其链中移除新的中间件。

## 待解决问题

- `find_skill` 是否应返回完整的 Skill 内容还是仅返回元数据和路径。第一次实现应返回元数据/路径，并依赖 DeepAgents 文件工具来读取 Skill。
- 默认 SkillsMiddleware 是否应在试点期间保持启用。第一次实现应保留它，除非它与 `find_skill` 行为冲突。
