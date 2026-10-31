# Spec Delta

## REMOVED Requirements

### Requirement: Main Agent 路由调度
**Reason**: 当前实现不存在 `[module:XXX][mode:XXX]` 标记、Main Agent 路由或对应解析代码；Basic QA 已改为显式节点流，溯源图使用 DeepAgents 调度，本规格描述的架构已退役。
**Migration**: 无需迁移；按各图现有入口和 `langgraph.json` 使用，不恢复模块/模式标记。

### Requirement: 6 SubAgent 模块化定义
**Reason**: 代码中不存在 base-agent、intelligent-analysis-agent 等 6 个 SubAgent 定义，相关图已从公开入口移除。
**Migration**: 不恢复旧 SubAgent 目录；新增能力按当前图边界实现。

### Requirement: AgentState 路由字段
**Reason**: 当前图状态不含 `module`/`mode` 路由字段，代码检索无对应写入或读取。
**Migration**: 不新增路由字段；模式概念随 Basic QA 快速/专家模式一并删除。

### Requirement: 移除 intent_match_tool
**Reason**: 该要求是历史清理项，当前代码已无 `intent_match_tool` 或 `intent_config.py` 引用，保留在规格中只会制造过期约束。
**Migration**: 无需迁移；保持现有 Skill 按需发现与加载机制。
