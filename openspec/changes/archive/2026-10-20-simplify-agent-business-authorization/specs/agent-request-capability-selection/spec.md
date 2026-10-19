## MODIFIED Requirements

### Requirement: 服务端验证与依赖冲突
服务端 SHALL 验证实际Graph的业务权限、能力归属、目录/材料版本、连接状态、选择长度及Skill工具依赖，不再要求普通Skill/MCP独立人员授权。未知、不可用、路径伪造、跨图选择在外呼前拒绝；显式Skill不被静默替换，也不因依赖缺失扩大用户选择。

#### Scenario: Skill依赖被排除
- **WHEN** 显式Skill必需的MCP工具不在本次指定集合
- **THEN** 返回中文冲突说明，不启动模型或工具；依赖声明不自动授予权限或扩大选择

#### Scenario: 菜单确定性分析
- **WHEN** data-analysis当前菜单映射S1，用户提交S2
- **THEN** 拒绝不一致选择；一致S1且业务获准、依赖满足时可执行

#### Scenario: 无Skill的自动调用
- **WHEN** 用户有业务资格，使用自动模式且模型直接调用业务内有效MCP
- **THEN** 无需先加载Skill，也无需成员工具绑定，仍按已受理范围执行

### Requirement: 原生入口与所有执行层一致
当前注册图的BFF及原生运行 SHALL 从可信受理记录获取业务和选择，拒绝载荷冲突。模型schema、Skill加载、实际调用、重试、子任务及辅助路径 MUST 遵守实时业务许可和已受理执行范围；普通Skill/MCP不再单独做人员RBAC。

#### Scenario: 伪造metadata或直接工具调用
- **WHEN** 已受理T1的请求伪造T2，或模型直接调用执行范围之外的T2
- **THEN** 拒绝不一致请求或实际调用，T2网络请求为0

#### Scenario: DeepAgents子任务越界
- **WHEN** 父任务选择S1与T1，子任务试图加载S2、调用T2或切换独立业务
- **THEN** 继承父约束，不通过文件读取、旧schema或静态工具集扩大业务或选择范围

### Requirement: 重试重新生成与恢复继承
重试 SHALL 继承原attempt的业务与选择并重新检查实时许可、连接及策略；重新生成默认继承，只有新submission明确声明才接受新合法选择。恢复 MUST 保持原选择，失效时拒绝，不静默自动匹配另一能力；旧人员绑定不参与判断。

#### Scenario: 恢复时目录已变化
- **WHEN** 暂停后Skill材料变更、连接失效、业务撤权或用户在resume改选择
- **THEN** 恢复拒绝并要求重新提交，原attempt及历史事实不改写

#### Scenario: 重新生成修改选择
- **WHEN** 用户有业务资格，以新提交键重新生成并改成合法S2
- **THEN** 新attempt记录S2，原回答和S1仍可回读，不增加原逻辑提问数
