## ADDED Requirements

### Requirement: SKILL.md 总纲自动注入
每个 Skill SHALL 包含 SKILL.md 总纲文件，SkillsMiddleware SHALL 自动扫描 skills 目录下的 SKILL.md，解析 YAML frontmatter（name, description, allowed-tools），将 name + description 注入到 SubAgent 的 system prompt Skills 列表中。

#### Scenario: SkillsMiddleware 加载 Skill 元数据
- **WHEN** SubAgent 配置指定 `skills=["./skills/base/"]`
- **THEN** SkillsMiddleware 扫描 `./skills/base/` 下所有子目录的 SKILL.md，提取每个 Skill 的 name 和 description，注入到 system prompt 作为可用 Skill 列表

#### Scenario: Skill 元数据列表注入格式
- **WHEN** SkillsMiddleware 扫描到 air-quality-realtime 和 ranking-assessment 两个 Skill
- **THEN** system prompt 中包含 Skills 列表：`- air-quality-realtime: 查询城市空气质量实况数据... Read ./skills/base/air-quality-realtime/SKILL.md for full instructions`

### Requirement: SKILL_FAST.md 和 SKILL_EXPERT.md 运行时读取
SKILL.md 总纲 SHALL 包含模式选择指引段落，引导 SubAgent 根据 `[mode:XXX]` 标记用 `read_file` 工具主动读取 SKILL_FAST.md 或 SKILL_EXPERT.md。SkillsMiddleware SHALL NOT 扫描 SKILL_FAST.md 和 SKILL_EXPERT.md。

#### Scenario: 快速模式读取 SKILL_FAST.md
- **WHEN** SubAgent 收到 `[mode:fast]` 标记并识别匹配 Skill
- **THEN** SubAgent 读取 SKILL.md 总纲 → 根据指引读取 SKILL_FAST.md → 执行精简查询流程

#### Scenario: 专家模式读取 SKILL_EXPERT.md
- **WHEN** SubAgent 收到 `[mode:expert]` 标记并识别匹配 Skill
- **THEN** SubAgent 读取 SKILL.md 总纲 → 根据指引读取 SKILL_EXPERT.md → 执行深度分析流程

#### Scenario: SkillsMiddleware 不扫描模式变体文件
- **WHEN** SkillsMiddleware 扫描 skills 目录
- **THEN** SkillsMiddleware 仅扫描 SKILL.md 文件，不扫描 SKILL_FAST.md 和 SKILL_EXPERT.md

### Requirement: SKILL.md YAML frontmatter 标准格式
SKILL.md SHALL 包含 YAML frontmatter，包含以下必填字段：name（小写字母+连字符）、description（触发关键词+典型场景）、version、allowed-tools（工具白名单）。可包含可选字段：metadata（category, question_ids, requires_weather, prompt_fast, prompt_expert）。

#### Scenario: SKILL.md frontmatter 解析
- **WHEN** SkillsMiddleware 读取 SKILL.md 的 YAML frontmatter
- **THEN** 成功提取 `name`、`description`、`allowed-tools` 字段，并将 `prompt_fast` 和 `prompt_expert` 路径存储为模式变体指引

### Requirement: Skill 三层文件目录结构
Skill 目录 SHALL 按模块组织：`skills/base/`（7 个子目录）、`skills/intelligent-analysis/`、`skills/data-analysis/`、`skills/intelligent-report/`、`skills/deep-research/`、`skills/intelligent-tracing/`。base 模块的每个 Skill 子目录 SHALL 包含 SKILL.md 和 references 目录；其余预留模块 skills 目录 SHALL 为空。

#### Scenario: 预留模块 Skill 目录留白
- **WHEN** 预留模块被创建
- **THEN** `skills/intelligent-analysis/`、`skills/data-analysis/`、`skills/intelligent-report/`、`skills/deep-research/`、`skills/intelligent-tracing/` 目录下无 SKILL.md 文件

#### Scenario: skills_fast 和 skills_expert 目录不再使用
- **WHEN** 三层文件体系生效后
- **THEN** `skills_fast/` 和 `skills_expert/` 目录下的 Skill 文件被迁移到对应的模块目录，原目录可清理
