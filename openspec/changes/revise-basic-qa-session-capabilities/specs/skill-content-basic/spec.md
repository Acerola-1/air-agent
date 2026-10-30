## MODIFIED Requirements

### Requirement: SKILL.md 正文模式选择指引段落
每个SKILL.md正文 SHALL 包含模式选择指引和通用说明，列出fast/expert材料及业务数据流程。Basic QA通过受控加载器恢复总纲和当前模式规则，支持多个Skill组合但不得静默丢弃各自适用的领域要求；其他采用CompositeBackend的代理保留既有材料路由。材料声明不恢复人员级Skill/MCP权限，不要求独立工具伪造所属Skill。

#### Scenario: SKILL.md 正文结构合规
- **WHEN** 读取任意Basic QA Skill正文
- **THEN** 含模式指引和通用说明，明确材料位置、数据流程及局限；不要求旧成员工具绑定

#### Scenario: Skill 文件通过 CompositeBackend 路由读取
- **WHEN** 使用CompositeBackend的代理读取其受控Skill材料
- **THEN** 按原路由获得正确内容；Basic QA以受控ID加载实现对应能力，无需新增任意文件工具

#### Scenario: 激活多个Skill
- **WHEN** 一个任务组合两个适用Skill
- **THEN** 模型收到两个Skill的完整当前模式规则，适用范围分别明确；规则冲突不能靠覆盖先前字符串隐藏，无法协调时澄清或分别处理

## ADDED Requirements

### Requirement: 基础问答工具依赖分类
Basic QA Skill SHALL 将非绘图工具声明为必需、绘图工具声明为可选，覆盖总纲与模式引用。缺失必需依赖时目标Skill不可作为完整已激活能力；可选绘图缺失不阻断正文规则。工具可用但调用无结果仍按原Skill缺失规范回答。独立工具只能补足未覆盖需求，不能伪称已完成缺依赖的完整Skill流程。

#### Scenario: 图表不可用
- **WHEN** 必需工具齐全，绘图工具未登记或不可用
- **THEN** 正文规则可激活，跳过不可用绘图，不显示不存在的schema或宣称图表已生成

#### Scenario: 业务数据为空
- **WHEN** 已激活Skill的主工具调用返回空
- **THEN** 按原领域规则说明缺失，不倒退成加载失败，不编造数值

#### Scenario: 必需依赖缺失
- **WHEN** 某Skill缺少必需工具，而其他有效能力仍存在
- **THEN** 仅目标加载失败，其他能力保留；可说明局限或处理未覆盖部分，不通过无来源替代工具冒充原流程已完成
