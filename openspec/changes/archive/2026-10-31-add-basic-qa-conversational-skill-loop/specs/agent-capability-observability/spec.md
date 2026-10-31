## MODIFIED Requirements

### Requirement: Skill各阶段独立
系统 SHALL 分别记录Skill候选/匹配、选中、加载与实际执行，包含稳定Skill标识、版本或内容摘要、Graph、选择来源、attempt/submission及可用的已受理选择引用。用户选择意图与服务端接受计划必须分开，显式选择来源不得伪装为语义匹配。多个Skill允许独立观测，无匹配、加载失败和没有进入执行不得计为成功执行。Basic QA SHALL 区分模型搜索、直接加载和缓存命中，分别统计搜索工具调用与实际 embedding 尝试；加载事件不能从规则字符串存在或进程缓存推断。

#### Scenario: 候选没有执行
- **WHEN** 路由产生3个候选，选中1个但加载失败
- **THEN** 候选数、选中数及加载失败分别记录，实际成功执行数为0

#### Scenario: 菜单确定性选择
- **WHEN** data-analysis通过菜单映射执行Skill，Basic QA按模型选择执行Skill
- **THEN** 两者均按实际执行证据记录，选择来源分别标注，不把确定性路由或直接加载伪装成语义搜索

#### Scenario: 用户选择但没有执行
- **WHEN** 用户显式选择S1，服务端受理后运行在加载前取消
- **THEN** 可回读用户意图和受理选择，不补造matched/loaded/executed，不增加实际Skill使用量

#### Scenario: 用户选择与实际执行核对
- **WHEN** 新submission以用户来源选择S1，并成功加载执行其确定版本
- **THEN** 阶段观测可按选择引用、submission与实际Skill版本核对，模型/工具计费仍按原观测去重

#### Scenario: 缓存加载后未执行
- **WHEN** 本轮直接加载缓存材料后取消，未执行关联业务步骤
- **THEN** 搜索数为0，加载事实含缓存命中，执行数为0，不复用上一轮的执行事实

#### Scenario: 顺序切换后归属
- **WHEN** 自动模式先执行 S1 的工具，再切换 S2 并执行其工具
- **THEN** 工具分别关联执行时活动 Skill 及版本，不将两阶段合并成 top1 的使用量；同一物理调用仍只统计一次
