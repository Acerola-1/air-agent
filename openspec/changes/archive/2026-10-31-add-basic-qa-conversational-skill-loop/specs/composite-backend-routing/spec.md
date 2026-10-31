## MODIFIED Requirements

### Requirement: CompositeBackend 路由配置
采用 DeepAgents 文件后端的业务 graph SHALL 使用其配置的 CompositeBackend，默认状态存储与 `/skills/` 只读材料路由分离。Basic QA SHALL 使用受控 Skill 加载器读取登记材料，不要求配置 CompositeBackend，不因此提供任意文件访问。本变更不迁移其他图的实际后端；历史图清单不用于恢复已移除的业务或更改封存范围。

#### Scenario: basic-qa graph 使用 CompositeBackend
- **WHEN** basic-qa graph 初始化
- **THEN** 无需 CompositeBackend，Skill 材料由受控加载器按登记目录解析；初始化不触发模型、embedding 或 MCP 网络请求

#### Scenario: data-analysis graph 使用 CompositeBackend
- **WHEN** 检查 data-analysis 的已有文件后端行为
- **THEN** 若其路径采用 CompositeBackend，保留既有状态/材料路由；本变更不将当前确定性图迁移回 DeepAgents，也不解除菜单约束

### Requirement: Skill 文件从磁盘读取
采用 CompositeBackend 的代理 SHALL 将 `/skills/` 文件操作路由到受控 FilesystemBackend，保证总纲及模式材料正确读取。Basic QA SHALL 通过受控加载器读取已登记 Skill 的总纲与模式文件，拒绝模型提供任意路径；两种方式均不得以文件内容扩大业务和已受理范围。

#### Scenario: 读取 Skill SKILL.md 文件
- **WHEN** 采用 CompositeBackend 的代理读取其配置的 `/skills/` 总纲
- **THEN** 按受控路由返回对应材料，不将历史示例路径作为所有图的现行目录

#### Scenario: SkillsMiddleware 加载 Skill 列表
- **WHEN** 使用 SkillsMiddleware 的代理初始化其配置目录
- **THEN** 可用 Skill 的 name 和 description 出现在其 Skills 列表；Basic QA 使用受控元数据目录实现对应发现能力

#### Scenario: Basic QA 拒绝任意路径
- **WHEN** Basic QA 的加载请求包含未登记 ID 或路径穿越形式
- **THEN** 不读取目标文件、不激活工具，返回加载失败
