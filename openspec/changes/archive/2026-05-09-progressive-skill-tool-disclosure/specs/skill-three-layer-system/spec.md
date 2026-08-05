## 修改需求

### 需求：每个业务 Graph 拥有自己的 Agent 配置

每个业务 Graph SHALL 是一个独立的 DeepAgent 实例，拥有自己的 `system_prompt`、skills、tools、middleware、permissions、model selection、internal planning rules 和 final output contract。业务 Graph MAY 在 `tools=` 中仅声明 Skill 入口工具，同时通过中间件注册其业务工具以实现动态披露。

#### 场景：Basic QA 图加载 basic QA skills

- **WHEN** `basic-qa` 图被构建
- **THEN** 它加载 basic-qa 业务 skill 目录，不依赖由父 Agent 选择的 SubAgent skill 目录

#### 场景：业务工具可通过中间件注册

- **WHEN** 业务 Graph 使用渐进式 Skill Tool 披露
- **THEN** 图可以通过中间件注册业务工具，同时在 Skill 选择前仅向模型暴露入口/系统工具

#### 场景：Deep research 图使用自己的配置

- **WHEN** `deep-research` 图被构建
- **THEN** 它使用为该图配置的 deep research 提示词、skill 目录、工具策略和输出期望
