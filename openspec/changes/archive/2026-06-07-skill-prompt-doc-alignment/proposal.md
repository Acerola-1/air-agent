## Why

basic_qa 下 7 个 Skill 的 fast.md / expert.md / SKILL.md 与设计文档《数智大气-通用问题提示词》存在系统性偏差：气象数据要求层级错误（文档标"必须"的 Skill 实为"可选"或"用户要求才补充"）、多城对比缺少浓度差值量化、达标研判模板与文档示例格式差距大、数值预测数据缺少双向规则（有数据必须分析 / 无数据必须标注局限）。这些偏差会导致快速模式和专家模式输出与产品规范不一致。

## What Changes

- **air-quality-basic-query**：快速模式气象从"可省略"改为"必须获取+缺失必须标注"；多城对比模板增加浓度差值量化；增加时效性标注规则；单城模板增加综合指数；数值预测模块统一为独立步骤+双向规则
- **comparison-composition**：allowed-tools 加入气象工具；fast.md 增加气象获取流程+缺失标注"气象数据暂缺"；expert.md 删除"不得主动调用天气工具"改为必须调用；同比模板增加优良天数字段（有数据时展示）
- **compliance-feasibility**：研判结论统一为文档规定的3种（大概率可实现/存在难度/无法实现）；保良模板按文档格式补全累计浓度+逐小时控制阈值+当前小时数据（TODO: 工具暂不支持逐小时控制阈值）；气象缺失标注统一为"缺失气象数据，研判结论存在局限"；数值预测模块统一为独立步骤+双向规则
- **ranking-assessment**：增加同比>±100%异常标注规则
- **regional-benchmark**：增加达标城市名单推荐按拼音首字母排序规则
- **station-extreme**：增加国控/省控降级展示规则
- **trend-analysis**：快速模式气象从"用户要求才补充"改为"必须获取+缺失必须标注"；数值预测模块统一为独立步骤+双向规则

## Capabilities

### New Capabilities
- `forecast-data-bidirectional-rule`: 数值预测数据双向规则——有预报数据时必须结合分析，无预报数据时必须标注局限，适用于涉及当天/未来数据的 Skill（air-quality-basic-query、compliance-feasibility、trend-analysis）

### Modified Capabilities
- `skill-content-basic`: 调整 7 个 Skill 的气象数据要求层级、模板格式、缺失标注规则、异常校验规则等，使其与设计文档对齐

## Impact

- 7 个 Skill 的 SKILL.md / fast.md / expert.md 共约 13 个文件需修改
- comparison-composition 的 SKILL.md allowed-tools 需增加气象工具
- 无代码层变更，仅提示词/规则文本调整
- compliance-feasibility 保良模板新增 TODO 标记（工具暂不支持逐小时控制阈值）
