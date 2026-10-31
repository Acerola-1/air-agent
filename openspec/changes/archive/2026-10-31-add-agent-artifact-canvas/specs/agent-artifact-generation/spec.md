## ADDED Requirements

### Requirement: Agent SHALL generate artifacts via create_artifact tool

系统 SHALL 提供 `create_artifact` 工具，Agent 可显式调用以生成画布内容。工具 MUST 接收 `title`（标题）、`content_type`（内容类型）、`content`（原始源码字符串）三个必选参数，以及 `open_in`（展示模式）一个可选参数。工具执行后 MUST 通过 `stream_writer` 将 Artifact 事件推送给前端，并返回简短摘要给 LLM 上下文（不包含原始源码）。

#### Scenario: Agent 生成 HTML 报表画布

- **WHEN** Agent 调用 `create_artifact(title="北京PM2.5报告", content_type="html", content="<!doctype html>...", open_in="canvas_window")`
- **THEN** 工具通过 `stream_writer` 推送 `{type: "artifact", title: "北京PM2.5报告", content_type: "html", content: "<!doctype html>...", open_in: "canvas_window"}` 事件
- **AND** 工具返回 `{success: true, summary: "已在画布中生成 [北京PM2.5报告]..."}` 给 LLM 上下文

#### Scenario: Agent 未指定 open_in 时使用默认值

- **WHEN** Agent 调用 `create_artifact(title="数据对比", content_type="markdown", content="| ... |")` 但未传 `open_in`
- **THEN** 系统 MUST 使用 `open_in="canvas_window"` 作为默认值
- **AND** 推送的 Artifact 事件中 `open_in` 字段为 `"canvas_window"`

#### Scenario: Agent 传入无效 content_type 时校验失败

- **WHEN** Agent 调用 `create_artifact(content_type="pdf", ...)`
- **THEN** 工具 MUST 返回参数校验错误，不推送任何事件
- **AND** 返回的错误信息明确列出支持的 content_type 值

### Requirement: ArtifactMiddleware SHALL auto-detect fenced code blocks in messages

系统 SHALL 提供 `ArtifactMiddleware` 中间件，自动扫描 AIMessage 和 ToolMessage 中的 fenced code block（` ```html ` / ` ```markdown ` / ` ```svg `），将其封装为 Artifact 事件并推送给前端。中间件 MUST 将原始 fenced code block 替换为占位摘要，避免大段源码进入后续 LLM 上下文。

#### Scenario: AIMessage 包含 HTML 代码块

- **WHEN** Agent 在 AIMessage 中输出 ` ```html\n<html>...完整HTML源码...</html>\n``` ` 且内容长度 > 50 字符
- **THEN** 中间件 MUST 封装 `{type: "artifact", content_type: "html", content: "<html>...", title: "<从源码提取>", open_in: "canvas_window", auto_detected: true}` 事件并推送
- **AND** AIMessage 中的原始 fenced code block MUST 被替换为占位摘要文本

#### Scenario: ToolMessage 返回值包含 SVG 代码块

- **WHEN** 某工具的 ToolMessage 内容中包含 ` ```svg\n<svg>...</svg>\n``` `
- **THEN** 中间件 MUST 封装 `{type: "artifact", content_type: "svg", ...}` 事件并推送
- **AND** ToolMessage 内容 MUST 被压缩，fenced code block 替换为占位摘要

#### Scenario: 短代码片段不被误识别为 Artifact

- **WHEN** AIMessage 中包含 ` ```html\n<div>hi</div>\n``` ` 但内容长度 ≤ 50 字符
- **THEN** 中间件 MUST NOT 封装为 Artifact 事件
- **AND** 原始内容保持不变

#### Scenario: create_artifact 工具结果不被中间件二次处理

- **WHEN** `create_artifact` 工具返回 `{success: true, summary: "..."}` 给中间件
- **THEN** 中间件 MUST 直接透传该结果，不进行 code block 扫描或事件推送

### Requirement: ArtifactMiddleware SHALL replace all RichOutputMiddleware usages

系统 MUST 在所有 6 个 graph 中用 `ArtifactMiddleware` 替换 `RichOutputMiddleware`。3 个 DeepAgent 图在 `_build_middleware()` 中直接替换；3 个 LangGraph 图通过 `tool_wrappers.py` 的 `composed_tool_wrapper` 间接替换。`RichOutputMiddleware` 类及其相关代码 MUST 被删除。

#### Scenario: DeepAgent 图中间件替换

- **WHEN** 加载 `deep_research` / `intelligent_report` / `intelligent_tracing` 任一 graph
- **THEN** `_build_middleware()` 返回的中间件列表中 MUST 包含 `ArtifactMiddleware()` 而非 `RichOutputMiddleware()`

#### Scenario: LangGraph 图工具包装器替换

- **WHEN** `composed_tool_wrapper` 执行工具后处理结果
- **THEN** MUST 调用 `ArtifactMiddleware` 的处理逻辑而非 `_handle_rich_output()`
- **AND** `tool_wrappers.py` 中的 `_handle_rich_output()` 函数 MUST 被删除

#### Scenario: 旧 RichOutputMiddleware 代码完全删除

- **WHEN** 在 `src/common/middleware/` 目录中搜索 `rich_output`
- **THEN** MUST NOT 找到 `rich_output_middleware.py` 文件
- **AND** `__init__.py` 中 MUST NOT 导出 `RichOutputMiddleware`

### Requirement: System prompts SHALL guide agents to use artifact output

所有 6 个 graph 的系统提示词 MUST 包含引导 Agent 使用 fenced code block 或 `create_artifact` 工具输出画布内容的指引。指引 MUST 说明：当需要生成报表、画页面、生成文档、展示复杂数据时，使用 ` ```html ` / ` ```markdown ` / ` ```svg ` fenced code block 输出内容。

#### Scenario: 系统提示词包含 Artifact 引导

- **WHEN** 读取任一 graph 的 `SYSTEM_PROMPT` 常量
- **THEN** 提示词中 MUST 包含关于 fenced code block 输出画布内容的引导文本
- **AND** 引导文本 MUST 列出 html / markdown / svg 三种支持的代码块语言
