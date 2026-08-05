## ADDED Requirements

### Requirement: MCP chart_data 格式兼容提取
系统 SHALL 在 `_extract_rich_outputs_from_message()` 中增加对 MCP 工具返回的 `{data: {llm_data: ..., chart_data: ...}}` 格式的兼容提取逻辑。当 ToolMessage 的 content 包含 `chart_data` 字段时，系统 SHALL 将其作为 rich_output 推送前端渲染。

#### Scenario: MCP 返回 chart_data 格式时提取推送
- **WHEN** MCP 工具返回 `{"code": 200, "data": {"llm_data": {...}, "chart_data": {"echarts_option": {...}}}}` 格式的 ToolMessage
- **THEN** `_extract_rich_outputs_from_message()` 提取 `chart_data` 内容，通过 stream_writer 推送 `{"output_type": "chart_data", "display_mode": "canvas", "data": chart_data内容}` 给前端

#### Scenario: MCP 返回 rich_outputs 格式时正常提取
- **WHEN** MCP 工具返回包含 `rich_outputs[]` 数组的 ToolMessage
- **THEN** `_extract_rich_outputs_from_message()` 按现有逻辑提取 `rich_outputs`，通过 stream_writer 推送给前端（现有行为不变）

#### Scenario: MCP 返回无图表数据时静默跳过
- **WHEN** MCP 工具返回的 ToolMessage 不包含 `chart_data` 或 `rich_outputs` 字段
- **THEN** `_extract_rich_outputs_from_message()` 不推送任何 rich_output 事件，静默跳过

### Requirement: output-hints 配合 chart_data 推送
Skill YAML frontmatter 中的 `output-hints` SHALL 与 chart_data 推送机制配合，声明 MCP 工具返回数据的 display_mode 和 chart_subtype，指导前端渲染类型。

#### Scenario: output-hints 声明图表渲染类型
- **WHEN** broadcast-hour SKILL.md 的 output-hints 声明 `statistics_city_broadcastHour: display_mode: canvas, chart_subtype: line`
- **THEN** 当 MCP 工具 statistics_city_broadcastHour 返回 chart_data 时，推送的 rich_output 包含 `display_mode: "canvas"` 和 `chart_subtype: "line"`

### Requirement: 清理 legacy nodes 模块
系统 SHALL 移除 legacy `nodes.py` 职责桶；模型、MCP、权限规则、上下文辅助分别由独立模块承载。

#### Scenario: nodes.py 不再作为职责桶存在
- **WHEN** 检查 `src/agent/` 源代码
- **THEN** 不存在 `nodes.py` 文件，且业务代码不再引用 `agent.nodes`
