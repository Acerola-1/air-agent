# data_analysis LangGraph 节点流重构设计

> 日期：2026-06-14
> 分支：`dev_data_analysis_upgrade`
> 状态：设计稿

## 1. 背景与问题

`data_analysis` 模块当前使用 `create_deep_agent` 构建 agent loop，每次请求至少 4 轮模型调用：

1. 模型看到 `find_skill` → 调用 `find_skill`
2. 模型读取 SKILL.md + references/fast.md
3. 模型决定调用 MCP 业务工具
4. 模型基于工具结果组织输出

每轮模型调用都经过 DeepAgents 完整中间件链（12 个中间件），其中 `SkillToolRegistryMiddleware` 每轮刷新 MCP 工具，`TimeContextMiddleware` 每轮注入时间上下文，`SkillToolFilterMiddleware` 每轮过滤工具列表——大量开销是重复且不必要的。

而 `data_analysis` 的场景高度确定性：菜单 ID → 固定 Skill → 固定工具调用 → 固定输出格式，模型自由度低、很少偏离 Skill 规则。

## 2. 设计目标

| 指标 | 当前 | 目标 |
|------|------|------|
| 模型调用轮数 | 4 轮 | 2-3 轮（含 REACT 循环） |
| 中间件链开销 | 每轮 12 个中间件 | 0（逻辑内嵌节点） |
| Skill 匹配 | 1 轮模型调用 | 确定性节点，0 模型调用 |
| Skill 规则读取 | 1-2 轮模型调用（read_file） | 确定性节点，0 模型调用 |
| 反思/重试 | 自由 agent loop | 简单 REACT 循环，max_iterations=3 |
| 最终输出 | FinalOutputCleanupMiddleware（after_agent 钩子） | finalize_output 节点，复用清理逻辑 |

## 3. 整体架构

用 `StateGraph` 构建一个带简单 REACT 循环的确定性节点流：

```
START
  → resolve_skill        确定性：menu_name → Skill 路径 + allowed-tools + 规则内容
  → prepare_model        确定性：组装 messages + 过滤工具 + 注入上下文
  → call_model           LLM：决定调工具 / 直接输出
  → route_after_model    条件边：有 tool_calls → execute_tools；无 → finalize_output
  → execute_tools        ToolNode：执行 MCP 工具（含重试、进度推送、富输出、异常兜底）
  → route_after_tools    条件边：循环回 prepare_model（受 max_iterations 限制）或 → finalize_output
  → finalize_output      LLM：流式清理最终答案 + stream_writer 推送
  → END
```

### 3.1 与当前架构的对比

```
当前 (DeepAgents agent loop):
  START → [before_agent] → [before_model] → model → [after_model] → (tool_calls?)
                                                                    → YES → tools → [before_model] → model → ...
                                                                    → NO  → [after_agent] → END
  每轮经过：SkillToolRegistry → ToolProgress → GlobalException → MCPResilience
            → TimeContext → RichOutput → FinalOutputCleanup → ModeRouting
            → CodeInterpreter → SkillToolFilter

新设计 (StateGraph 节点流):
  START → resolve_skill → prepare_model → call_model → (tool_calls?)
                                                          → YES → execute_tools → prepare_model → ...  (max 3 次)
                                                          → NO  → finalize_output → END
  关键逻辑内嵌节点，无中间件链遍历开销
```

## 4. 节点详细设计

### 4.1 `resolve_skill` — 确定性 Skill 解析

**职责**：
1. 从 `config["configurable"]["menu_name"]` 取菜单名
2. 用 `match_menu_skill(menu_name)` 确定性匹配 Skill
3. 读取 SKILL.md frontmatter → 解析 `allowed-tools`
4. 根据 `config["configurable"]["mode"]`（`fast` / `expert`）确定规则文件
5. 读取规则文件全文（`references/fast.md` 或 `references/expert.md`）
6. 将所有信息写入 state

**输入**：`config["configurable"]` 中的 `menu_name`、`mode`
**输出**：state 更新

```python
def resolve_skill(state: DataAnalysisState, config: RunnableConfig) -> dict:
    configurable = config.get("configurable", {})
    menu_name = str(configurable.get("menu_name") or configurable.get("module") or "").strip()
    mode = get_routing_context(configurable).mode  # "fast" 或 "expert"

    matched = match_menu_skill(menu_name)

    if matched is None:
        return {
            "selected_skill": None,
            "selected_skill_path": None,
            "selected_skill_description": None,
            "selected_skill_allowed_tools": [],
            "skill_rules_content": "",
            "skill_search_attempted": True,
            "skill_search_question": "",
        }

    skill_dir = SKILLS_DIR / matched.skill_name
    skill_file = skill_dir / "SKILL.md"
    allowed_tools = _load_allowed_tools(skill_file)  # 复用现有函数

    # 确定规则文件
    rules_file = skill_dir / "references" / ("fast.md" if mode != "expert" else "expert.md")
    if not rules_file.exists():
        rules_file = skill_dir / "references" / "fast.md"
    skill_rules = rules_file.read_text(encoding="utf-8") if rules_file.exists() else ""

    skill_path = f"/skills/{matched.skill_name}/SKILL.md"

    return {
        "selected_skill": matched.skill_name,
        "selected_skill_path": skill_path,
        "selected_skill_description": matched.title,
        "selected_skill_allowed_tools": allowed_tools,
        "skill_rules_content": skill_rules,
        "skill_search_attempted": True,
        "skill_search_question": "",
    }
```

**复用**：`match_menu_skill()`、`_load_allowed_tools()` 来自现有 `data_analysis/skill_discovery.py`。

**失配处理**：`allowed_tools` 为空，`skill_rules_content` 为空，后续节点按通用能力降级。

### 4.2 `prepare_model` — 确定性模型准备

**职责**：
1. 构建系统提示词（`SYSTEM_PROMPT` + output_guard + Skill 规则 + 时间上下文 + mode 上下文）
2. 过滤 MCP 工具列表——只保留 `selected_skill_allowed_tools` 中的业务工具
3. 刷新 MCP 工具（确保运行时可用）
4. 组装 `bind_tools` 调用参数

**输入**：state（含 `selected_skill_allowed_tools`、`skill_rules_content`）
**输出**：state 更新（`available_tools`、`system_prompt`）

```python
async def prepare_model(state: DataAnalysisState, config: RunnableConfig) -> dict:
    # 1. 刷新 MCP 工具
    await mcp_client.ensure_mcp_tools()

    # 2. 构建系统提示词
    system_prompt = with_data_analysis_output_guard(SYSTEM_PROMPT)

    # 注入时间上下文
    time_context = _build_time_context()
    system_prompt = f"{system_prompt}\n\n{time_context}"

    # 注入 mode 上下文
    routing = get_routing_context(config.get("configurable", {}))
    mode_context = routing.prompt
    if routing.mode == "expert":
        mode_context = f"{mode_context}\n\n{EXPERT_MODE_SYSTEM_PROMPT}"
    else:
        mode_context = f"{mode_context}\n\n{FAST_MODE_SYSTEM_PROMPT}"
    system_prompt = f"{system_prompt}\n\n{mode_context}"

    # 注入 Skill 规则
    skill_rules = state.get("skill_rules_content", "")
    if skill_rules:
        system_prompt = f"{system_prompt}\n\n{skill_rules}"

    # 3. 过滤工具
    all_business_tools = get_business_tools()
    allowed = set(state.get("selected_skill_allowed_tools") or [])
    available_tools = [
        t for t in all_business_tools
        if getattr(t, "name", None) in allowed
    ]

    return {
        "system_prompt": system_prompt,
        "available_tools": available_tools,
    }
```

### 4.3 `call_model` — LLM 推理

**职责**：调用模型，决定是调用工具还是直接输出

**输入**：state（含 `messages`、`system_prompt`、`available_tools`）
**输出**：`{"messages": [AIMessage]}`

```python
async def call_model(state: DataAnalysisState, config: RunnableConfig) -> dict:
    model = ModelRegistry.mimo_v2_5_pro
    system_prompt = state.get("system_prompt", "")
    available_tools = state.get("available_tools", [])

    messages = state["messages"]
    if system_prompt:
        messages = [SystemMessage(content=system_prompt)] + list(messages)

    model_with_tools = model.bind_tools(available_tools) if available_tools else model
    response = await model_with_tools.ainvoke(messages)

    return {"messages": [response]}
```

### 4.4 `route_after_model` — 条件边

```python
def route_after_model(state: DataAnalysisState) -> Literal["execute_tools", "finalize_output"]:
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        iteration = state.get("iteration_count", 0)
        if iteration < MAX_ITERATIONS:
            return "execute_tools"
        # 超过最大轮次，强制进入输出
        return "finalize_output"
    return "finalize_output"
```

### 4.5 `execute_tools` — 工具执行

使用 LangGraph `ToolNode`，但通过 `wrap_tool_call` / `awrap_tool_call` 注入以下逻辑（等效于原中间件链的工具侧逻辑）：

| 原中间件 | 新实现位置 | 逻辑 |
|----------|-----------|------|
| `ToolProgressMiddleware` | `awrap_tool_call` | 工具调用前推送 `progress` 事件 |
| `RichOutputMiddleware` | `awrap_tool_call` | 工具调用后推送 `rich_output` 事件 + 压缩 ToolMessage |
| `GlobalExceptionMiddleware` | `awrap_tool_call` | 捕获异常返回降级 ToolMessage |
| `MCPResilienceMiddleware` | `awrap_tool_call` | MCP 工具失败返回 503 降级 ToolMessage |
| `SkillToolRegistryMiddleware` | `awrap_tool_call` | 运行时动态绑定工具实例 |

这些逻辑通过组合成一个 `awrap_tool_call` 函数传入 `ToolNode`：

```python
async def composed_tool_wrapper(request, handler):
    # 1. 推送进度
    _push_tool_progress(request)

    # 2. 动态绑定工具实例
    request = _bind_tool_instance(request)

    # 3. 执行工具（含异常兜底）
    try:
        result = await handler(request)
    except Exception as exc:
        if _is_mcp_tool(request):
            result = _mcp_fallback(request, exc)
        else:
            result = _global_fallback(request, exc)

    # 4. 处理富输出
    result = _handle_rich_output(request, result)

    return result

tool_node = ToolNode(
    tools=[...],
    handle_tool_errors=True,
    awrap_tool_call=composed_tool_wrapper,
)
```

### 4.6 `route_after_tools` — 条件边

```python
def route_after_tools(state: DataAnalysisState) -> Literal["prepare_model", "finalize_output"]:
    iteration = state.get("iteration_count", 0) + 1
    # 循环计数在 state 更新时递增
    if iteration >= MAX_ITERATIONS:
        return "finalize_output"
    return "prepare_model"
```

**注意**：`iteration_count` 在 `execute_tools` 节点返回时递增，通过 state reducer 实现。

### 4.7 `finalize_output` — 最终输出清理与推送

**职责**：等效于 `FinalOutputCleanupMiddleware` 的 `aafter_agent` 逻辑

1. 从 messages 中提取最后一条可见 AI 答案（无 tool_calls 的 AIMessage）
2. 通过 `stream_writer` 推送 `progress` 事件
3. 调用 finalizer 模型流式清理答案
4. 逐 chunk 推送 `final_output_delta` 事件
5. 推送 `final_output_done` 事件

```python
async def finalize_output(state: DataAnalysisState, config: RunnableConfig) -> dict:
    writer = ...  # 从 LangGraph runtime 获取 stream_writer

    answer = _extract_last_visible_answer(state)
    if not answer:
        return {}

    writer({"node": "final_output_cleanup", "type": "progress", "message": "正在整理最终答案"})

    try:
        cleaned = await _stream_cleaned_answer(answer=answer, writer=writer)
    except Exception:
        return {}

    if not cleaned:
        return {}

    return {}  # 最终输出已通过 stream_writer 推送，不修改 messages
```

**关键问题**：如何在 StateGraph 节点中获取 `stream_writer`？

LangGraph 节点函数签名支持 `writer: StreamWriter` 参数注入：

```python
from langgraph.types import StreamWriter

async def finalize_output(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict:
    ...
```

`StreamWriter` 是一个 `Callable[[dict], None]`，调用即可推送自定义事件到前端。这个机制在 `StateGraph` 节点中天然可用，不需要 DeepAgents 的 `runtime.stream_writer`。

## 5. State Schema 设计

```python
from typing import Annotated, NotRequired, Required
from langgraph.graph.message import add_messages

MAX_ITERATIONS = 3

class DataAnalysisState(TypedDict):
    """data_analysis 节点流状态."""
    messages: Annotated[list[AnyMessage], add_messages]
    # Skill 相关
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    skill_rules_content: NotRequired[str]
    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str]
    # 模型调用相关
    system_prompt: NotRequired[str]
    available_tools: NotRequired[list[BaseTool | dict[str, Any]]]
    # 循环控制
    iteration_count: NotRequired[int]
```

**说明**：
- `messages` 使用 `add_messages` reducer，与 LangGraph `MessagesState` 一致
- `iteration_count` 不设 reducer，每次节点返回时覆盖（`LastValue` 默认行为）
- 不使用 DeepAgents 的 `DeltaChannel`，因为我们控制了消息量（不需要 O(N) checkpoint 优化）
- 不需要 `remaining_steps`，我们自己通过 `iteration_count` + `MAX_ITERATIONS` 控制循环

## 6. Skill 系统设计

### 6.1 核心变化

| 决策点 | 当前 (DeepAgents) | 新设计 (StateGraph) |
|--------|-------------------|---------------------|
| Skill 匹配 | 模型调用 `find_skill` 工具（1 轮 LLM） | `resolve_skill` 确定性节点（0 轮 LLM） |
| Skill 规则获取 | 模型通过 `read_file` 读取（1-2 轮 LLM） | `resolve_skill` 节点直接读取文件，`prepare_model` 注入 messages |
| 工具披露 | `find_skill` 返回 Command(update=...)，中间件据此过滤 | `resolve_skill` 写入 `selected_skill_allowed_tools`，`prepare_model` 过滤 |
| Skill 规则存储 | 依赖 FilesystemBackend | state 字段 `skill_rules_content` |

### 6.2 不再需要的组件

- **`find_skill` 工具**：`data_analysis` 的菜单映射完全确定性，不需要模型参与匹配
- **`FilesystemBackend`**：不再需要模型读取文件
- **`SkillToolRegistryMiddleware`**：MCP 工具刷新移到 `prepare_model` 节点
- **`SkillToolFilterMiddleware`**：工具过滤移到 `prepare_model` 节点
- **`CodeInterpreterMiddleware`**：`data_analysis` 不使用代码解释

### 6.3 保留不变的组件

- **SKILL.md + references/fast.md + references/expert.md**：文件结构完全不变
- **`menu_skill_mapping.py`**：完全复用
- **`skill_discovery.py` 中的辅助函数**：`_load_allowed_tools`、`_exposed_skill_path` 等可复用

### 6.4 失配处理

当 `match_menu_skill()` 返回 `None` 时：
- `selected_skill_allowed_tools` 设为空
- `skill_rules_content` 设为空
- `call_model` 只看到系统提示词 + 页面数据，无业务工具
- 模型基于通用能力回答（等效于当前 `unmatched_policy="strict"`）

## 7. 最终输出与前端推送机制

### 7.1 当前机制回顾

DeepAgents 的输出推送依赖 `runtime.stream_writer`，6 种事件类型：

| 事件 type | 来源中间件 | 用途 |
|-----------|-----------|------|
| `progress` | ToolProgressMiddleware | 工具调用进度 |
| `rich_output` | RichOutputMiddleware | 图表/附件 |
| `final_output_delta` | FinalOutputCleanupMiddleware | 流式清理答案 |
| `final_output_done` | FinalOutputCleanupMiddleware | 完整清理答案 |
| `external_service_status` | MCPResilienceMiddleware | MCP 服务降级通知 |
| `expanded_questions` | ExpandQuestionMiddleware | 推荐追问（data_analysis 不用） |

### 7.2 新设计的事件推送

| 事件 type | 新实现位置 | 推送方式 |
|-----------|-----------|---------|
| `progress` | `execute_tools` 的 `awrap_tool_call` | `writer(event)` via StreamWriter |
| `rich_output` | `execute_tools` 的 `awrap_tool_call` | `writer(event)` via StreamWriter |
| `final_output_delta` | `finalize_output` 节点 | `writer(event)` via StreamWriter |
| `final_output_done` | `finalize_output` 节点 | `writer(event)` via StreamWriter |
| `external_service_status` | `execute_tools` 的 `awrap_tool_call` | `writer(event)` via StreamWriter |

**关键变化**：从 `runtime.stream_writer`（DeepAgents 提供）切换到 `StreamWriter`（LangGraph 节点参数注入）。

`StreamWriter` 在节点函数中通过关键字参数获取：

```python
async def my_node(state: State, *, writer: StreamWriter) -> dict:
    writer({"type": "progress", "message": "..."})
```

对于 `ToolNode` 中的 `awrap_tool_call`，`StreamWriter` 需要通过 `request.runtime.stream_writer` 传入。`ToolNode` 内的 `ToolRuntime` 对象天然包含 `stream_writer`，与 DeepAgents 一致。

### 7.3 前端兼容性

所有事件格式保持不变——前端无需修改。只是推送来源从中间件变为节点/工具包装器。

## 8. 错误处理

| 场景 | 当前处理 | 新设计处理 |
|------|---------|-----------|
| 模型调用失败 | `GlobalExceptionMiddleware.wrap_model_call` → 返回 AIMessage 兜底 | `call_model` 节点 try/except → 返回 AIMessage 兜底 |
| MCP 工具失败 | `MCPResilienceMiddleware.awrap_tool_call` → 返回 503 降级 ToolMessage | `composed_tool_wrapper` 内 try/except → 返回 503 降级 ToolMessage |
| 非 MCP 工具失败 | `GlobalExceptionMiddleware.awrap_tool_call` → 返回 500 降级 ToolMessage | `composed_tool_wrapper` 内 try/except → 返回 500 降级 ToolMessage |
| Skill 匹配失败 | `find_skill` 返回 `matched=False` | `resolve_skill` 返回 `selected_skill=None` |
| 规则文件缺失 | 模型降级回答 | `skill_rules_content=""`，模型降级回答 |
| finalizer 失败 | `FinalOutputCleanupMiddleware` 退化使用原始 messages | `finalize_output` 退化使用原始 messages |

## 9. 其他图不受影响

本设计仅重构 `data_analysis` 图。其他 5 个图（`basic-qa`、`intelligent-analysis`、`deep-research`、`intelligent-report`、`intelligent-tracing`）继续使用 `create_deep_agent` + 语义路由 `find_skill`，不需要任何修改。

`langgraph.json` 中 `data-analysis` 的入口改为指向新图：

```json
{
  "data-analysis": {
    "path": "src/data_analysis/graph.py",
    "getter": "graph"
  }
}
```

入口路径不变，只是 `graph.py` 内部实现变了。

## 10. 不做的事（YAGNI）

- **不引入本地 embedding 模型**（那是 `find_skill` 的优化，与本重构无关）
- **不修改 SKILL.md / references 文件格式**（完全复用现有 Skill 编写规范）
- **不为 data_analysis 增加语义路由**（菜单映射足够）
- **不做混合方案**（回退到 DeepAgents agent loop 过于复杂）
- **不修改其他图的实现**（各自独立）
- **不使用 `DeltaChannel`**（消息量可控，`add_messages` 足够）
