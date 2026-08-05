## ADDED Requirements

### Requirement: 最终输出清理中间件
系统 SHALL 提供一个后置中间件，在业务 DeepAgent 完成后清理最终可见答案中的过程信息，并保持主 Agent 执行链路、工具调用、Skill 输出和原始 Messages 降级路径可用。

#### Scenario: 提取最后可见业务答案
- **WHEN** `state.messages` 中同时存在工具调用消息、`ToolMessage` 和多条 `AIMessage`
- **THEN** 中间件 SHALL 选择最后一条不含 `tool_calls` 且内容非空的 `AIMessage` 作为待清理正文
- **AND** 中间件 SHALL NOT 使用 `ToolMessage`、带 `tool_calls` 的 `AIMessage` 或空内容消息作为待清理正文

#### Scenario: 无可清理答案时跳过
- **WHEN** `state.messages` 中不存在可见业务答案
- **THEN** 中间件 SHALL 不调用 finalizer 模型
- **AND** 主 Agent SHALL 按原有消息结果结束

### Requirement: 整理开始进度事件
系统 SHALL 在最终输出清理开始时通过 custom stream 推送固定进度事件，让调用方知道主业务执行已结束且正在整理最终答案。

#### Scenario: 推送整理进度
- **WHEN** 中间件准备对最终答案执行清理
- **THEN** 中间件 SHALL 先调用 `runtime.stream_writer` 推送事件
- **AND** 事件 SHALL 包含 `type: "progress"`
- **AND** 事件 SHALL 包含 `message: "正在整理最终答案"`

### Requirement: 最终答案流式清理输出
系统 SHALL 支持 finalizer 模型流式输出清理后的最终正文，调用方可以优先监听该流式结果作为用户可见答案。

#### Scenario: 推送清理正文增量
- **WHEN** finalizer 模型返回一个非空文本 chunk
- **THEN** 中间件 SHALL 通过 `runtime.stream_writer` 推送 `type: "final_output_delta"` 事件
- **AND** 事件的 `message` SHALL 为当前 chunk 文本

#### Scenario: 推送清理正文完成事件
- **WHEN** finalizer 模型完成且得到非空完整正文
- **THEN** 中间件 SHALL 推送 `type: "final_output_done"` 事件
- **AND** 事件的 `message` SHALL 为完整清理后正文

### Requirement: 只清理过程信息且保持正文保真
系统 SHALL 限制 finalizer 只删除过程性和内部实现信息，不得重构、扩写、压缩或改变 Skill 产出的业务正文。

#### Scenario: 删除过程和内部信息
- **WHEN** 待清理正文包含工具调用计划、执行进度、失败重试、Skill 名称、工具名称、函数参数、JSON、路径、middleware、LangGraph、MCP 或内部字段说明
- **THEN** finalizer 输出 SHALL 删除这些过程或内部实现信息
- **AND** finalizer 输出 SHALL 保留原答案中的业务结论、依据、关键数据和建议

#### Scenario: 保留业务正文结构
- **WHEN** 待清理正文包含 Markdown 标题、列表、表格、日期、数值、单位、排序、风险提示或建议段落
- **THEN** finalizer 输出 SHALL 保留这些业务正文结构和内容
- **AND** finalizer 输出 SHALL NOT 重排、改写、总结、翻译、扩写或压缩原业务答案

#### Scenario: 原答案已干净
- **WHEN** 待清理正文不包含过程信息或内部实现信息
- **THEN** finalizer 输出 SHALL 与原答案保持一致

### Requirement: 清理失败降级
系统 SHALL 将最终输出清理视为展示增强，finalizer 失败不得影响主回答完成。

#### Scenario: finalizer 调用失败
- **WHEN** finalizer 模型调用异常、超时或返回空结果
- **THEN** 中间件 SHALL 记录内部日志并停止清理流程
- **AND** 中间件 SHALL NOT 向用户推送清理失败说明
- **AND** 调用方 SHALL 仍可退化使用原始 Messages 中的最后可见答案

### Requirement: 业务图统一接入
系统 SHALL 将最终输出清理中间件接入所有业务 DeepAgent graph，并保持现有工具、Skill、富输出和推荐追问能力不变。

#### Scenario: 6 个业务图装配中间件
- **WHEN** 任一业务 graph 构建 middleware 列表
- **THEN** middleware 列表 SHALL 包含最终输出清理中间件
- **AND** 当该 graph 已装配 `ExpandQuestionMiddleware` 时，最终输出清理中间件 SHALL 位于 `ExpandQuestionMiddleware` 之前

#### Scenario: 现有 custom 事件不变
- **WHEN** 工具进度、富输出或推荐追问事件被推送
- **THEN** 最终输出清理中间件 SHALL NOT 改变这些事件的既有字段和语义
