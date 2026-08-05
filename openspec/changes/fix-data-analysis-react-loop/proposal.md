## Why

data_analysis 从 DeepAgent 迁移到 LangGraph 节点流后，存在三个核心缺陷导致"程序无 output"：

1. **`route_after_tools` 无条件循环 + off-by-one**：工具执行后无条件回到 `prepare_model`，只看迭代计数不看工具结果。加上 `_increment_iteration` 和 `route_after_tools` 双重 +1，实际只允许 2 轮工具调用。当迭代超限时最后一条 AIMessage 带 `tool_calls`，`_extract_last_visible_answer` 跳过它 → 提取空答案 → 无 output。
2. **Skill 匹配失败时无降级系统提示词**：旧版 DeepAgent 有完整的 `SYSTEM_PROMPT` 作为兜底，新版 `resolve_skill` 返回空 `skill_rules_content` 和空 `allowed_tools` → 模型无工具无规则 → 无有效输出。
3. **SKILL.md 正文（执行边界）未加载**：`resolve_skill` 只读 frontmatter 的 `allowed-tools`，丢弃了 SKILL.md 中"详细规则入口"和"执行边界"等跨 mode 共享的刚性约束。

## What Changes

- **修复 ReAct 循环路由**：去掉 `route_after_tools`，改为 `execute_tools → call_model`，由 `route_after_model` 统一根据 `tool_calls` 有无决定继续循环或输出。`prepare_model` 只在 `resolve_skill` 后执行一次，不在循环内重复。
- **修复迭代计数 off-by-one**：`_increment_iteration` 和路由判断统一使用 `iteration_count`，不再在路由函数中额外 +1。
- **Skill 匹配失败降级**：`resolve_skill` 匹配失败时，将旧版 DeepAgent 的完整 `SYSTEM_PROMPT`（含角色职责、核心分析原则、输出要求）作为 `skill_rules_content` 写入 state，确保模型有基本行为指引。
- **加载 SKILL.md 正文**：`resolve_skill` 读取 SKILL.md 的 frontmatter 之后的正文，与 `references/fast.md` 或 `expert.md` 合并后写入 `skill_rules_content`。
- **增强 `_extract_last_visible_answer` 兜底**：当所有 AIMessage 都带 `tool_calls` 时，提取最后一条 AIMessage 的文本内容作为兜底答案，而非返回空字符串。

## Capabilities

### New Capabilities
- `react-loop-routing`: 简易 ReAct 循环路由——工具执行后直接回 `call_model`，由 `route_after_model` 统一决策，迭代计数准确

### Modified Capabilities
- `skill-content-basic`: Skill 内容加载范围扩展——需包含 SKILL.md 正文 + references 文件的合并内容，以及匹配失败时的降级提示词

## Impact

- `src/data_analysis/graph.py`：图结构变更（边和节点调整）
- `src/data_analysis/nodes.py`：`resolve_skill` 加载逻辑、路由函数、`_extract_last_visible_answer` 兜底
- `src/data_analysis/prompt_builder.py`：`build_system_prompt` 降级提示词支持
- `src/data_analysis/state.py`：`MAX_ITERATIONS` 语义确认
