## Context

主流程共享提示词 (`prompts.py`) 中存在三类约束：

1. **工具调用绝对静默**：`MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中的"工具调用静默协议"段落
2. **不暴露内部类**：`TOOL_FAILURE_SILENCE_PROTOCOL`、"严禁泄露内部信息""严禁过程化自述""严禁进度提示""不得提及工具或内部步骤"等
3. **业务规范**：Markdown 格式、国标、单位、日期、同比规则等

其中第 1、2 类与权限修正披露存在冲突，且第 2 类职责应由 `FinalOutputCleanupMiddleware` 统一承担。`prompts.py` 已在之前的变更中精简过，但上述冗余约束仍残留。

本变更依赖一个展示链路前提：前端用户可见正文只消费 `FinalOutputCleanupMiddleware` 推送的 `final_output_delta` / `final_output_done` 事件，不直接渲染主流程中间 `AIMessage.content`、`tool_calls`、`ToolMessage` 或 LangGraph 更新事件。因此，主流程中间轮是否产生自然语言不构成前端泄露风险，最终可见输出清理由 final cleanup 统一完成。

## Goals / Non-Goals

**Goals:**

- 移除提示词中所有工具调用绝对静默约束，避免权限说明被阻止
- 移除提示词中所有"不暴露内部"类约束（这些职责由 FinalOutputCleanupMiddleware 承担）
- 补强 FinalOutputCleanupMiddleware 对工具错误消息、状态码、超时和失败原因的清理提示
- 保留业务规范段落和"内部机制追问处理"段落
- 强化权限上下文注入，使 `correction_text` 成为用户可见权限说明的主要依据，`reason` 作为自然语言解释的辅助依据

**Non-Goals:**

- 不引入确定性最终拼接保底
- 不修改权限规则引擎或 `PermissionResult` 字段结构
- 不修改 `DATA_AUTHENTICITY_GUARD`、`ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE`、`TIME_PARAMETER_NORMALIZATION_GUIDE`、`AIR_QUALITY_UNIT_NORMALIZATION_GUIDE`、`POLLUTANT_LEVEL_BOUNDARY_GUIDE` 等业务提示词
- 不改变前端事件消费协议；前端仍只需消费 `final_output_delta` / `final_output_done` 作为最终正文

## Decisions

### Decision 1: 删除 TOOL_FAILURE_SILENCE_PROTOCOL 整段

**选择**：删除整段，不保留任何"工具失败时禁止输出错误信息"的提示词约束。

**理由**：该协议属于"不暴露内部"类约束——禁止输出工具名、错误类型、502/500/timeout 等。按用户确认的原则，这类约束统一由 `FinalOutputCleanupMiddleware` 承担，提示词层不应出现。

### Decision 2: 保留"内部机制追问处理"段落

**选择**：保留 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 中"内部机制追问处理"段落。

**理由**：这段不是"不暴露内部"的一般性禁止，而是用户主动追问内部机制时的业务口径转换规则（将内部机制问题转换为业务能力回答）。这是业务层面的交互策略，不是输出清理职责。

### Decision 3: 提示词层职责重新划分

**选择**：主流程共享提示词只管业务生成职责，不再承担通用最终输出清理职责。

```
之前：主流程提示词同时管业务生成和最终输出清理
      → 与 FinalOutputCleanupMiddleware 职责重叠，且与权限披露冲突

之后：主流程提示词 → 业务规范 + 查询规范 + 权限披露 + 内部机制追问业务口径
      FinalOutputCleanupMiddleware → 唯一用户可见正文的内部信息、过程信息和工具错误清理
```

### Decision 4: 权限说明以 correction_text 为主，reason 为辅

**选择**：`permission_result.correction_text` 是用户可见权限披露的主要文案依据；`permission_result.reason` 仅作为生成"为什么被拦截或修正"的辅助依据，不要求按字段名或原始结构输出。

**理由**：`correction_text` 由权限规则确定性模板生成，更适合用户直接理解；`reason` 可能包含规则引擎或聚合原因，机械输出会增加内部字段感。

## Risks / Trade-offs

- [Risk] 移除静默约束后，模型可能在中间工具调用轮输出自然语言 → Mitigation：前端只消费 `FinalOutputCleanupMiddleware` 的最终正文事件；主流程中间消息不作为用户可见聊天正文渲染
- [Risk] 移除"不暴露内部"提示词约束后，模型可能更倾向于输出内部细节 → Mitigation：`FinalOutputCleanupMiddleware` 在代码层确定性地清理；且"内部机制追问处理"段落仍保留，引导模型对用户主动追问转为业务语言
- [Risk] 仅靠提示词仍无法 100% 保证权限披露 → 该风险被接受，本变更明确不做确定性最终拼接保底
