# Deep Agents 中间件系统详解

## 一、中间件执行顺序（核心架构）

Deep Agents 使用**洋葱模型**（洋葱圈架构），请求从外层进入，响应从内层返回。

### 主代理的中间件栈（完整顺序）

```
harmess/deep_agents_source/libs/deepagents/deepagents/graph.py#L400-643

1. TodoListMiddleware              ← 任务规划（最先执行）
   ↓
2. SkillsMiddleware                ← 加载技能元数据
   ↓
3. FilesystemMiddleware            ← 文件操作工具注入
   ↓
4. SubAgentMiddleware              ← 子代理管理（task 工具）
   ↓
5. SummarizationMiddleware         ← 上下文自动压缩
   ↓
6. PatchToolCallsMiddleware        ← 工具调用优化
   ↓
7. AsyncSubAgentMiddleware         ← 异步子代理（如果有）
   ↓
8. [用户自定义中间件]               ← 你的定制层
   ↓
9. [Provider-specific middleware]  ← 模型特定优化
   ↓
10. _ToolExclusionMiddleware       ← 工具过滤
    ↓
11. AnthropicPromptCachingMiddleware ← 提示词缓存
    ↓
12. MemoryMiddleware               ← 持久化记忆加载
    ↓
13. HumanInTheLoopMiddleware       ← 人机交互审批
    ↓
14. _PermissionMiddleware          ← 权限控制（最后执行）
```

**关键点**：

- 权限中间件在最后，能看到所有工具（包括其他中间件注入的）
- 记忆中间件在用户中间件之后，保证用户定制不会被缓存失效
- 子代理中间件在中间位置，既可调用文件工具，又能被权限控制

---

## 二、每个中间件的详细功能

### 1. **TodoListMiddleware（任务规划）**

**功能**：

- 提供 `write_todos` 工具
- 自动跟踪任务进度
- 支持任务分解和状态管理

**工作原理**：

```python
# 自动注入到系统提示词
## Task Management
- Break complex tasks into smaller steps
- Track progress using write_todos
- Update status as you complete each step
```

**作为子图时**：

- ✅ 子代理独立拥有自己的 TodoListMiddleware
- ✅ 子代理的任务列表**不共享**给父代理（PrivateState）
- ✅ 父代理无法看到子代理的内部任务进度

---

### 2. **SkillsMiddleware（技能系统）**

**功能**：

- 加载 SKILL.md 文件
- 渐进式披露（先加载元数据，按需加载内容）
- 注入技能指令到系统提示词

**工作原理**：

```python
harmess/deep_agents_source/libs/deepagents/deepagents/middleware/skills.py#L200-400

# 第一步：加载元数据
skills_metadata = [
    {
        "name": "data-analysis",
        "description": "数据分析技能",
        "path": "/skills/data-analysis/SKILL.md"
    }
]

# 第二步：注入到系统提示词
<available_skills>
- data-analysis: 数据分析技能（点击查看详情）
</available_skills>

# 第三步：按需加载完整内容（模型请求时）
<skill name="data-analysis">
完整的技能指令...
</skill>
```

**作为子图时**：

- ✅ 子代理可以配置**独立的 skills**（不继承父代理的）
- ✅ 子代理的技能元数据存储在 `skills_metadata`（PrivateState）
- ✅ 父代理无法访问子代理加载的技能内容
- ⚠️ 如果子代理未指定 skills，则**不加载任何技能**

**示例**：

```python
# 父代理有 data-analysis 和 code-review 技能
main_agent = create_deep_agent(
    skills=["./skills/data-analysis/", "./skills/code-review/"]
)

# 子代理只继承 web-search 技能
researcher = SubAgent(
    name="researcher",
    skills=["./skills/web-search/"],  # 独立配置，不继承父代理
)
```

---

### 3. **FilesystemMiddleware（文件系统）**

**功能**：

- 提供 6 个核心文件工具：
  - `ls` - 列出目录
  - `read_file` - 读取文件（支持分页）
  - `write_file` - 写入文件
  - `edit_file` - 编辑文件
  - `glob` - 文件搜索
  - `grep` - 内容搜索

**工作原理**：

```python
harmess/deep_agents_source/libs/deepagents/deepagents/middleware/filesystem.py#L1-150

# 所有文件操作都通过 Backend 抽象
backend.ls(path="/workspace")          → 返回目录列表
backend.read_file(path="/file.txt")    → 返回文件内容
backend.write_file(path="/file.txt")   → 写入文件
```

**作为子图时**：

- ✅ 子代理共享**同一个 backend**
- ✅ 文件系统状态**可以共享**（通过 `files` state）
- ⚠️ 权限隔离由 PermissionMiddleware 控制
- ⚠️ 子代理可以访问父代理创建的文件

**文件共享示例**：

```python
# 父代理创建文件
main_agent.invoke({
    "messages": [("user", "创建 report.txt")]
})
# 状态: files = {"report.txt": FileData(...)}

# 子代理可以读取该文件
researcher.invoke({
    "messages": [("user", "读取 report.txt")]
})
# 能够访问到父代理创建的文件
```

---

### 4. **SubAgentMiddleware（子代理管理）**

**功能**：

- 提供 `task` 工具
- 动态调用子代理
- 管理子代理的生命周期
- 提取子代理结果并返回给父代理

**工作原理**：

```python
harmess/deep_agents_source/libs/deepagents/deepagents/middleware/subagents.py#L1-150

# 父代理调用子代理
task(
    subagent_type="researcher",
    description="研究 AI agents 的最新进展"
)

# SubAgentMiddleware 的工作流程：
1. 找到 researcher 子代理配置
2. 创建独立的子代理运行时
3. 注入 description 作为输入
4. 运行子代理直到完成
5. 提取子代理的最后一条消息
6. 作为 ToolMessage 返回给父代理
```

**作为子图时**：

- ✅ 这是**核心中间件**，负责子图通信
- ✅ 子代理的整个中间件栈都在子图内部执行
- ✅ 子代理的结果通过 ToolMessage 传回父图
- ⚠️ 子代理内部可以再调用子代理（嵌套）

**嵌套示例**：

```python
# 主代理
main_agent → SubAgentMiddleware

# 调用 researcher 子代理
researcher (子图) → 自己的中间件栈
    ├── TodoListMiddleware
    ├── SkillsMiddleware (web-search 技能)
    ├── FilesystemMiddleware
    └── SubAgentMiddleware (可以再调用子代理！)
        ↓
    # researcher 可以再调用 analyst 子代理
    analyst (孙图) → 自己的中间件栈
```

---

### 5. **SummarizationMiddleware（上下文压缩）**

**功能**：

- 自动检测对话长度
- 当超过阈值时自动压缩历史消息
- 保留关键信息，删除冗余内容

**工作原理**：

```python
# 监控 token 使用
if token_count > threshold:
    # 提取最近 N 条消息
    recent_messages = messages[-10:]
    
    # 调用模型生成摘要
    summary = model.invoke("总结之前的对话...")
    
    # 替换历史消息
    new_messages = [
        SystemMessage("之前对话的摘要：..."),
        *recent_messages
    ]
```

**作为子图时**：

- ✅ 子代理有**独立的压缩机制**
- ✅ 子代理的压缩不会影响父代理的对话历史
- ⚠️ 父代理看不到子代理的压缩摘要（隔离）

**压缩时机**：

```python
# 父代理对话：100 messages
main_agent: messages = [msg1, msg2, ..., msg100]

# 父代理调用 researcher 子代理
researcher.invoke({
    "messages": [("user", "研究任务")]
})

# 子代理内部产生 50 条消息后触发压缩
researcher内部: messages = [summary, msg45, ..., msg50]

# 子代理完成，返回结果给父代理
# 父代理的 messages 不受影响，仍然是 100+1 条
main_agent: messages = [msg1, ..., msg100, ToolMessage(result)]
```

---

### 6. **MemoryMiddleware（持久化记忆）**

**功能**：

- 加载 AGENTS.md 文件
- 注入持久化上下文（项目规范、用户偏好）
- 支持动态更新记忆（通过 edit_file）

**工作原理**：

```python
harmess/deep_agents_source/libs/deepagents/deepagents/middleware/memory.py

# 加载记忆文件
memory_contents = {
    "./AGENTS.md": "# 项目规范\n使用 Python 3.10+...",
    "~/.deepagents/AGENTS.md": "# 用户偏好\n喜欢 TypeScript..."
}

# 注入到系统提示词
<agent_memory>
./AGENTS.md
# 项目规范
使用 Python 3.10+...

~/.deepagents/AGENTS.md
# 用户偏好
喜欢 TypeScript...
</agent_memory>
```

**作为子图时**：

- ✅ 子代理可以配置**独立的 memory**
- ✅ 子代理的记忆内容存储在 `memory_contents`（PrivateState）
- ✅ 父代理无法访问子代理的记忆
- ⚠️ 默认情况下，子代理**不继承**父代理的记忆

**记忆隔离示例**：

```python
# 父代理的记忆
main_agent = create_deep_agent(
    memory=["./AGENTS.md"]  # 项目规范
)

# 子代理有专门的记忆
researcher = SubAgent(
    name="researcher",
    system_prompt="你是研究助手",
    # 没有配置 memory，所以不加载任何记忆
)

# 另一个子代理有自己的记忆
coder = SubAgent(
    name="coder",
    system_prompt="你是编程助手",
    memory=["./coder/AGENTS.md"],  # 编程规范
)
```

---

### 7. **HumanInTheLoopMiddleware（人机交互）**

**功能**：

- 在特定工具调用前暂停
- 等待人类审批或修改
- 支持中断恢复和继续执行

**工作原理**：

```python
# 配置中断规则
interrupt_on = {
    "execute": True,           # 执行命令前暂停
    "edit_file": {
        "when": "before",      # 编辑前暂停
        "prompt": "确认编辑？"
    }
}

# 执行流程
1. 代理调用 execute("rm -rf /")
2. HumanInTheLoopMiddleware 检测到中断规则
3. 暂停执行，等待人类响应
4. 人类批准/拒绝/修改
5. 根据人类响应继续或终止
```

**作为子图时**：

- ✅ 子代理可以配置**独立的中断规则**
- ⚠️ 子代理的中断**不会暂停父代理**
- ⚠️ 子代理的中断只影响子图内部

**中断继承规则**：

```python
# 父代理配置了中断
main_agent = create_deep_agent(
    interrupt_on={"execute": True}
)

# 子代理默认继承父代理的中断配置
researcher = SubAgent(
    name="researcher",
    # 不指定 interrupt_on，继承父代理的 {"execute": True}
)

# 子代理可以覆盖中断配置
coder = SubAgent(
    name="coder",
    interrupt_on={"write_file": True},  # 覆盖，只中断写文件
)

# 子代理可以禁用中断
analyzer = SubAgent(
    name="analyzer",
    interrupt_on={},  # 禁用所有中断
)
```

---

### 8. **PermissionMiddleware（权限控制）**

**功能**：

- 定义文件访问权限规则
- 控制哪些路径可以读/写
- 阻止危险操作

**工作原理**：

```python
# 定义权限规则
permissions = [
    FilesystemPermission(
        path="/workspace/**",
        allow_read=True,
        allow_write=True,
    ),
    FilesystemPermission(
        path="/etc/**",
        allow_read=False,
        allow_write=False,
    ),
]

# 检查权限œ
if tool_call == "read_file" and path == "/etc/passwd":
    # 匹配第二条规则，拒绝读取
    raise PermissionError("禁止访问 /etc/**")
```

**作为子图时**：

- ✅ 子代理可以配置**独立的权限规则**
- ⚠️ 子代理的权限规则**替换**父代理的规则（不继承）
- ⚠️ 子代理的权限更严格时，可以阻止访问父代理创建的文件

**权限隔离示例**：

```python
# 父代理可以访问整个 workspace
main_agent = create_deep_agent(
    permissions=[
        FilesystemPermission(path="/workspace/**", allow_read=True, allow_write=True)
    ]
)

# 父代理创建文件
main_agent.invoke({"messages": [("user", "创建 /workspace/secret.txt")]})

# 子代理只有读取权限（不能写入）
reader = SubAgent(
    name="reader",
    permissions=[
        FilesystemPermission(path="/workspace/**", allow_read=True, allow_write=False)
    ]
)

# 子代理可以读取父代理创建的文件
reader.invoke({"messages": [("user", "读取 /workspace/secret.txt")]})
# ✅ 成功读取

# 但子代理不能写入
reader.invoke({"messages": [("user", "修改 /workspace/secret.txt")]})
# ❌ PermissionError: 禁止写入 /workspace/**
```

---

## 三、作为子图时的关键行为

### 1. **状态隔离**

当 Deep Agents 作为子图集成时，**状态是隔离的**：

```python
# 父代理的状态
main_agent_state = {
    "messages": [...],
    "todos": [...],           # 父代理的任务列表
    "skills_metadata": [...], # 父代理的技能元数据（Private）
    "memory_contents": {...}, # 父代理的记忆内容（Private）
    "files": {...},           # 文件系统状态（可共享）
}

# 子代理的状态（完全独立）
subagent_state = {
    "messages": [...],        # 子代理的独立对话历史
    "todos": [...],           # 子代理的独立任务列表（Private）
    "skills_metadata": [...], # 子代理的独立技能（Private）
    "memory_contents": {...}, # 子代理的独立记忆（Private）
    "files": {...},           # 共享文件系统（如果 backend 相同）
}
```

**关键点**：

- ✅ `messages` 完全隔离（子代理的对话不影响父代理）
- ✅ `todos`, `skills_metadata`, `memory_contents` 都是 PrivateState（不传播）
- ⚠️ `files` 可以共享（取决于 backend 配置）

---

### 2. **中间件栈的独立执行**

子图内部有**完整的中间件栈**：

```
父图执行流程：
Main Request
    ↓
[TodoListMiddleware] ← 父代理的任务管理
    ↓
[SkillsMiddleware] ← 父代理的技能加载
    ↓
[SubAgentMiddleware] ← 调用子代理
    ↓
    进入子图 ──────────────────┐
                               │
    子图执行流程：              │
    Sub Request                │
        ↓                      │
    [TodoListMiddleware] ← 子代理的任务管理（独立）
        ↓                      │
    [SkillsMiddleware] ← 子代理的技能（独立）
        ↓                      │
    [FilesystemMiddleware] ← 子代理的文件工具（共享 backend）
        ↓                      │
    ... 其他中间件              │
        ↓                      │
    Model Call                 │
        ↓                      │
    Sub Response               │
                               │
    返回父图 ──────────────────┘
    ↓
提取子代理结果 → ToolMessage
    ↓
继续父图的中间件栈
    ↓
[MemoryMiddleware] ← 父代理的记忆
    ↓
[PermissionMiddleware] ← 父代理的权限检查
    ↓
Main Response
```

---

### 3. **工具调用流程**

当父代理调用子代理时，实际的工具调用流程：

```python
# 父代理调用 task 工具
task(
    subagent_type="researcher",
    description="研究 LangGraph 的最新特性"
)

# SubAgentMiddleware 处理流程：
harmess/deep_agents_source/libs/deepagents/deepagents/middleware/subagents.py#L1-150

1. 解析 task 参数
   - subagent_type: "researcher"
   - description: "研究任务描述"

2. 查找 researcher 配置
   - 找到 SubAgent spec
   - 获取 researcher 的 middleware 栈

3. 创建 researcher 运行时
   - 构建独立的 StateGraph
   - 加载 researcher 的中间件栈
   - 配置独立的 backend

4. 执行 researcher 子图
   researcher.invoke({
       "messages": [HumanMessage(description)]
   })

5. researcher 内部执行
   - researcher 的 TodoListMiddleware 处理任务
   - researcher 的 SkillsMiddleware 加载技能
   - researcher 的 FilesystemMiddleware 提供文件工具
   - researcher 的 Model 生成响应

6. 提取 researcher 的最后一条消息
   last_message = researcher_state["messages"][-1]

7. 作为 ToolMessage 返回给父代理
   ToolMessage(
       content=last_message.content,
       tool_call_id="task_xxx"
   )

8. 父代理继续执行
   - 父代理看到 ToolMessage 结果
   - 可以基于结果继续对话
```

---

### 4. **Backend 共享与隔离**

Backend（文件系统后端）的共享策略：

```python
# 方式 A：共享 Backend（推荐）
main_backend = FilesystemBackend(root_dir="/workspace")

main_agent = create_deep_agent(
    backend=main_backend,  # 共享 backend
)

researcher = SubAgent(
    name="researcher",
    # 不指定 backend，继承 main_backend
)

# 结果：
# ✅ 父代理和子代理共享同一个文件系统
# ✅ 父代理创建的文件，子代理可以访问
# ⚠️ 需要通过 PermissionMiddleware 控制访问权限


# 方式 B：独立 Backend（隔离）
main_backend = FilesystemBackend(root_dir="/workspace")
researcher_backend = FilesystemBackend(root_dir="/research")

researcher = SubAgent(
    name="researcher",
    backend=researcher_backend,  # 独立 backend
)

# 结果：
# ✅ 子代理只能访问 /research 目录
# ❌ 子代理无法访问父代理的 /workspace 目录
# ✅ 完全隔离的文件系统
```

---

## 四、实际集成示例

### 示例 1：将 Deep Agent 作为 LangGraph 的子节点

```python
from langgraph.graph import StateGraph, END
from deepagents import create_deep_agent
from typing import TypedDict

# 你的现有 LangGraph 项目
class MyState(TypedDict):
    user_input: str
    analysis_result: str
    final_output: str

def preprocess(state: MyState):
    """你的预处理节点"""
    return {"analysis_result": "预处理结果"}

def postprocess(state: MyState):
    """你的后处理节点"""
    return {"final_output": "最终输出"}

# 创建 Deep Agent 作为子节点
deep_agent_node = create_deep_agent(
    model="openai:gpt-4",
    skills=["./skills/data-analysis/"],
    memory=["./AGENTS.md"],
    backend=FilesystemBackend(root_dir="/workspace"),
)

# 组合到你的图中
graph = StateGraph(MyState)
graph.add_node("preprocess", preprocess)
graph.add_node("deep_agent", deep_agent_node)  # 添加 Deep Agent 子图
graph.add_node("postprocess", postprocess)

graph.add_edge("preprocess", "deep_agent")
graph.add_edge("deep_agent", "postprocess")
graph.add_edge("postprocess", END)

compiled = graph.compile()

# 执行
result = compiled.invoke({
    "user_input": "分析这份销售数据"
})

# Deep Agent 子图的中间件栈会在 "deep_agent" 节点内完整执行
# 但不会影响父图的其他节点
```

---

### 示例 2：自定义中间件并添加到 Deep Agent

```python
from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware

class MyLoggingMiddleware(AgentMiddleware):
    """自定义日志中间件 - 记录所有工具调用"""
    
    def wrap_tool_execution(
        self,
        tool_call,
        handler,
        config
    ):
        # 记录工具调用
        print(f"[LOG] 调用工具: {tool_call['name']}")
        print(f"[LOG] 参数: {tool_call['args']}")
        
        # 执行工具
        result = handler(tool_call, config)
        
        # 记录结果
        print(f"[LOG] 结果: {result[:100]}...")
        
        return result

class MyRateLimitMiddleware(AgentMiddleware):
    """自定义速率限制中间件"""
    
    def __init__(self, max_calls_per_minute=10):
        self.max_calls = max_calls_per_minute
        self.call_history = []
    
    def before_agent(self, state, runtime, config):
        # 检查调用频率
        current_time = time.time()
        recent_calls = [t for t in self.call_history if current_time - t < 60]
        
        if len(recent_calls) >= self.max_calls:
            raise RuntimeError("超过速率限制")
        
        self.call_history.append(current_time)
        return None

# 创建 Deep Agent 并添加自定义中间件
agent = create_deep_agent(
    model="openai:gpt-4",
    middleware=[
        MyLoggingMiddleware(),      # 日志中间件
        MyRateLimitMiddleware(10),  # 速率限制
    ],
    skills=["./skills/"],
    backend=FilesystemBackend(),
)

# 中间件执行顺序：
# TodoListMiddleware
# → SkillsMiddleware
# → FilesystemMiddleware
# → SubAgentMiddleware
# → SummarizationMiddleware
# → PatchToolCallsMiddleware
# → MyLoggingMiddleware        ← 你的自定义中间件
# → MyRateLimitMiddleware      ← 你的自定义中间件
# → MemoryMiddleware
# → PermissionMiddleware
```

---

### 示例 3：配置子代理的独立中间件

```python
from deepagents import create_deep_agent, SubAgent
from deepagents.middleware import SkillsMiddleware

# 定义一个子代理，有独立的技能和权限
researcher = SubAgent(
    name="researcher",
    description="专门做网络研究",
    system_prompt="你是研究助手，专注于收集信息",
    
    # 独立的技能配置
    skills=["./skills/web-search/", "./skills/arxiv-search/"],
    
    # 独立的权限规则（只允许读取）
    permissions=[
        FilesystemPermission(
            path="/research/**",
            allow_read=True,
            allow_write=False,
        )
    ],
    
    # 独立的记忆文件
    memory=["./researcher/AGENTS.md"],
    
    # 禁用中断（全自动执行）
    interrupt_on={},
)

# 创建主代理
main_agent = create_deep_agent(
    model="openai:gpt-4",
    subagents=[researcher],
    
    # 主代理有自己的技能和权限
    skills=["./skills/data-analysis/"],
    permissions=[
        FilesystemPermission(
            path="/workspace/**",
            allow_read=True,
            allow_write=True,
        )
    ],
)

# 当调用 researcher 子代理时：
# ✅ researcher 使用自己的 web-search 技能
# ✅ researcher 只能读取 /research 目录
# ✅ researcher 加载自己的 AGENTS.md
# ✅ researcher 不会暂停等待人类审批
# ⚠️ researcher 无法访问主代理的 data-analysis 技能
```

---

## 五、总结：中间件在子图中的关键特性

| 特性               | 主代理       | 子代理（子图） | 是否隔离     |
| ------------------ | ------------ | -------------- | ------------ |
| **TodoList**       | 独立任务列表 | 独立任务列表   | ✅ 完全隔离   |
| **Skills**         | 独立技能配置 | 独立技能配置   | ✅ 完全隔离   |
| **Memory**         | 独立记忆内容 | 独立记忆内容   | ✅ 完全隔离   |
| **Filesystem**     | 共享 backend | 共享 backend   | ⚠️ 可共享     |
| **SubAgent**       | 可调用子代理 | 可调用子代理   | ✅ 嵌套隔离   |
| **Summarization**  | 独立压缩     | 稳定压缩       | ✅ 完全隔离   |
| **HumanInTheLoop** | 父图暂停     | 子图暂停       | ⚠️ 只暂停子图 |
| **Permission**     | 父图权限规则 | 子图权限规则   | ⚠️ 替换不继承 |

**关键结论**：

1. ✅ **状态完全隔离** - 子代理的状态不会污染父代理
2. ✅ **中间件栈独立执行** - 子图有完整的中间件栈
3. ⚠️ **文件系统可共享** - 通过 backend 配置控制
4. ⚠️ **权限可定制** - 子代理可以更严格或更宽松
5. ✅ **嵌套支持** - 子代理可以再调用子代理

希望这个详细的讲解能帮你理解 Deep Agents 的中间件系统！如果你有具体的集成场景或问题，我可以帮你设计更详细的方案。