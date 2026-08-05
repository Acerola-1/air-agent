## ADDED Requirements

### Requirement: Skill 级空气质量数值预报适用性分类
系统 SHALL 对所有包含 `mcp_city_common_get_air_quality_forecast` 的 Skill 逐个进行适用性分类，并将分类结果落实到对应 Skill 文件中。分类 MUST 至少包含必需预测类、条件预测类、可选预测类和非预测类。

#### Scenario: 生成并应用 Skill 分类
- **WHEN** 实施本变更时扫描 Skill 目录中包含 `mcp_city_common_get_air_quality_forecast` 的文件
- **THEN** 每个命中的 Skill SHALL 被归入一种适用性分类
- **AND** 该 Skill 的 `SKILL.md` 或 `references/*.md` SHALL 与分类结果一致，不得只在工具白名单中保留工具而缺少业务边界说明

### Requirement: 监测事实优先于预报数据
系统 SHALL 要求所有保留空气质量数值预报的 Skill 明确区分已监测事实和预测研判。空气质量数值预报 MUST 只用于未来或主数据未覆盖时段的风险补充，不得覆盖、替代或重算已监测事实。

#### Scenario: 已监测统计不被预报覆盖
- **WHEN** Skill 同时获得页面或主工具监测数据以及空气质量数值预报数据
- **THEN** 已监测小时、日累计、峰值、均值、AQI、等级、首要污染物、排名和占比 SHALL 以监测数据为准
- **AND** 预报数据 SHALL 只在趋势预判、未来风险、剩余小时研判或注意事项中使用

### Requirement: 小时级当日未覆盖全天触发条件
小时播报、实时基础查询和其他小时级当日分析 Skill SHALL 仅在查询日期为当日、主监测数据未覆盖全天且输出需要后续趋势或风险研判时，主动调用空气质量数值预报。

#### Scenario: 当日主数据未覆盖全天时补充预报
- **WHEN** 查询日期为当日且主监测小时序列最后有效小时早于 23 时，或当日剩余小时缺失
- **AND** 用户问题或专家模式输出结构需要未来趋势、超标风险或值守建议
- **THEN** Skill SHALL 允许补充空气质量数值预报用于当日剩余小时或未来短临趋势研判

#### Scenario: 历史日期不主动调用预报
- **WHEN** 查询日期早于当日
- **THEN** 小时级 Skill MUST NOT 主动调用空气质量数值预报
- **AND** 输出 SHALL 基于历史监测事实和可用历史气象数据完成复盘

#### Scenario: 当日数据已覆盖全天不主动调用预报
- **WHEN** 查询日期为当日且主监测小时序列已覆盖 0-23 时
- **THEN** 小时级 Skill MUST NOT 主动调用空气质量数值预报
- **AND** 输出 SHALL 按完整监测事实进行分析，不得提示“缺失预报数据”

### Requirement: 非预测类 Skill 移除或限制预报工具
历史复盘、排名、日历、箱线图、空间分布、比例构成和仅解释页面数据的非预测类 Skill SHOULD 从 `allowed-tools` 移除 `mcp_city_common_get_air_quality_forecast`。若因兼容需要保留，Skill detailed references MUST 明确默认不调用，只有用户明确询问未来空气质量风险时才可作为可选补充。

#### Scenario: 非预测类 Skill 不主动扩展未来研判
- **WHEN** 用户在非预测类 Skill 场景中请求历史事实、排名、占比、空间分布或页面数据解读
- **THEN** Skill MUST NOT 主动调用空气质量数值预报
- **AND** 最终回答 MUST NOT 增加与问题无关的未来空气质量预测板块

### Requirement: 缺失预报数据的降级输出
保留空气质量数值预报的 Skill SHALL 定义缺失降级口径。满足调用条件但预报数据不可用时，Skill MUST 基于已有监测数据和可用气象数据降级回答，并用业务语言说明未来空气质量数值预报未纳入研判。

#### Scenario: 满足条件但预报缺失
- **WHEN** Skill 满足空气质量数值预报调用条件但未获得有效预报数据
- **THEN** 最终回答 SHALL 不提及工具、接口或错误细节
- **AND** 对应预测或风险板块 SHALL 说明“暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断”或同等业务口径

### Requirement: Skill 内容静态校验
系统 SHALL 通过测试或静态检查验证空气质量数值预报规则已经落实到 Skill 内容中，防止新增或修改 Skill 时只暴露工具而缺少调用条件和输出边界。

#### Scenario: 保留预报工具的 Skill 通过边界校验
- **WHEN** 测试扫描包含 `mcp_city_common_get_air_quality_forecast` 的 Skill
- **THEN** 该 Skill 的 detailed references SHALL 包含调用条件、禁止条件、监测事实优先和缺失降级相关规则
- **AND** 小时播报专家规则 SHALL 包含“当日”“未覆盖全天”“不得覆盖已监测事实”或等价业务表述
