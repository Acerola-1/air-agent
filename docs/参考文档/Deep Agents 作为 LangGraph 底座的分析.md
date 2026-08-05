# 非常推荐！Deep Agents 作为 LangGraph 底座的分析

## 为什么推荐？

### 1. **Deep Agents 本身就是基于 LangGraph 构建的**

```python
harmess/deep_agents_source/libs/deepagents/deepagents/graph.py#L218-400
def create_deep_agent(...) -> CompiledStateGraph:
    """返回一个编译好的 LangGraph StateGraph"""
```

这意味着：
- ✅ **100% LangGraph 兼容** - 返回的就是 `CompiledStateGraph`
- ✅ **可以使用所有 LangGraph 特性** - streaming, checkpointing, persistence 等
- ✅ **无缝集成** - 可以直接嵌入现有的 LangGraph 项目

### 2. **开箱即用的企业级功能**

Deep Agents 提供了完整的"电池 included"功能栈：

**内置工具**：
```python
- write_todos        # 任务规划和跟踪
- ls, read_file, write_file, edit_file  # 文件操作
- glob, grep         # 文件搜索
- execute            # Shell 命令执行
- task               # 子代理调用
```

**中间件栈**（自动组装）：
```python
Base Stack:
├── TodoListMiddleware         # 任务管理
├── SkillsMiddleware          # 技能加载
├── FilesystemMiddleware      # 文件系统
├── SubAgentMiddleware        # 子代理管理
├── SummarizationMiddleware   # 上下文压缩
├── PatchToolCallsMiddleware  # 工具调用优化
└── AsyncSubAgentMiddleware   # 异步子代理

Tail Stack:
├── MemoryMiddleware          # 持久化记忆
├── HumanInTheLoopMiddleware  # 人机交互审批
└── _PermissionMiddleware     # 权限控制
```

### 3. **高度可定制**

你可以自定义**几乎所有组件**：

```python
harmess/deep_agents_source/libs/deepagents/deepagents/graph.py#L218-282
agent = create_deep_agent(
    model="openai:gpt-4",              # 自定义模型
    tools=[my_custom_tool],            # 自定义工具
    system_prompt="你的提示词",          # 自定义系统提示
    middleware=[MyMiddleware()],        # 自定义中间件
    subagents=[...],                   # 自定义子代理
    skills=["./skills/"],              # 自定义技能
    memory=["./AGENTS.md"],            # 自定义记忆
    permissions=[...],                 # 自定义权限规则
    backend=FilesystemBackend(),       # 自定义后端
    checkpointer=...,                  # 持久化
    interrupt_on={"execute": True},    # 人机交互
)
```

---

## 集成方案

### 方案 A：直接使用（最简单）

如果你要构建一个新的 agent 应用，直接使用 `create_deep_agent`：

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

# 创建一个功能完整的 agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    skills=["./skills/"],           # 加载技能
    memory=["./AGENTS.md"],         # 加载记忆
    backend=FilesystemBackend(),    # 文件系统后端
)

# 直接使用（完全兼容 LangGraph）
result = agent.invoke({
    "messages": [("user", "你的任务")]
})

# 或使用 streaming
async for chunk in agent.astream(...):
    print(chunk)
```

### 方案 B：扩展定制（推荐）

如果你有现有的 LangGraph 项目，可以这样集成：

```python
from deepagents import create_deep_agent, SubAgent
from deepagents.middleware import MemoryMiddleware
from langchain_core.tools import tool

# 1. 添加你的自定义工具
@tool
def my_custom_tool(query: str) -> str:
    """你的自定义工具"""
    return "结果"

# 2. 创建 Deep Agent 作为主代理
main_agent = create_deep_agent(
    model="openai:gpt-4",
    tools=[my_custom_tool],         # 添加自定义工具
    skills=["./skills/"],           # 使用 Deep Agents 的技能系统
    memory=["./AGENTS.md"],         # 使用记忆系统
    middleware=[MyCustomMiddleware()],  # 添加自定义中间件
)

# 3. 或者作为子代理集成进你的现有项目
from langgraph.graph import StateGraph

# 你的现有 LangGraph 项目
your_graph = StateGraph(...)

# 将 Deep Agent 作为子节点
your_graph.add_node("deep_agent", main_agent)
your_graph.add_edge("your_node", "deep_agent")
```

### 方案 C：混合架构（最灵活）

将 Deep Agents 的组件**按需集成**到你的项目中：

```python
from deepagents.middleware import (
    SkillsMiddleware,
    MemoryMiddleware,
    FilesystemMiddleware,
)
from deepagents.backends import FilesystemBackend

# 你的现有 LangGraph 项目
your_agent = create_agent(
    model=...,
    tools=[...],
)

# 只使用 Deep Agents 的 Skills 系统
skills_middleware = SkillsMiddleware(
    backend=FilesystemBackend(),
    sources=["./skills/"],
)

# 添加到你的 agent
your_agent.add_middleware(skills_middleware)
```

---

## 实际示例：Content Writer Agent

这是一个完整的示例，展示了如何用 Deep Agents 构建一个复杂的 agent：

```python
harmess/deep_agents_source/examples/content-builder-agent/content_writer.py#L168-174
def create_content_writer():
    return create_deep_agent(
        memory=["./AGENTS.md"],           # 加载品牌风格指南
        skills=["./skills/"],             # 加载 blog-post, social-media 技能
        tools=[generate_cover, generate_social_image],  # 自定义图片生成工具
        subagents=load_subagents(...),    # 加载研究子代理
        backend=FilesystemBackend(root_dir=EXAMPLE_DIR),
    )

# 使用
agent = create_content_writer()
result = await agent.astream({
    "messages": [("user", "写一篇关于 AI agents 的博客文章")]
})
```

---

## 核心优势对比

| 特性            | 直接用 LangGraph     | 用 Deep Agents 作为底座    |
| --------------- | -------------------- | -------------------------- |
| **开发速度**    | 需要自己组装所有组件 | ⭐⭐⭐⭐⭐ 开箱即用             |
| **Skills 系统** | ❌ 需自己实现         | ✅ 标准化 Skills 系统       |
| **Memory 系统** | ❌ 需自己实现         | ✅ AGENTS.md 标准           |
| **子代理**      | ⚠️ 需手动管理         | ✅ 自动化管理 + task 工具   |
| **文件操作**    | ❌ 需自己写工具       | ✅ 内置完整工具集           |
| **上下文管理**  | ❌ 需自己实现         | ✅ 自动 summarization       |
| **权限控制**    | ❌ 需自己实现         | ✅ 内置权限中间件           |
| **人机交互**    | ⚠️ 需手动实现         | ✅ interrupt_on 配置        |
| **多后端**      | ❌ 需自己实现         | ✅ Filesystem/State/Sandbox |
| **可扩展性**    | ⭐⭐⭐⭐⭐ 完全自由       | ⭐⭐⭐⭐⭐ 完全可定制           |
| **标准化**      | ❌ 无标准             | ✅ Agent Skills 标准        |

---

## 最佳实践建议

### 1. **作为新项目的底座**
如果你要构建一个新的 agent 应用：
```python
✅ 直接使用 create_deep_agent
✅ 添加你的自定义工具和 skills
✅ 使用标准的 AGENTS.md 配置记忆
✅ 利用内置的 checkpointer 和 store
```

### 2. **集成到现有项目**
如果你已有 LangGraph 项目：
```python
✅ 将 Deep Agent 作为子节点或子代理
✅ 只使用你需要的中间件
✅ 利用 Deep Agents 的 skills 和 memory 系统
✅ 保持你的现有架构不变
```

### 3. **渐进式迁移**
```python
步骤 1: 先用 Deep Agents 的 SkillsMiddleware
步骤 2: 添加 MemoryMiddleware
步骤 3: 使用 FilesystemMiddleware
步骤 4: 最终迁移到完整的 create_deep_agent
```

---

## 具体集成代码示例

### 示例 1：最简单的集成

```python
from deepagents import create_deep_agent

# 创建一个功能完整的 agent（一行代码）
agent = create_deep_agent(
    model="openai:gpt-4",
    skills=["./skills/"],
)

# 使用（完全兼容 LangGraph）
async for event in agent.astream(
    {"messages": [("user", "帮我分析这个数据")]},
    config={"configurable": {"thread_id": "session-1"}},
    stream_mode="values",
):
    print(event)
```

### 示例 2：添加自定义工具

```python
from deepagents import create_deep_agent
from langchain_core.tools import tool

@tool
def query_database(sql: str) -> str:
    """查询数据库"""
    # 你的数据库查询逻辑
    return "查询结果"

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    # 你的邮件发送逻辑
    return "已发送"

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[query_database, send_email],  # 添加自定义工具
    skills=["./skills/data-analysis/", "./skills/email-writing/"],
    backend=FilesystemBackend(),
)
```

### 示例 3：集成子代理

```python
from deepagents import create_deep_agent, SubAgent

# 定义你的子代理
researcher = SubAgent(
    name="researcher",
    description="专门做研究的子代理",
    system_prompt="你是一个研究助手...",
    tools=[web_search],  # 子代理可以有自己的工具
    model="openai:gpt-3.5-turbo",  # 子代理可以用不同的模型
)

coder = SubAgent(
    name="coder",
    description="专门写代码的子代理",
    system_prompt="你是一个编程助手...",
)

agent = create_deep_agent(
    model="openai:gpt-4",
    subagents=[researcher, coder],  # 添加子代理
)
```

### 示例 4：添加自定义中间件

```python
from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware

class MyLoggingMiddleware(AgentMiddleware):
    """自定义日志中间件"""
    
    def process_request(self, request, config):
        print(f"收到请求: {request}")
        return request
    
    def process_response(self, response, config):
        print(f"返回响应: {response}")
        return response

agent = create_deep_agent(
    model="openai:gpt-4",
    middleware=[MyLoggingMiddleware()],  # 添加自定义中间件
)
```

### 示例 5：集成到现有 LangGraph 项目

```python
from langgraph.graph import StateGraph, END
from deepagents import create_deep_agent

# 你的现有 LangGraph 项目
class MyState(TypedDict):
    user_input: str
    processed_data: str
    final_output: str

# 你的现有节点
def process_input(state: MyState):
    # 你的处理逻辑
    return {"processed_data": "处理后的数据"}

# 创建 Deep Agent 作为子节点
deep_agent = create_deep_agent(
    model="openai:gpt-4",
    skills=["./skills/"],
)

# 组合到你的图中
graph = StateGraph(MyState)
graph.add_node("process", process_input)
graph.add_node("deep_agent", deep_agent)  # 添加 Deep Agent
graph.add_node("output", generate_output)

graph.add_edge("process", "deep_agent")
graph.add_edge("deep_agent", "output")
graph.add_edge("output", END)

compiled = graph.compile()
```

---

## 总结

### ✅ 强烈推荐的原因：

1. **100% LangGraph 兼容** - 无缝集成
2. **企业级功能完备** - Skills, Memory, Middleware, Tools 全都有
3. **高度可定制** - 可以自定义几乎所有组件
4. **标准化** - 基于 Agent Skills 开放标准
5. **节省大量开发时间** - 不需要自己实现基础功能
6. **活跃维护** - LangChain 官方项目，持续更新

### ⚠️ 需要注意的点：

1. **依赖 LangChain 最新版本** - 需要确保版本兼容
2. **需要学习中间件概念** - 但文档很清晰
3. **可能需要适配现有代码** - 但迁移成本很低

### 🎯 我的建议：

**直接使用 Deep Agents 作为底座！** 它提供了：
- 最佳的标准化实践
- 完整的功能栈
- 极高的可扩展性
- 与 LangGraph 的完美兼容

你可以从最简单的集成开始，然后逐步添加更多功能。这样既能快速启动，又能保持完全的控制权。

如果你有具体的集成场景或问题，我可以帮你设计更详细的方案！

## User

你跟我讲一下 deepagnts 的中间件都能做什么,尤其是我把他塞进 langgraph 的子图后


太好了！现在我有了非常完整的信息。让我整理一下 Deep Agents 的中间件系统，特别是当作为子图时的行为。

从代码中我可以看到：

1. **中间件的执行顺序**：
   - 主代理的中间件栈：
     ```
     TodoListMiddleware
     ↓
     SkillsMiddleware (如果配置了 skills)
     ↓
     FilesystemMiddleware
     ↓
     SubAgentMiddleware
     ↓
     SummarizationMiddleware
     ↓
     PatchToolCallsMiddleware
     ↓
     AsyncSubAgentMiddleware (如果有异步子代理)
     ↓
     [用户自定义中间件]
     ↓
     [Provider-specific middleware]
     ↓
     _ToolExclusionMiddleware (如果有排除的工具)
     ↓
     AnthropicPromptCachingMiddleware
     ↓
     MemoryMiddleware (如果配置了 memory)
     ↓
     HumanInTheLoopMiddleware (如果配置了 interrupt_on)
     ↓
     _PermissionMiddleware (如果配置了 permissions，必须在最后)
     ```

2. **每个中间件的作用**：
   - TodoListMiddleware: 任务规划和管理
   - SkillsMiddleware: 加载和暴露技能到系统提示词
   - FilesystemMiddleware: 提供文件操作工具（ls, read_file, write_file, edit_file, glob, grep）
   - SubAgentMiddleware: 提供子代理调用工具（task）
   - SummarizationMiddleware: 自动压缩长对话上下文
   - PatchToolCallsMiddleware: 优化工具调用
   - MemoryMiddleware: 持久化记忆和上下文
   - HumanInTheLoopMiddleware: 人机交互审批机制
   - PermissionMiddleware: 精细化权限控制

3. **作为子图时的行为**：
   - 子代理继承了父代理的核心中间件能力
   - 可灵活配置技能、权限和交互模式
   - 记忆系统确保上下文的连贯性和信息传递

记忆状态私密且独立，每个子代理拥有专属的技能配置和权限管理机制。通过精心设计的隔离与继承策略，实现了代理间的协同与自主性平衡。
</thinking>

