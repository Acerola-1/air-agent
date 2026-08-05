## MODIFIED Requirements

### Requirement: SKILL.md 正文模式选择指引段落
每个 SKILL.md 的 YAML frontmatter 之后的正文 SHALL 包含固定的模式选择指引段落和通用说明段落，引导 SubAgent 根据 mode 标记读取对应变体文件。Skill 文件 SHALL 通过 CompositeBackend 的 `/skills/` 路由从 FilesystemBackend 读取，读取行为与之前一致。

#### Scenario: SKILL.md 正文结构合规
- **WHEN** 读取任意 base 模块 SKILL.md 的正文内容
- **THEN** 正文包含"模式选择指引"段落（列出 fast 和 expert 两个模式及对应文件路径）和"通用说明"段落（权限校验和数据获取刚性流程）

#### Scenario: Skill 文件通过 CompositeBackend 路由读取
- **WHEN** agent 通过 read_file 工具读取 `/skills/base/air-quality-realtime/SKILL.md`
- **THEN** CompositeBackend 将 `/skills/` 前缀的请求路由到 FilesystemBackend，文件内容正确返回，与之前直接使用 FilesystemBackend 的行为一致
