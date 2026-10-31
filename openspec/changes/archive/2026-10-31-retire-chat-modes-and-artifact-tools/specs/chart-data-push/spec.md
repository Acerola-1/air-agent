# Spec Delta

## REMOVED Requirements

### Requirement: MCP chart_data 格式兼容提取
**Reason**: Basic QA 不再生成或渲染图表，`chart_*` 工具已退役，chart_data 推送链路没有消费方。
**Migration**: 删除 chart_data/rich_outputs 提取与 stream_writer 推送；Skill 中以 Markdown 表格和文字说明替代图表。

### Requirement: output-hints 配合 chart_data 推送
**Reason**: Skill frontmatter 不再声明图表渲染类型，`optional-tools` 中的 `chart_*` 一并删除。
**Migration**: 删除 output-hints 中的 display_mode/chart_subtype 声明；不再按图表类型驱动前端渲染。

### Requirement: 清理 legacy nodes 模块
**Reason**: legacy `src/agent/nodes.py` 职责桶早已不存在，本条要求已完成，且与图表能力撤除同属过期约束。
**Migration**: 无需迁移；保留现有模块划分，不因本变更恢复 legacy nodes。
