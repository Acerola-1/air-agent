## 修改需求

### 需求：从 MCP 工具响应中提取富输出
富输出提取 SHALL 由 `RichOutputMiddleware` 在 `awrap_tool_call` 钩子中执行，拦截每次 MCP 工具执行后的 ToolMessage 响应。它 MUST 同时支持 `rich_outputs[]`（新格式）和 `chart_data`（旧格式）的提取。

#### 场景：MCP 响应包含 rich_outputs 数组
- **WHEN** 工具返回的 JSON 包含 `data.rich_outputs[]` 数组
- **THEN** 中间件提取每一项，通过 stream_writer 推送为 `{"node": "deepagent_executor", "type": "rich_output", "output_type", "display_mode", "sequence", "title", "tool_name", "chart_subtype", "message": data}`

#### 场景：MCP 响应包含旧版 chart_data
- **WHEN** 工具返回的 JSON 包含 `data.chart_data` 但无 `rich_outputs[]`
- **THEN** 中间件提取 chart_data，以默认 `output_type="chart_data"` 和 `display_mode="canvas"` 推送

#### 场景：Skill output-hints 声明 display_mode 为 none
- **WHEN** 当前 Skill 对某个工具的 output-hints 指定 `display_mode: none`
- **THEN** 中间件完全跳过该工具的提取

#### 场景：工具响应非 JSON 或不成功
- **WHEN** ToolMessage 内容无法解析为 JSON 或 `success != True`
- **THEN** 中间件静默跳过，不发送任何推送