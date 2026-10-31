# Spec Delta

## Purpose

约束 Basic QA 的用户可见回答形态：只保留一套回答契约，统一采用专家分析结构，不提供快速/专家模式，也不产出图表或画布。

## ADDED Requirements

### Requirement: 单一回答契约
Basic QA SHALL 只提供一套回答契约，不提供快速/专家运行模式；请求中的 `chat_mode` MUST 被忽略，任何轮次 MUST NOT 因该参数改变系统提示、Skill 材料、工具范围或输出结构。

#### Scenario: 旧客户端提交 chat_mode
- **WHEN** 请求携带 `chat_mode=expert` 或 `chat_mode=fast`
- **THEN** 服务端按无模式处理，不切换提示或材料，不要求重新加载已加载 Skill

#### Scenario: 同一线程跨轮回答
- **WHEN** 同一线程连续两轮提问
- **THEN** 两轮使用同一份 Skill 规则文件与同一套工具范围，不出现模式差异

#### Scenario: 系统提示不含模式上下文
- **WHEN** Basic QA 组装系统提示
- **THEN** 提示中不存在 `<runtime_context>mode: ...</runtime_context>`、快速模式规则或专家模式规则段落

### Requirement: 统一专家结构与深度
Basic QA 的回答 SHALL 统一采用专家分析结构：先给核心结论，再给数据与事实，再给分析或建议，最后给注意事项；简单事实问题 MUST 仍以结论和关键数据开头，不得为套用结构而省略事实或编造分析。

#### Scenario: 趋势分析问题
- **WHEN** 用户询问多日污染物趋势且主数据与气象数据可用
- **THEN** 回答包含核心结论、逐日趋势、峰谷变化、气象影响和后续关注点

#### Scenario: 单事实查询
- **WHEN** 用户只询问某城市某时刻的污染物浓度
- **THEN** 回答以结论、数值、单位、时间和数据来源开头，不因统一结构加入无数据支撑的成因或管控建议

#### Scenario: 用户明确要求建议
- **WHEN** 用户在数据结论之后明确要求管控或跟踪建议
- **THEN** 回答可给出有数据支撑的建议，并标注适用条件与数据局限

### Requirement: 不产出图表或画布
Basic QA SHALL NOT 向模型披露 `chart_*` 或 `create_artifact` 工具，MUST NOT 指示模型生成图表、HTML 画布或 artifact；用户可见输出形态限于文本与 Markdown 表格。用户要求图表时，回答 SHALL 用表格或文字说明替代，并明确未生成图。

#### Scenario: 用户要求图表
- **WHEN** 用户要求生成折线图、柱状图或画布
- **THEN** 回答用 Markdown 表格和文字说明呈现数据，不调用图表或画布工具，不返回 `artifact_ref`

#### Scenario: 模型工具披露
- **WHEN** Basic QA 构造模型请求
- **THEN** 工具列表中不存在 `chart_*` 与 `create_artifact`，Skill 规则中不存在“必须调用图表工具”的指令

#### Scenario: 读取历史回答
- **WHEN** 读取 Basic QA 的历史回答
- **THEN** 回答正文只包含文本与表格，不依赖画布组件渲染
