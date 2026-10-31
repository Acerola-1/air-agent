## ADDED Requirements

### Requirement: System SHALL automatically summarize earlier conversation history using LLM when history exceeds threshold

系统 SHALL 在多轮会话累积历史超过预设预算（如待压缩历史预估 Token 超过 `CONVERSATION_SUMMARY_THRESHOLD_TOKENS`，默认 30,000，或历史轮数超过安全阈值）时，自动触发独立会话摘要生成机制。系统 MUST 保留最近 `CONVERSATION_SUMMARY_KEEP_TURNS` 轮（默认 3 轮）活跃对话不予压缩，将其余更早的历史轮次抽取并与已有 `summary` 结合，调用大模型更新为高度精炼的全局会话摘要（保留核心意图、关键讨论结论和实体，控制在 1,000 字符内），并持久化在状态中。

#### Scenario: 活跃轮次不足或 Token 未超阈值时跳过会话摘要

- **WHEN** 当前会话历史消息较短（例如仅有 2 轮对话，估算 Token 远小于阈值）
- **THEN** 系统 MUST 不触发大模型会话摘要调用，保持原始对话流转

#### Scenario: 早期多轮对话被大模型平滑压缩为全局摘要

- **WHEN** 当前会话累计多轮问答且待压缩的早期历史 Token 超过阈值
- **THEN** 系统 MUST 提取早于最近 3 轮的早期消息
- **AND** 系统 MUST 调用大模型将早期消息与既有摘要融合成更新后的全局摘要并存入 `state["summary"]`
- **AND** 模型后续推理时，早期轮次被摘要替代，最近 3 轮完整保留，送入模型的 Token 被大幅缩减至安全预算内

#### Scenario: 会话摘要生成异常或超时安全降级

- **WHEN** 大模型执行会话摘要调用超时（>15秒）或发生异常
- **THEN** 系统 MUST 捕获异常，回退至安全降级摘要或保留既有摘要，绝不抛出异常阻断主业务图执行
