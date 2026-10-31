# Spec Delta

## MODIFIED Requirements

### Requirement: SKILL.md YAML frontmatter 标准格式
SKILL.md SHALL 包含 YAML frontmatter，包含 name、description、allowed-tools、required-tools、optional-tools。required-tools MUST NOT 包含 `chart_*`，optional-tools MUST 为空或不存在；frontmatter MUST NOT 再声明 `prompt_fast`、`prompt_expert` 或任何模式变体路径。

#### Scenario: SKILL.md frontmatter 解析
- **WHEN** 加载器读取 SKILL.md 的 YAML frontmatter
- **THEN** 成功提取 name、description、allowed-tools、required-tools、optional-tools，且不要求任何模式字段

#### Scenario: 图表工具声明被拒绝
- **WHEN** SKILL.md 的 required-tools 或 optional-tools 声明 `chart_*`
- **THEN** 材料校验失败并说明图表工具已退役，不得继续加载该 Skill

#### Scenario: 模式变体字段被忽略
- **WHEN** 旧 SKILL.md 仍包含 `prompt_fast` 或 `prompt_expert`
- **THEN** 加载器忽略这些字段，不据此拼接另一份规则文件

## ADDED Requirements

### Requirement: Skill 三层文件目录结构与单一规则文件
Skill 目录 SHALL 以实际注册图及配置组织。Basic QA 采用 `src/basic_qa/skills/<slug>/SKILL.md` 与 `references/rules.md`，每个 Skill 只有一份规则文件，MUST NOT 存在 `references/fast.md` 或 `references/expert.md`；材料存在且可读才计为可用。其他图不因本变更移动、清空或恢复目录，data-analysis 继续封存；空预留目录不代表已实现能力。

#### Scenario: Basic QA 单一规则文件
- **WHEN** 检查任一 Basic QA Skill 目录
- **THEN** 存在 SKILL.md 与 references/rules.md，不存在 references/fast.md 与 references/expert.md

#### Scenario: 空目录或缺失规则
- **WHEN** Skill 目录缺少 SKILL.md 或 references/rules.md
- **THEN** 该 Skill 不计为可用，加载失败关闭，不回退到其他文件或其他模式

#### Scenario: data-analysis 保持封存
- **WHEN** 检查 data-analysis 的 Skill 目录
- **THEN** 其现有目录与文件不被本变更移动或删除

#### Scenario: 预留模块 Skill 目录留白
- **WHEN** 有空的预留目录或尚未实现的图
- **THEN** 不列为可用 Skill，不以此要求清空已有业务目录

### Requirement: 单一 references 规则文件运行时读取
Basic QA 加载 Skill 时 SHALL 读取 SKILL.md 总纲与同目录 `references/rules.md`，MUST NOT 按模式选择文件；文件缺失、不可读或读取期间被修改时 SHALL 失败关闭并说明材料不可用。

#### Scenario: 加载 Skill
- **WHEN** 模型调用 load_skill 并命中某个 Skill
- **THEN** 服务端读取 SKILL.md 与 references/rules.md，返回合并后的规则与工具范围

#### Scenario: 规则文件缺失
- **WHEN** Skill 目录缺少 references/rules.md
- **THEN** 加载返回材料不可用，不回退到 fast.md、expert.md 或其他 Skill

#### Scenario: 跨轮复用
- **WHEN** 同一线程后续轮次继续使用已加载 Skill
- **THEN** 使用同一份 rules.md 与同一材料版本，不因不存在的模式参数重载

## REMOVED Requirements

### Requirement: SKILL_FAST.md 和 SKILL_EXPERT.md 运行时读取
**Reason**: Basic QA 不再提供快速/专家模式，双份规则文件已合并为单一 `references/rules.md`。
**Migration**: 删除 `references/fast.md` 与 `references/expert.md`，将内容合并进 `references/rules.md`；加载器改为只读该文件。

### Requirement: Skill 三层文件目录结构
**Reason**: 原需求要求 Basic QA 同时提供 `references/fast.md` 与 `references/expert.md`，模式删除后该结构不再成立。
**Migration**: 使用新增的“Skill 三层文件目录结构与单一规则文件”，每个 Skill 只保留 `references/rules.md`，并从旧分散目录迁出。
