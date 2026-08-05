## 新增需求

### 需求：图本地 Skill 发现入口工具

系统 SHALL 为业务 Graph 提供一个 `find_skill` 工具，将当前用户请求与该 Graph 的本地 Skill 进行语义匹配。

#### 场景：匹配 Skill 返回元数据和允许的工具

- **WHEN** 模型使用匹配本地 Skill（超过配置阈值）的用户请求调用 `find_skill`
- **THEN** 工具返回 `matched=true`、Skill 名称、Skill 路径、描述、相似度分数和 Skill 的 `allowed_tools`
- **AND** 所选 Skill 元数据存储在 Agent 状态中供后续中间件使用

#### 场景：无 Skill 匹配时业务工具保持隐藏

- **WHEN** 模型调用 `find_skill` 且没有 Skill 通过配置的阈值
- **THEN** 工具返回 `matched=false`
- **AND** 不存储所选 Skill 的 allowed-tools 用于业务 Tool 披露

### 需求：Skill 元数据包含允许的工具

系统 SHALL 从每个本地 `SKILL.md` frontmatter 解析 `allowed-tools` 并将其作为结构化 Skill 元数据暴露。

#### 场景：空格分隔的 allowed-tools 字段被解析

- **WHEN** `SKILL.md` frontmatter 包含 `allowed-tools: helper_getLatestTime base_city_air_concentration`
- **THEN** 加载的 Skill 元数据包含 `allowed_tools=["helper_getLatestTime", "base_city_air_concentration"]`

#### 场景：缺失 allowed-tools 默认为空列表

- **WHEN** `SKILL.md` frontmatter 省略了 `allowed-tools`
- **THEN** 加载的 Skill 元数据包含空的 `allowed_tools` 列表

### 需求：业务工具预注册以实现动态披露

系统 SHALL 在运行时过滤发生之前注册所有可能被 Skill 披露的业务工具。

#### 场景：业务工具在动态披露后可执行

- **WHEN** 一个 Skill 被选中且其允许的业务工具之一被披露给模型
- **THEN** ToolNode 可以执行该工具，因为它已通过 Graph 或中间件工具注册表注册

### 需求：模型可见工具按所选 Skill 过滤

系统 SHALL 根据当前所选 Skill 的 `allowed_tools` 过滤模型可见的业务工具。

#### 场景：Skill 选择前仅入口和系统工具可见

- **WHEN** 状态中没有选定的 Skill
- **THEN** 业务工具对模型请求隐藏
- **AND** `find_skill` 和框架/系统工具保持可见

#### 场景：Skill 选择后仅允许的业务工具可见

- **WHEN** 状态包含 `selected_skill_allowed_tools`
- **THEN** 模型请求仅包含名称在该列表中的业务工具
- **AND** `find_skill` 和框架/系统工具保持可见

#### 场景：未列出的业务工具保持隐藏

- **WHEN** 一个已注册的业务工具不在所选 Skill 的 `allowed_tools` 列表中
- **THEN** 该工具不包含在模型可见的 `request.tools` 中

### 需求：Basic QA 图试点渐进式披露

`basic-qa` Graph SHALL 使用渐进式 Skill 和 Tool 披露作为第一个业务 Graph 试点。

#### 场景：Basic QA 声明 Skill 入口工具

- **WHEN** `basic-qa` Graph 被组装
- **THEN** 其声明的业务入口工具集包含 `find_skill`
- **AND** 其现有业务工具通过中间件或等效注册表注册以实现动态披露

#### 场景：Basic QA 提示词要求 Skill 发现

- **WHEN** `basic-qa` Graph 收到业务空气质量请求
- **THEN** 其指令告诉模型在使用业务数据工具之前调用 `find_skill`
