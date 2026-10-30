## MODIFIED Requirements

### Requirement: SKILL.md 总纲自动注入
每个Skill SHALL 包含SKILL.md总纲。Basic QA SHALL 将登记目录保留在服务端，仅通过 search_skills 向模型返回最多五个候选的稳定 ID、名称、简介和材料版本，选择后按需加载正文并在会话中复用；初始提示 MUST NOT 注入全量目录或全部正文，不要求使用 SkillsMiddleware。使用该中间件的其他代理保留按配置目录扫描frontmatter并注入简介的机制。

#### Scenario: SkillsMiddleware 加载 Skill 元数据
- **WHEN** 使用SkillsMiddleware的代理指定技能来源目录
- **THEN** 提取各SKILL.md的name和description形成目录；该机制不作为Basic QA必须迁移框架的要求

#### Scenario: 大目录按需发现
- **WHEN** Basic QA 服务端登记 90 个可用 Skill，模型尚未搜索或加载
- **THEN** 实际模型请求不含这 90 项目录；search_skills 仅返回至多五项候选，load_skill 使用候选 ID 与版本，不开放任意文件路径

#### Scenario: 任意轮次按需加载
- **WHEN** Basic QA在第十轮才首次需要某项预设能力
- **THEN** 可发现并加载该Skill；此前轮次无需为将来准备而强制加载，之后有效引用可跨轮复用

### Requirement: SKILL_FAST.md 和 SKILL_EXPERT.md 运行时读取
SKILL.md总纲 SHALL 包含模式指引。Basic QA由受控加载器读取总纲与服务端有效模式的`references/fast.md`或`references/expert.md`，模式是会话材料引用的一部分，模型不能自报覆盖；其他代理继续读取各自声明的变体。模式文件 MUST NOT 作为独立Skill扫描，缺失不得静默回退另一模式。

#### Scenario: 快速模式读取 SKILL_FAST.md
- **WHEN** 服务端有效模式为fast并加载Skill
- **THEN** Basic QA加载总纲和references/fast.md；其他代理读取其已声明快速模式材料

#### Scenario: 专家模式读取 SKILL_EXPERT.md
- **WHEN** 服务端有效模式为expert并加载Skill
- **THEN** Basic QA加载总纲和references/expert.md；其他代理读取其已声明专家模式材料

#### Scenario: SkillsMiddleware 不扫描模式变体文件
- **WHEN** 扫描Skill目录
- **THEN** 仅将总纲登记为Skill，模式变体不重复进入能力目录

#### Scenario: 跨轮模式改变
- **WHEN** 新轮由fast改为expert
- **THEN** 已加载fast引用不被当成expert规则，按有效模式明确重载；没有材料时说明不可用，不拼接另一模式

### Requirement: Skill 三层文件目录结构
Skill目录 SHALL 以实际注册图及配置组织。Basic QA采用`src/basic_qa/skills/<slug>/SKILL.md`及references/fast.md、references/expert.md，材料存在且可读才计为可用。其他图不因本变更移动、清空或恢复目录，data-analysis继续封存；空预留目录不代表已实现能力。

#### Scenario: 预留模块 Skill 目录留白
- **WHEN** 有空的预留目录或尚未实现的图
- **THEN** 不列为可用Skill，不以此要求清空已有业务目录

#### Scenario: skills_fast 和 skills_expert 目录不再使用
- **WHEN** Basic QA解析模式材料
- **THEN** 使用所属Skill的references目录，不从旧分散目录拼接规则，本变更不删除历史文件
