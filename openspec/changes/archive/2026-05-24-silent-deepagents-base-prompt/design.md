## 背景

项目使用 DeepAgents 构建多个 LangGraph 业务图。每个图当前通过 `with_main_agent_tool_use_output_guard(...)` 或 `with_data_analysis_output_guard(...)` 传入强业务 `system_prompt`，但 DeepAgents 会在调用方提示词之后追加其默认的 `BASE_AGENT_PROMPT`。该默认提示词包含两个与期望用户体验冲突的行为：

- 它告知模型用户可以实时看到响应和工具输出。
- 它鼓励在较长任务中输出简短进度更新。

期望的行为比禁用 DeepAgents 规划更窄：智能体仍应理解、内部规划、调用工具、读取 Skill 文件、重试、验证并持续执行直到完成。只有 `AIMessage.content` 中面向过程的自然语言内容应在最终面向用户的答案准备好之前被抑制。

DeepAgents 官方支持通过 `HarnessProfile.base_system_prompt` 替换其基础提示词。最终提示词组装顺序为 `system_prompt`，然后是默认 `BASE_AGENT_PROMPT` 或 `HarnessProfile.base_system_prompt`，最后是 `HarnessProfile.system_prompt_suffix`（如已设置）。

## 目标 / 非目标

**目标：**

- 使用官方 `HarnessProfile.base_system_prompt` 扩展点替换 DeepAgents 默认 `BASE_AGENT_PROMPT`。
- 保留官方基础提示词的核心任务执行行为，仅复制并修改用户可见的过程/进度措辞。
- 在通过 `ChatOpenAI` 实例化的 OpenAI 兼容第三方模型供应商中一致应用。
- 确保注册在任何图调用 `create_deep_agent(...)` 之前完成。
- 保持实现集中化，使所有业务图共享相同的静默 DeepAgents 基础提示词。
- 添加针对性测试以验证提示词注册和关键提示词内容。

**非目标：**

- 不修改 DeepAgents 包源码。
- 不在首次实现中禁用工具执行、Skill 读取、MCP 调用、文件系统中间件、子智能体或任务规划。
- 不移除 `write_todos` 或禁用默认通用子智能体，除非后续证据表明仅提示词方式不足。
- 不在本变更中修改 SSE 协议、LangGraph state 结构或前端渲染逻辑。
- 不隐藏 `AIMessage.tool_calls` 或 `ToolMessage`，如果调用方直接将结构化消息渲染为用户可见文本。

## 决策

### 决策 1：使用 `HarnessProfile.base_system_prompt` 而非编辑业务提示词

实现将在图构建之前注册一个带有自定义 `base_system_prompt` 的 HarnessProfile。

理由：

- `system_prompt=` 在 DeepAgents 基础提示词之前插入，无法移除后续冲突的指令。
- `system_prompt_suffix` 可以抵消默认提示词，但会在同一系统消息中留下矛盾指令。
- `base_system_prompt` 通过官方 DeepAgents API 干净地替换 `BASE_AGENT_PROMPT`。

考虑过的替代方案：增强 `with_main_agent_tool_use_output_guard(...)`。这已被尝试过，但由于 DeepAgents 在调用方提示词之后追加其默认基础提示词，效果仍然较弱。

### 决策 2：复制官方基础提示词并最小化编辑过程可见性

自定义基础提示词将保留官方核心行为、专业客观性、执行任务、处理失败和澄清请求的章节和指令。编辑后的提示词仅修改引言和进度部分，要求在工具工作期间静默执行。

理由：

- 最小差异降低削弱 DeepAgents 任务执行的风险。
- 智能体保持相同的操作契约：先理解、行动、验证、持续工作直到完成，仅提出必要的后续问题。
- 变更针对过程叙述的确切来源，而非重新设计智能体行为。

考虑过的替代方案：用非常简短的静默提示词替换基础提示词。风险较高，因为可能移除 DeepAgents 关于持久性、验证和失败处理的有用默认行为。

### 决策 3：为 OpenAI 兼容客户端注册 provider 级 `"openai"` HarnessProfile

实现将在 `"openai"` 下注册静默 profile，因为业务模型是预配置的 `ChatOpenAI` 实例，通过自定义 `base_url` 连接到 OpenAI 兼容的第三方供应商。

理由：

- 具体模型名称频繁变更。
- DeepAgents 按 provider 和模型标识符解析预构建模型实例；provider 级注册在 provider 解析为 `openai` 时覆盖模型变更。
- 这与项目当前使用 OpenAI 兼容客户端连接 SiliconFlow、DeepSeek 兼容、XFYun 兼容及类似供应商的做法一致。

考虑过的替代方案：注册精确键如 `openai:<model>`。当仅一个模型需要受影响时更安全，但每次模型名称变更时需要持续维护。

风险控制：

- 将注册保留在仅由此服务导入的项目特定模块中。
- 为代表性 `ChatOpenAI` 模型添加小型内省测试，确认解析的 provider 为 `openai`。
- 记录如果某个模型需要不同行为，后续可以叠加精确 `provider:model` profile。

### 决策 4：在共享模块中集中 profile 注册

实现将添加一个公共模块，负责定义复制的提示词并注册 HarnessProfile。图模块将在 `create_deep_agent(...)` 之前导入或调用此注册。

理由：

- 多个图入口点当前独立调用 `create_deep_agent(...)`。
- 集中化避免提示词漂移和重复注册代码。
- 注册是可加的且对模块导入使用足够幂等，但实现仍应通过保持模块窄小且命名良好来避免令人惊讶的副作用。

考虑过的替代方案：在每个图模块中注册 profile。这很简单但会重复代码并增加某个图遗漏注册的风险。

## 风险 / 权衡

- Provider 级 `"openai"` profile 影响同一 Python 进程中使用 OpenAI 兼容 `ChatOpenAI` provider 的每个 DeepAgent → 缓解措施：这对当前服务是有意为之；如果未来图需要可见进度，使用精确模型 profile 或按图工厂隔离注册。
- DeepAgents 默认提示词可能在未来版本中变更，复制的自定义提示词可能漂移 → 缓解措施：包含一个任务，在升级 DeepAgents 时与已安装的 `BASE_AGENT_PROMPT` 进行比较。
- 仅提示词抑制无法隐藏结构化工具调用消息，如果前端直接渲染它们 → 缓解措施：记录此边界，将渲染层过滤作为单独变更保留（如需要）。
- 某些模型可能仍会无视提示词约束输出过程内容 → 缓解措施：在测试或手动验证中添加回归提示词；如需要，后续添加模型响应守卫中间件，在 `tool_calls` 存在时清空 `AIMessage.content`。
- 当前环境可能与锁文件不匹配（`requirements.lock.txt` 引用 `deepagents==0.6.3`，而活动 venv 可能导入不同版本）→ 缓解措施：在实现期间验证已安装版本，如果 HarnessProfile API 不同则对齐环境。

## 迁移计划

1. 添加共享静默 DeepAgents profile 模块。
2. 在任何图创建 DeepAgent 之前注册 provider 级 `"openai"` HarnessProfile。
3. 从所有使用 `create_deep_agent(...)` 的图模块导入或调用注册。
4. 为提示词内容和注册位置添加单元测试。
5. 通过 LangSmith 追踪或本地内省手动检查一个图的组装模型提示词，确认默认 `BASE_AGENT_PROMPT` 不再出现。
6. 通过移除共享注册导入/调用回滚；图构建将回退到 DeepAgents 默认基础提示词。

## 待解决问题

- 注册是否应为所有 OpenAI 兼容模型使用 provider 级 `"openai"`，还是生产环境最终应支持环境标志来选择 provider 级与精确模型注册？
- 如果 Java SSE 消费者仍然显示结构化消息对象，后续变更是否应添加 `AIMessage.tool_calls` 和 `ToolMessage` 的渲染层过滤？
