"""Agent 运行时观测域.

逐调用模型观测、Skill/MCP 归因、Langfuse 真实映射与版本化成本。

- `context`：可信观测上下文（身份来自平台注入 RuntimeIdentity，观测 ID 不可由
  客户端 payload 覆盖）；
- `store`：观测事实 + runtime.* 事件 + outbox 同事务持久化（FORCE RLS）；
- `collector`：有界队列 / 批次 / flush / 重试（≤3 次指数退避），丢弃可见；
- `model_adapter`：LangChain 回调适配，逻辑调用/物理尝试分离，usage 归一化去重；
- `tool_adapter`：工具/MCP 逻辑调用与每次尝试、结果分类；
- `skill_adapter`：Skill 候选/选中/加载/执行分阶段；
- `langfuse_adapter`：隐私白名单 metadata、主/辅助 Trace 映射、支持接口回读。

观测关闭或故障不改变业务答案；授权检查失败关闭不因此放行。
"""
