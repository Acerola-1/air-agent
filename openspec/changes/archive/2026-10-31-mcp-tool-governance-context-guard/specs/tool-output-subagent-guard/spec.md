## ADDED Requirements

### Requirement: Tool execution SHALL offload oversized outputs to database and summarize via Subagent

系统 SHALL 在工具执行拦截层（`composed_tool_wrapper`）对单次工具输出进行字符大小检测。当工具返回内容的字符数超过 `TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS`（默认 20,000 字符）时，系统 MUST 自动将原始完整数据持久化至专属数据表 `tool_artifact_storage`，并自动调用拥有独立 192K 上下文的 Subagent 提炼子代理进行结构化总结，将总结文本作为 `ToolMessage.content`，将存储引用作为 `ToolMessage.artifact` 返回。

#### Scenario: 工具返回正常大小数据时直接放行

- **WHEN** 工具执行完毕，返回文本长度为 3,500 字符（≤ 20,000 阈值）
- **THEN** 系统 MUST 原样构造或保留 `ToolMessage`
- **AND** 状态标记为正常，不触发数据库落库，亦不调用 Subagent 总结

#### Scenario: 工具返回超大数据时自动落库并由 Subagent 提炼

- **WHEN** 模型调用工具时因参数不当导致工具返回 500,000 字符的原始 JSON（> 20,000 阈值）
- **THEN** 系统 MUST 将 500,000 字符原始数据写入 `tool_artifact_storage` 表并生成唯一 `artifact_id`
- **AND** 系统 MUST 调度 Subagent 总结子代理，传入用户原始提问、工具调用信息与原始数据
- **AND** 系统返回的 `ToolMessage.content` MUST 包含 Subagent 提炼后的高浓度结论（长度控制在 1,500 字符以内）
- **AND** 系统返回的 `ToolMessage.artifact` MUST 包含 `{"artifact_id": "<id>", "storage_type": "database"}` 引用元数据，原始 50 万字符绝不出现在 content 中

#### Scenario: Subagent 成功命中并抽取目标实体数据

- **WHEN** 用户提问为“海淀区空气质量”，超大数据集中包含全国 3000 个区县的数据
- **THEN** Subagent MUST 精准提取海淀区的相关指标与结论
- **AND** Subagent MUST 过滤掉其他非目标区县的全部噪音数据

#### Scenario: Subagent 未匹配到目标实体时显式警示

- **WHEN** 用户提问为“朝阳区空气质量”，但超大数据集中仅包含河北省数据（参数严重错误）
- **THEN** Subagent 生成的总结中 MUST 包含显式警示信息：“未匹配到目标实体 [朝阳区] 的相关数据，可能由于查询参数缺失导致”
- **AND** Subagent MUST NOT 虚构或编造朝阳区的数据

#### Scenario: Subagent 调用超时或异常时安全降级

- **WHEN** Subagent 提炼调用因模型超时或网络异常发生失败
- **THEN** 系统 MUST 截取原始数据的前 2000 字符与末尾 500 字符作为预览样本
- **AND** 系统 MUST 在 `ToolMessage.content` 中附带安全降级说明与 `artifact_id` 引用，严禁将全量 50 万字符裸送回主图

### Requirement: Tool wrapper SHALL preserve GraphBubbleUp for HITL interrupts

工具执行包装器在捕获异常时，MUST 显式放行 `GraphBubbleUp` 异常，保证 LangGraph 的 `interrupt()` 人机交互挂起机制不受影响。

#### Scenario: 工具内部调用 interrupt 时正常向上冒泡

- **WHEN** 工具执行期间调用了 `interrupt()` 触发人机交互挂起，底层抛出 `GraphBubbleUp` 异常
- **THEN** 工具包装器 MUST 直接 re-raise 该异常，绝不将其捕获降级为普通错误消息
