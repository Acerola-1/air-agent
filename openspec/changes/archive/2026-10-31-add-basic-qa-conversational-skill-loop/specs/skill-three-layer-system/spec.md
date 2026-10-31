## MODIFIED Requirements

### Requirement: SKILL.md 总纲自动注入
每个 Skill SHALL 包含 SKILL.md 总纲文件。Basic QA SHALL 将本图经范围检查的稳定 ID、name、description、材料版本和可用状态作为简介提供给模型，不自动注入所有正文；完整规则通过受控加载获得。使用 SkillsMiddleware 的其他代理 SHALL 自动扫描其配置目录的 SKILL.md，解析 YAML frontmatter（name, description, allowed-tools），将 name + description 注入 system prompt Skills 列表。

#### Scenario: SkillsMiddleware 加载 Skill 元数据
- **WHEN** 使用 SkillsMiddleware 的 SubAgent 配置指定 `skills=["./skills/base/"]`
- **THEN** 扫描配置目录下所有子目录的 SKILL.md，提取 name 和 description，注入可用 Skill 列表；此配置示例不规定 Basic QA 当前路径

#### Scenario: Skill 元数据列表注入格式
- **WHEN** 使用 SkillsMiddleware 的 SubAgent 扫描到 air-quality-realtime 和 ranking-assessment 两个 Skill
- **THEN** system prompt 中包含相应名称、简介及受控读取位置；Basic QA 则提供稳定 ID 和版本用于加载，不要求披露文件路径

#### Scenario: Basic QA 初始目录
- **WHEN** Basic QA 开始一个新的用户轮次
- **THEN** 模型看到本次范围内 Skill 简介，可选择搜索、直接加载或不使用 Skill；目录存在不代表规则已加载

### Requirement: SKILL_FAST.md 和 SKILL_EXPERT.md 运行时读取
SKILL.md 总纲 SHALL 包含模式选择指引。Basic QA SHALL 由受控加载器读取总纲及服务端有效模式对应的 `references/fast.md` 或 `references/expert.md`，不得由模型自报模式覆盖；其他代理保留根据模式标记读取声明的变体文件的机制。SkillsMiddleware MUST NOT 将模式变体作为独立 Skill 扫描。

#### Scenario: 快速模式读取 SKILL_FAST.md
- **WHEN** 当前有效模式为 fast 并加载匹配 Skill
- **THEN** Basic QA 加载总纲和 `references/fast.md`；其他代理读取其声明的快速模式文件并执行精简流程

#### Scenario: 专家模式读取 SKILL_EXPERT.md
- **WHEN** 当前有效模式为 expert 并加载匹配 Skill
- **THEN** Basic QA 加载总纲和 `references/expert.md`；其他代理读取其声明的专家模式文件并执行深度流程

#### Scenario: SkillsMiddleware 不扫描模式变体文件
- **WHEN** 扫描 Skill 目录
- **THEN** 模式变体文件不作为独立 Skill 出现在目录中

#### Scenario: Basic QA 模式材料缺失
- **WHEN** 当前模式的必需规则文件缺失
- **THEN** 加载失败且不启用领域工具，不静默降级到另一模式

### Requirement: Skill 三层文件目录结构
Skill 目录 SHALL 按实际注册图组织。Basic QA 目录为 `src/basic_qa/skills/<slug>/SKILL.md` 及 `references/fast.md`、`references/expert.md`；材料必须存在才能计为可用。其他图以实际注册入口及现有配置为准，本变更不移动、清空或恢复其目录；data-analysis 保留封存状态。

#### Scenario: 预留模块 Skill 目录留白
- **WHEN** 存在未实现或空的预留 Skill 目录
- **THEN** 不将其列为可用业务技能；不因此清空已有实现的图目录

#### Scenario: skills_fast 和 skills_expert 目录不再使用
- **WHEN** Basic QA 解析模式材料
- **THEN** 使用所属 Skill 的 references 目录，不从旧 `skills_fast/`、`skills_expert/` 拼接来源；不在此任务中删除历史文件
