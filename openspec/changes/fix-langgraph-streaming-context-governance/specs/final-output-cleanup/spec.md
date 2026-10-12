# 显式图最终输出契约增量

## ADDED Requirements

### Requirement: 显式业务图的终答契约

系统 SHALL 为 basic-qa、intelligent-analysis 和 data-analysis 提供明确的终答可见标记与稳定消息 ID；中间答案不展示，data-analysis 清洗正文替换同轮原稿。其他 DeepAgents 图保持既有行为。

#### Scenario: 清洗答案替换原稿

- **WHEN** 同一轮产生非空原稿和清洗终稿
- **THEN** 实时界面及重新打开线程只展示一个终答，工具卡仍可查看

#### Scenario: 整理模型失败

- **WHEN** 清洗模型超时或失败
- **THEN** 以明确的终答标记提供可读降级答案，不发布半段清洗文本或内部错误
