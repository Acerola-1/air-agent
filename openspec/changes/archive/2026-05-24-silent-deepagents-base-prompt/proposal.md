## Why

DeepAgents 默认基础提示词会告知模型用户可以实时看到响应和工具输出，并鼓励长任务输出进度更新，容易导致模型在 `AIMessage.content` 中泄露工具调用计划、任务进度、失败重试和下一步动作。当前业务问答只需要最终结论正文，因此需要以官方支持的 HarnessProfile 方式微调基础提示词，在保留任务执行能力的同时让模型静默完成中间步骤。

## What Changes

- 新增静默 DeepAgents 基础提示词能力：复制官方 `BASE_AGENT_PROMPT` 的任务执行核心内容，仅替换实时可见和进度更新相关措辞。
- 通过 `HarnessProfile(base_system_prompt=...)` 注册自定义基础提示词，不修改 DeepAgents 包源码。
- 支持 OpenAI 协议兼容第三方供应商的模型切换场景，优先使用 provider 级 `"openai"` profile，避免每次模型名变化都需要新增精确 profile。
- 保留 DeepAgents 的理解任务、使用工具、持续执行、验证结果、必要时追问或阻塞上报能力。
- 明确本变更只约束模型自然语言正文，不负责隐藏 `tool_calls`、`ToolMessage` 或调用方主动渲染的结构化消息。

## Capabilities

### New Capabilities
- `silent-agent-output`: 定义 DeepAgents 在工具调用和任务执行过程中的用户可见输出静默规则，以及最终正文的可见边界。

### Modified Capabilities

## Impact

- 影响所有使用 `create_deep_agent(...)` 创建的业务图，包括 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing`。
- 新增公共 DeepAgents profile 注册位置或初始化模块，必须在各 graph 执行 `create_deep_agent(...)` 前完成注册。
- 不改变业务工具、Skill 路由、MCP 工具、图入口、前端 SSE 协议或 LangGraph state 结构。
- 不升级或修改 DeepAgents 源码；如运行环境仍为 `deepagents==0.6.1`，需确认已支持 `HarnessProfile.base_system_prompt`，并与锁文件中的 `deepagents==0.6.3` 保持一致。
