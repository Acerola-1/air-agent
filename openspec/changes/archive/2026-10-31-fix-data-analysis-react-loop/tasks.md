## 1. 修复 ReAct 循环路由结构

- [x] 1.1 修改 `graph.py`：去掉 `route_after_tools` 导入和 `increment_iteration → route_after_tools` 条件边，改为 `execute_tools → increment_iteration → call_model` 确定性边
- [x] 1.2 修改 `graph.py`：去掉 `resolve_skill → prepare_model → call_model` 中的 `prepare_model` 在循环中的重复执行，确保 `prepare_model` 只在 `resolve_skill` 后执行一次
- [x] 1.3 修改 `nodes.py`：删除 `route_after_tools` 函数
- [x] 1.4 修改 `nodes.py`：修复 `route_after_model` 中迭代计数判断，直接使用 `iteration_count` 与 `MAX_ITERATIONS` 比较，不再额外 +1

## 2. 修复 Skill 内容加载

- [x] 2.1 修改 `nodes.py` 的 `resolve_skill`：读取 SKILL.md frontmatter 之后的正文内容
- [x] 2.2 修改 `nodes.py` 的 `resolve_skill`：将 SKILL.md 正文与 references/fast.md 或 expert.md 以分隔符 `---` 合并后写入 `skill_rules_content`
- [x] 2.3 修改 `nodes.py` 的 `resolve_skill`：匹配失败时将 `prompt_builder.SYSTEM_PROMPT` 作为 `skill_rules_content` 写入 state

## 3. 修复答案提取兜底

- [x] 3.1 修改 `nodes.py` 的 `_extract_last_visible_answer`：当所有 AIMessage 都带 `tool_calls` 时，提取最后一条 AIMessage 的 `content` 文本作为兜底答案

## 4. 验证

- [x] 4.1 运行 `ruff check` 和 `ruff format` 检查修改的文件
- [x] 4.2 运行 `python3 -m compileall` 验证语法正确性
- [x] 4.3 运行相关单元测试（如有）
