## MODIFIED Requirements

### Requirement: SKILL.md 正文模式选择指引段落
每个 SKILL.md 的 YAML frontmatter 之后的正文 SHALL 包含模式选择指引段落和通用说明段落，说明 fast/expert 材料及业务执行流程。Basic QA SHALL 通过受控加载器读取总纲与当前有效模式的 references 文件；其他采用 CompositeBackend 的代理保留 `/skills/` 路由读取机制。材料中的工具声明只约束执行范围，不恢复已退役的人员级 Skill/MCP 授权。

#### Scenario: SKILL.md 正文结构合规
- **WHEN** 读取任意 Basic QA Skill 的正文内容
- **THEN** 正文包含“模式选择指引”（列出 fast、expert 及对应文件路径）和“通用说明”（业务范围检查与数据获取流程）；不得要求旧成员工具绑定

#### Scenario: Skill 文件通过 CompositeBackend 路由读取
- **WHEN** 使用 CompositeBackend 的其他代理通过 read_file 读取配置的 `/skills/` 文件
- **THEN** 仍通过原受控路由返回正确内容；Basic QA 不需要因此增加 read_file 或任意文件访问工具

#### Scenario: Basic QA 加载完整领域规则
- **WHEN** Basic QA 成功加载一个 Skill
- **THEN** 总纲及当前模式规则共同生效，保留气象数据、数据时效、缺失说明、输出边界等既有领域要求；必需工具无法映射或必需依赖缺失时加载失败；可选依赖不可用时标记不可用并说明局限，不假装已加载完整能力

## ADDED Requirements

### Requirement: 基础问答工具依赖分类
Basic QA Skill SHALL 将非绘图工具声明为必需依赖、绘图工具声明为可选依赖，覆盖总纲和模式规则中实际引用的工具。绘图工具不可用 SHALL 允许加载正文规则并跳过绘图步骤；不得因此阻断正文或冒充已生成图表。必需工具不可用与实际调用后返回无数据 MUST 分别处理，后者仍遵守现有数据缺失规则。

#### Scenario: 图表工具未配置
- **WHEN** 所有必需工具可用，但某个绘图工具未登记或不可用
- **THEN** 正文规则可成功加载，工具集合不包含该绘图工具，回答说明图表未生成，不执行不存在的调用

#### Scenario: 主数据工具可用但没有结果
- **WHEN** 必需主数据工具已可用且成功加载，执行后返回空数据
- **THEN** 按 Skill 原有缺失说明回答，不撤回为加载失败，不用其他未经验证的数据源补造结果
