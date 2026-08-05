## 1. 基线与提示词来源

- [x] 1.1 确认运行时使用的已安装 DeepAgents 版本，并与 `requirements.lock.txt` 比较。
- [x] 1.2 如果本地 `.venv` 依赖版本落后于 Docker 或 `requirements.lock.txt`，使用 `uv` 从锁文件同步/更新本地依赖，然后再复制 DeepAgents 提示词。
- [x] 1.3 定位已安装的 `deepagents.graph.BASE_AGENT_PROMPT` 并将其当前完整字符串复制到项目拥有的提示词常量中。
- [x] 1.4 仅编辑默认提示词中使工具输出对用户可见或鼓励进度更新的行。
- [x] 1.5 保留默认提示词中关于核心行为、专业客观性、任务执行、失败处理和澄清行为的章节。
- [x] 1.6 添加注释或测试，记录哪些上游提示词行被有意修改。

## 2. 共享 HarnessProfile 注册

- [x] 2.1 创建用于静默 DeepAgents profile 注册的共享公共模块。
- [x] 2.2 在该模块中使用最小编辑的上游提示词定义 `SILENT_DEEPAGENTS_BASE_PROMPT`。
- [x] 2.3 在 provider 级 `"openai"` 键下注册 `HarnessProfile(base_system_prompt=SILENT_DEEPAGENTS_BASE_PROMPT, system_prompt_suffix=...)`。
- [x] 2.4 保持后缀简短且仅限于最终用户可见输出约束。
- [x] 2.5 使 profile 注册可安全地从多个图模块导入，无需图特定的代码重复。

## 3. 图集成

- [x] 3.1 在 `src/basic_qa/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.2 在 `src/intelligent_analysis/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.3 在 `src/data_analysis/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.4 在 `src/intelligent_report/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.5 在 `src/deep_research/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.6 在 `src/intelligent_tracing/graph.py` 中于 `create_deep_agent(...)` 之前导入或调用共享静默 profile 注册。
- [x] 3.7 不在本变更中移除工具、Skill 中间件、文件系统中间件、摘要中间件、子智能体中间件或图检查点。

## 4. OpenAI 兼容 Provider 验证

- [x] 4.1 添加针对性测试或诊断辅助工具，确认代表性项目 `ChatOpenAI` 模型将 `ls_provider` 解析为 `"openai"`。
- [x] 4.2 验证当模型名称变更但客户端仍为 `ChatOpenAI` 时，provider 级注册仍然适用。
- [x] 4.3 记录如果未来模型需要不同行为时的精确 `provider:model` 注册回退路径。
- [x] 4.4 确保实现不在注册中硬编码 SiliconFlow、DeepSeek、XFYun、Ark 或其他第三方模型名称。

## 5. 单元测试

- [x] 5.1 添加测试，验证静默基础提示词不包含上游关于用户可实时看到工具输出的措辞。
- [x] 5.2 添加测试，验证静默基础提示词不指示模型为较长任务提供进度更新。
- [x] 5.3 添加测试，验证静默基础提示词仍包含等同于先理解、行动、验证和持续工作直到完成的任务执行指导。
- [x] 5.4 添加测试，验证共享注册模块为 `"openai"` provider 注册了 HarnessProfile。
- [x] 5.5 添加测试或静态检查，验证每个图模块在 `create_deep_agent(...)` 之前导入或调用共享注册。
- [x] 5.6 添加测试，验证现有业务提示词守卫仍通过 `with_main_agent_tool_use_output_guard(...)` 和 `with_data_analysis_output_guard(...)` 组合。

## 6. 行为验证

- [x] 6.1 运行静默 profile 模块的针对性单元测试文件。
- [x] 6.2 运行现有提示词守卫测试，确保输出守卫内容无回归。
- [x] 6.3 通过 LangSmith 追踪或本地图/模型请求内省检查一个组装的模型系统提示词。
- [x] 6.4 确认组装提示词使用自定义基础提示词而非 DeepAgents 默认 `BASE_AGENT_PROMPT`。
- [x] 6.5 触发需要 `find_skill` 和 `read_file` 的代表性图路径，然后确认当工具调用存在时中间 `AIMessage.content` 为空。
- [x] 6.6 确认工具执行完成后最终答案仍包含正常业务 Markdown 内容。

## 7. 文档与回滚

- [x] 7.1 添加简短开发者说明，解释为何使用 `HarnessProfile.base_system_prompt` 而非仅增强 `system_prompt`。
- [x] 7.2 记录本变更抑制模型文本内容但不隐藏渲染的 `tool_calls` 或 `ToolMessage` 结构的边界。
- [x] 7.3 记录通过移除共享注册导入或禁用注册模块进行回滚。
- [x] 7.4 更新 PR 说明，提及 provider 级 `"openai"` 范围及其对 OpenAI 兼容第三方模型的适用性。
