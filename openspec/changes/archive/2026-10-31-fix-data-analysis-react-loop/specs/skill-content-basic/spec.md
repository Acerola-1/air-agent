## MODIFIED Requirements

### Requirement: air-quality-realtime Skill 总纲
系统 SHALL 在 `resolve_skill` 节点中加载 SKILL.md frontmatter 之后的正文，与 `references/fast.md` 或 `references/expert.md` 合并后写入 `skill_rules_content`。合并格式为 `{SKILL.md 正文}\n\n---\n\n{references 内容}`。

#### Scenario: Skill 匹配成功时加载 SKILL.md 正文 + references
- **WHEN** `match_menu_skill` 返回匹配结果
- **THEN** 系统 SHALL 读取 SKILL.md frontmatter 之后的正文
- **AND** 系统 SHALL 根据 mode 读取 `references/fast.md` 或 `references/expert.md`
- **AND** 系统 SHALL 将两者以分隔符合并后写入 `skill_rules_content`

#### Scenario: SKILL.md 无正文内容
- **WHEN** SKILL.md frontmatter 之后无正文内容
- **THEN** 系统 SHALL 仅将 references 文件内容写入 `skill_rules_content`

## ADDED Requirements

### Requirement: Skill 匹配失败时降级系统提示词
系统 SHALL 在 `resolve_skill` 匹配失败时，将 `prompt_builder.SYSTEM_PROMPT`（含角色职责、核心分析原则、输出要求）作为 `skill_rules_content` 写入 state，确保模型有基本行为指引。

#### Scenario: 菜单名未匹配到 Skill
- **WHEN** `match_menu_skill` 返回 None
- **THEN** 系统 SHALL 将 `prompt_builder.SYSTEM_PROMPT` 写入 `skill_rules_content`
- **AND** `selected_skill_allowed_tools` SHALL 为空列表
- **AND** `selected_skill` SHALL 为 None

#### Scenario: 菜单名为空或特殊值
- **WHEN** 前端传入的 menu_name 为空、"none"、"null" 或 "default"
- **THEN** `match_menu_skill` SHALL 返回 None
- **AND** 系统 SHALL 将 `prompt_builder.SYSTEM_PROMPT` 写入 `skill_rules_content`
