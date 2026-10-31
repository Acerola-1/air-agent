# Spec Delta

## MODIFIED Requirements

### Requirement: CompositeBackend 路由配置
采用 Deep Agents 文件后端的业务图 SHALL 保持其现有 CompositeBackend 配置和状态／只读材料路由。Basic QA 通过登记目录的受控加载器恢复 Skill 与单一 `references/rules.md`，不要求使用 CompositeBackend、不开放任意文件工具；本变更不把其他原生 LangGraph 图改回 Deep Agents，不恢复已退役图或改变数据分析封存范围。

#### Scenario: basic-qa graph 使用 CompositeBackend
- **WHEN** Basic QA 初始化
- **THEN** 不要求 CompositeBackend，登记加载器提供材料；图导入不产生模型、embedding 或 MCP 网络预热

#### Scenario: data-analysis graph 使用 CompositeBackend
- **WHEN** 检查 data-analysis 现有实现
- **THEN** 保留其实际后端和菜单约束；存在 CompositeBackend 路径时保持原路由，不因历史示例强行迁移图架构

### Requirement: Skill 文件从磁盘读取
采用 CompositeBackend 的代理 SHALL 将配置的 Skill 材料操作路由到受控 FilesystemBackend。Basic QA SHALL 通过已登记 Skill ID 读取总纲及单一 `references/rules.md`，完整规则按实际模型请求恢复，不接受模型自由路径；两种方式均不能用文件内容扩大业务和受理范围。

#### Scenario: 读取 Skill SKILL.md 文件
- **WHEN** 使用 CompositeBackend 的代理读取配置的 Skill 总纲
- **THEN** 受控路由返回正确文件，不把历史目录当作所有图现行路径

#### Scenario: SkillsMiddleware 加载 Skill 列表
- **WHEN** 使用 SkillsMiddleware 的代理扫描其配置目录
- **THEN** 以实际材料形成名称／简介列表；Basic QA 以受控目录提供等效发现入口，不声称其已经使用该中间件

#### Scenario: Basic QA 拒绝任意路径
- **WHEN** 加载参数包含路径穿越、跨图材料或未登记 ID
- **THEN** 不读取任意文件、不激活目标，其他有效会话能力不被无关失败清空
