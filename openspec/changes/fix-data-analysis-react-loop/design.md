## Context

data_analysis 模块从 DeepAgent（create_deep_agent + 中间件链）迁移到 LangGraph StateGraph 节点流。当前图结构：

```
START → resolve_skill → prepare_model → call_model ⇄ execute_tools → increment_iteration → finalize_output → END
```

核心问题在于 `route_after_tools`（increment_iteration 之后）无条件路由回 `prepare_model`，只检查迭代计数不看模型是否还需要调用工具。加上 off-by-one（`_increment_iteration` 和 `route_after_tools` 各 +1 一次），实际只允许 2 轮而非 3 轮工具调用。

旧版 DeepAgent 的 ReAct 循环由框架 agent loop 管理：模型无 tool_calls 时自动结束，无需显式路由函数。新版需要自行实现等效逻辑。

约束：
- `data-analysis` 的 `SkillToolFilterMiddleware` 使用 `unmatched_policy="strict"`，匹配失败时不暴露业务工具
- Skill 规则文件（fast.md / expert.md）较长，需要完整注入 system_prompt
- MCP 工具是运行时动态加载的，图编译时 ToolNode 的工具列表是快照

## Goals / Non-Goals

**Goals:**
- 修复 ReAct 循环，使 `call_model` 无 `tool_calls` 时正确终止到 `finalize_output`
- 修复迭代计数 off-by-one
- Skill 匹配失败时提供降级系统提示词
- 加载 SKILL.md 正文并与 references 文件合并注入上下文
- 增强答案提取兜底，消除"无 output"问题

**Non-Goals:**
- 不改变 `unmatched_policy="strict"` 策略（匹配失败不暴露工具）
- 不重构 ToolNode 工具列表动态更新机制（`composed_tool_wrapper` 的动态绑定已覆盖）
- 不改变 Skill 文件结构或内容
- 不引入 `find_skill` 工具调用（新版使用确定性 `resolve_skill` 节点替代）

## Decisions

### D1: 去掉 `route_after_tools`，简化循环结构

**决策**：将图结构从：

```
call_model → [route_after_model] → execute_tools → increment_iteration → [route_after_tools] → prepare_model/call_model
```

改为：

```
call_model → [route_after_model] → execute_tools → call_model（循环）
                                        ↓
                              prepare_model（仅 resolve_skill 后执行一次）
```

具体：`execute_tools` 后直接回到 `call_model`（不再经过 `prepare_model`），由 `route_after_model` 统一判断是否有 `tool_calls`：
- 有 `tool_calls` 且未超迭代 → `execute_tools`
- 无 `tool_calls` 或超迭代 → `finalize_output`

`prepare_model` 只在 `resolve_skill → prepare_model → call_model` 的首次调用前执行，不在循环中重复。

**理由**：`prepare_model` 的两个职责——MCP 刷新和 system_prompt 组装——在循环内重复执行是浪费。MCP 刷新只需首次调用前执行一次（MCP 工具在请求生命周期内不会变化）；system_prompt 在同一轮对话中不变。

**替代方案**：保留 `route_after_tools` 但增加 `tool_calls` 检查。拒绝原因：增加了不必要的复杂度，且没有解决 `prepare_model` 重复执行的问题。

### D2: 迭代计数在 `route_after_model` 中统一判断

**决策**：`_increment_iteration` 节点保留（在 `execute_tools` 后执行），但 `route_after_model` 直接读取 `iteration_count` 与 `MAX_ITERATIONS` 比较，不再额外 +1。

**理由**：`_increment_iteration` 每次 execute_tools 后 +1，`route_after_model` 读到的就是"已完成的工具调用轮数"，语义清晰。

### D3: Skill 匹配失败降级——复用旧版 SYSTEM_PROMPT

**决策**：`resolve_skill` 匹配失败时，将 `prompt_builder.SYSTEM_PROMPT`（含角色职责、核心分析原则、输出要求）作为 `skill_rules_content` 写入 state，而非空字符串。

**理由**：旧版 DeepAgent 的 `SYSTEM_PROMPT` 始终存在，是模型的基本行为指引。新版在匹配失败时丢失了这些指引，导致模型无规则可循。

### D4: SKILL.md 正文与 references 文件合并注入

**决策**：`resolve_skill` 读取 SKILL.md frontmatter 之后的正文，以分隔符与 `references/fast.md` 或 `expert.md` 合并后写入 `skill_rules_content`。

格式：
```
{SKILL.md 正文}

---

{references/fast.md 或 expert.md 内容}
```

**理由**：SKILL.md 正文包含跨 mode 共享的刚性约束（执行边界、角色范围限制），丢失可能导致模型违反基本约束。

### D5: `_extract_last_visible_answer` 兜底增强

**决策**：当所有 AIMessage 都带 `tool_calls` 时，提取最后一条 AIMessage 的 `content` 文本作为兜底。

**理由**：迭代超限时最后一条 AIMessage 可能同时包含 `tool_calls` 和 `content`（某些模型会在同一响应中既输出文本又请求工具调用）。丢弃这个 `content` 会导致空答案。

## Risks / Trade-offs

- **[prepare_model 不在循环中]** → MCP 工具在请求生命周期内通常不会变化，但如果 MCP 服务器中途重启新增工具，循环内不会刷新。缓解：MCP 刷新在首次 `prepare_model` 中执行，且 `composed_tool_wrapper` 的动态绑定可覆盖运行时新增工具。
- **[SKILL.md 正文 + references 合并后 system_prompt 变长]** → 增加上下文 token 消耗。缓解：SKILL.md 正文通常较短（~20 行），增量可控。
- **[兜底提取带 tool_calls 的 AIMessage 内容]** → 可能提取到模型的中途思考而非最终答案。缓解：这是最后手段，只在正常提取路径失败时触发，且 `_stream_cleaned_answer` 会对内容做最终清理。
