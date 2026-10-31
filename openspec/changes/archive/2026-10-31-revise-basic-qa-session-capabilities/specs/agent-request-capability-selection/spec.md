## REMOVED Requirements

### Requirement: 自动默认与显式选择交互
**Reason**: 用户已撤回事先指定 Skill，不能继续保留 Skill auto/explicit 协议与运行分支。
**Migration**: 新请求使用 agent.capability-selection.v2，只保留 MCP 范围。旧 Skill 字段被明确拒绝，已冻结历史选择不改写且不再用于 retry/resume；新请求重新按需发现能力。

## ADDED Requirements

### Requirement: 模型发现与 MCP 受理范围
系统 SHALL 由模型优先 search_skills 发现新业务能力，从候选中 load_skill；不接受请求事先指定 Skill，不向模型注入全量 Skill 目录。已有有效引用可跨轮复用。MCP auto/allowlist 仍独立限定受理范围；指定空集合表示不使用 MCP。

#### Scenario: 旧客户端指定 Skill
- **WHEN** 请求含 skill 对象或 capability_skill_mode、capability_skill_id、capability_material_version
- **THEN** 明确拒绝，不静默忽略或恢复旧约束，模型和业务工具零外呼

#### Scenario: 自然语言提及 Skill
- **WHEN** 用户在消息中提及某项专业能力
- **THEN** 将其用于搜索并选择候选，或复用已有效加载引用，不生成新的受理 Skill 限制

#### Scenario: MCP 空集合
- **WHEN** 受理 MCP allowlist 为空
- **THEN** 不披露或执行任何 MCP 工具，本地工具按既有规则运行

## MODIFIED Requirements

### Requirement: 服务端验证与依赖冲突
服务端 SHALL 验证实际 Graph 的业务权限、MCP 范围、目录／连接版本与工具长度。Skill 的材料、模式与工具依赖由按需加载器核验，失败不扩大范围。模型加载不能修改受理选择，执行核对本次工具定义与实时授权，不因 Skill 归属或加载记录增加权限条件。

#### Scenario: Skill 所需工具不可用
- **WHEN** 模型加载的 Skill 必需工具被当前 MCP 范围排除
- **THEN** 加载明确失败且不扩大许可，其他有效能力保留

#### Scenario: 菜单策略故障
- **WHEN** 数据分析菜单策略加载或校验发生异常
- **THEN** 受理失败关闭且零外呼，正常未匹配与策略故障分别处理

### Requirement: 重试重新生成与恢复继承
重试与恢复 SHALL 继承新协议受理的 MCP 范围并复查实时业务／连接许可，不改变不可变摘要。撤回协议的历史选择保留为事实，但拒绝继续执行；重新生成或新请求按新协议受理。已加载能力按本次规则与 schema 重新核验，不每轮清空。

#### Scenario: 旧选择恢复
- **WHEN** retry/resume 的来源选择摘要版本低于 3
- **THEN** 明确拒绝并要求发起新请求，不改写历史或静默解除旧限制

#### Scenario: 新选择恢复
- **WHEN** 新协议运行暂停后恢复，MCP 范围和业务许可仍有效
- **THEN** 继承相同范围并允许恢复，变更范围或撤权时拒绝

#### Scenario: 同能力追问
- **WHEN** 新轮所需 Skill 已有效加载且仍获许可
- **THEN** 直接复用其规则和工具，无重复搜索或加载
