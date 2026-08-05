## 修改需求

### 需求：SKILL.md YAML frontmatter 标准格式

SKILL.md SHALL 包含 YAML frontmatter，其中包含必填字段 `name`、`description` 和 `allowed-tools`。`allowed-tools` 字段 SHALL 被解析为结构化元数据，并在启用渐进式披露时用作运行时业务 Tool 可见性契约。

#### 场景：SKILL.md frontmatter 解析

- **WHEN** 系统读取 `SKILL.md` YAML frontmatter
- **THEN** 成功提取 `name`、`description` 和 `allowed-tools`
- **AND** 将 `allowed-tools` 存储为该 Skill 可用的工具名称列表

#### 场景：allowed-tools 约束业务 Tool 可见性

- **WHEN** 渐进式 Skill Tool 披露已启用且一个 Skill 被选中
- **THEN** 只有该 Skill 的 `allowed-tools` 中列出的业务工具对模型可见
- **AND** DeepAgents 所需的框架/系统工具保持可用
