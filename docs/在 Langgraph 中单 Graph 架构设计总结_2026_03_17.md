# 结构重构\_2026_03_17：单 Graph 架构设计总结

本文记录系统的单 Graph 架构设计，包括 Graph 入口、目录组织、执行链路、运行模式、意图路由、工具体系和 Skill 设计。

架构基准：

| 项目         | 内容                         |
| ------------ | ---------------------------- |
| 分支         | `prod`                       |
| Graph 配置   | `langgraph.json`             |
| 对外 Graph   | `agent`                      |
| Graph 入口   | `./src/agent/graph.py:graph` |
| 主要代码目录 | `src/agent/`                 |

本架构的核心特征是：**一个统一的 LangGraph `agent` 承担所有业务入口、权限校验、模式路由、意图识别、工具执行、知识检索、复杂规划和最终输出**。

---

### 一、整体架构：单一 `agent` Graph 承载全部业务

|                 | 单 Graph 架构                                                           |
| --------------- | ----------------------------------------------------------------------- |
| **业务入口**    | 统一进入 `agent` graph                                                  |
| **模块划分**    | 主要通过意图识别、工具类型、chat mode 和节点路由完成                    |
| **Graph 数量**  | 1 个：`agent`                                                           |
| **代码组织**    | 业务节点、状态、工具、配置、DeepAgent 集成都集中在 `src/agent/`         |
| **Skills 位置** | `src/agent/skills/`                                                     |
| **Expert 能力** | 通过 `deepagent_integration.py` 将 DeepAgent 作为 Expert 子图挂入主流程 |
| **Checkpoint**  | Graph 编译时接入 PostgreSQL checkpointer                                |

`langgraph.json` 只暴露一个 graph：

```json
{
    "dependencies": ["."],
    "graphs": {
        "agent": "./src/agent/graph.py:graph"
    },
    "env": ".env"
}
```

这意味着调用方不区分业务 graph，所有请求都先进入 `agent`，再由主图内部判断执行路径。

---

### 二、目录结构：`src/agent` 集中式组织

| 路径                                 | 职责                                                                               |
| ------------------------------------ | ---------------------------------------------------------------------------------- |
| `src/agent/graph.py`                 | 定义主 `StateGraph`，注册节点、边和条件路由，导出 `graph`                          |
| `src/agent/nodes.py`                 | 核心节点实现，包含上下文构建、权限、意图识别、知识检索、工具执行、规划、生成等逻辑 |
| `src/agent/state.py`                 | 定义 `AgentState`、Planner、Replanner 等状态和结构化模型                           |
| `src/agent/config.py`                | 环境变量、数据库、Redis、模型等基础配置                                            |
| `src/agent/configuration.py`         | 运行时配置，核心字段包括 `chat_mode`                                               |
| `src/agent/intent_config.py`         | 语义路由配置，定义不同业务意图和匹配样例                                           |
| `src/agent/tools.py`                 | MCP、Text2SQL、Knowledge、WebSearch 等统一工具封装                                 |
| `src/agent/prompts.py`               | 主图使用的提示词模板                                                               |
| `src/agent/deepagent_integration.py` | Expert 模式 DeepAgent 子图包装器                                                   |
| `src/agent/skills/`                  | Expert 模式使用的技能目录和全局 `AGENTS.md`                                        |

该结构的特点是集中：读入口简单，但业务量增长后，`nodes.py` 和 `src/agent` 会逐渐承载过多职责。

---

### 三、主 Graph 流程：一个 StateGraph 内部完成多阶段编排

`graph.py` 使用 `StateGraph(AgentState)` 构建完整工作流，主要链路如下：

```text
START
  ↓
build_contextual_question
  ↓
fetch_time_context
  ↓
parse_region_expansion
  ↓
permission_agent
  ↓
permission_retrieve / permission_eval / permission_interrupt
  ↓
chat_mode_init
  ├─ expert → expert_subagent → expert_stream_handler → END
  └─ fast   → unified_intent_match
                 ├─ knowledge_agent → knowledge_retrieve → grade_documents / rewrite → generate
                 ├─ parallel_tool_executor → generate
                 ├─ planner → executor → replanner → generate
                 └─ generate
  ↓
expand_question
  ↓
END
```

**核心节点分组**：

| 节点组       | 节点                                                                                 | 职责                                                     |
| ------------ | ------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| 上下文预处理 | `build_contextual_question`、`fetch_time_context`、`parse_region_expansion`          | 结合历史会话改写问题，补充时间上下文和区域扩展信息       |
| 权限校验     | `permission_agent`、`permission_retrieve`、`permission_eval`、`permission_interrupt` | 判断是否需要权限工具、是否可继续执行、是否中断要求登录   |
| 模式初始化   | `chat_mode_init`                                                                     | 读取运行配置，决定 fast 或 expert 路径                   |
| 意图识别     | `unified_intent_match`                                                               | 基于语义路由识别 TEXT2SQL、MCP、KNOWLEDGE、DIRECT 等类型 |
| 知识检索     | `knowledge_agent`、`knowledge_retrieve`、`grade_documents`、`rewrite`                | 执行 RAG 检索、文档评分和问题改写                        |
| 工具执行     | `parallel_tool_executor`                                                             | 统一执行 TEXT2SQL 和 MCP 类工具                          |
| 复杂规划     | `planner`、`executor`、`replanner`                                                   | 对复杂任务进行计划、执行和重规划                         |
| Expert 子图  | `expert_subagent`、`expert_stream_handler`                                           | 调用 DeepAgent 子图并处理专家模式输出                    |
| 最终生成     | `generate`、`expand_question`                                                        | 生成用户可见答案，并输出拓展问题                         |

---

### 四、运行模式：fast 与 expert 双路径

系统通过 `Configuration.chat_mode` 控制执行模式：

| 模式     | 路径                                                            | 主要特点                                                        |
| -------- | --------------------------------------------------------------- | --------------------------------------------------------------- |
| `fast`   | `chat_mode_init` → `unified_intent_match` → 工具/知识/规划/生成 | 主图内部完成意图识别和执行，强调确定性路由和较短链路            |
| `expert` | `chat_mode_init` → `expert_subagent` → `expert_stream_handler`  | 将请求交给 DeepAgent 子图，由 Skills 和工具完成更复杂的专家分析 |

这个设计解决了“快速问答”和“深度分析”两类不同复杂度的问题，两条路径统一挂在同一个 `agent` graph 下。

---

### 五、意图路由：在主图内部识别业务类型

业务分流主要依赖 `src/agent/intent_config.py` 中的语义路由配置。

| Intent 类型 | 说明                                | 执行路径                 |
| ----------- | ----------------------------------- | ------------------------ |
| `TEXT2SQL`  | 需要结构化数据查询或 SQL 生成的问题 | `parallel_tool_executor` |
| `MCP`       | 需要调用 MCP 工具的问题             | `parallel_tool_executor` |
| `KNOWLEDGE` | 需要知识库检索的问题                | `knowledge_agent`        |
| `DIRECT`    | 可直接回答或无需外部工具的问题      | `generate`               |

业务模块没有作为独立 graph 暴露，而是通过大量 route examples 和模板配置在主图内部识别，例如空气质量实时查询、排名、趋势、同比环比、站点极值、播报小时、综合指数占比等。

**优点**：

- 调用方只需要知道一个 graph。
- 路由逻辑集中，便于统一调试。
- 新增意图时可以在一个配置文件内扩展样例和模板。

**代价**：

- 页面已经知道业务模块时，仍然需要主图再做一次意图判断。
- 所有业务 route 都耦合在同一套路由配置中。
- 意图样例、工具选择、输出模板容易互相影响。

---

### 六、工具体系：统一封装，多能力集中绑定

`tools.py` 将多类能力封装为统一工具层：

| 工具类          | 职责                                               |
| --------------- | -------------------------------------------------- |
| `MCPTool`       | 封装 MCP 工具调用                                  |
| `Text2SQLTool`  | 封装 Vanna/Text2SQL 查询能力                       |
| `KnowledgeTool` | 封装知识库检索能力                                 |
| `WebSearchTool` | 封装联网检索能力                                   |
| `UnifiedTools`  | 汇总工具列表，为 planner 和执行节点提供统一 schema |

此外，`nodes.py` 在启动和运行过程中还会涉及 Redis、Milvus、多查询检索器、MCP client、Vanna 适配、WebSearch、PostgreSQL checkpointer 等组件。

工具层的特点是：**能力集中、入口统一、主图负责判断何时调用何种工具**。

---

### 七、DeepAgent 集成：Expert 模式作为主图子能力

`src/agent/deepagent_integration.py` 负责把 DeepAgent 挂入主图：

| 设计点     | 说明                                    |
| ---------- | --------------------------------------- |
| 包装器     | `DeepAgentWrapper`                      |
| 创建方式   | `create_deep_agent(...)`                |
| 初始化方式 | 延迟初始化，首次 expert 请求时创建      |
| Skills     | 默认加载 `src/agent/skills/`            |
| Memory     | 默认加载 `src/agent/skills/AGENTS.md`   |
| Backend    | `FilesystemBackend(root_dir=src/agent)` |
| 执行方式   | `ainvoke`                               |
| 超时       | 300 秒                                  |

Expert 模式的执行链路：

```text
用户请求
  ↓
agent graph
  ↓
chat_mode_init 判断为 expert
  ↓
expert_subagent 调用 DeepAgentWrapper
  ↓
DeepAgent 根据 skills / memory / tools 执行分析
  ↓
expert_stream_handler 提取最终文本和图表数据
  ↓
END
```

这个阶段的 DeepAgent 更像是主图中的一个“专家分析子能力”，而不是独立业务入口。

---

### 八、Skill 设计：统一 Skill 根目录

Skills 统一位于 `src/agent/skills/` 下。

| 文件/目录                                          | 职责                          |
| -------------------------------------------------- | ----------------------------- |
| `src/agent/skills/AGENTS.md`                       | Expert 模式全局约束和输出规范 |
| `src/agent/skills/air-quality-analysis/SKILL.md`   | 空气质量综合分析技能          |
| `src/agent/skills/broadcast-hour/SKILL.md`         | 小时播报分析技能              |
| `src/agent/skills/integrated-index-ratio/SKILL.md` | 综合指数占比分析技能          |

Skill 的使用方式主要服务于 Expert DeepAgent：

|              | 设计说明                                         |
| ------------ | ------------------------------------------------ |
| **归属边界** | 统一挂在 `src/agent/skills/`                     |
| **加载对象** | Expert DeepAgent 子图                            |
| **业务隔离** | 依赖 Skill 文档和工具约束，而不是物理 graph 边界 |
| **输出控制** | 由 `AGENTS.md` 和各 `SKILL.md` 共同约束          |

Skill 目录已经具备专业技能沉淀的雏形，业务隔离主要依赖 Skill 文档、工具约束和主图路由。

---

### 九、架构优势

- **入口简单**：对外只暴露 `agent`，调用方接入成本低。
- **集中控制**：权限、上下文、意图、工具、知识库、规划和生成都在一个图内编排。
- **能力完整**：同时支持 RAG、Text2SQL、MCP、WebSearch、Planner/Replanner 和 Expert DeepAgent。
- **状态统一**：`AgentState` 承载完整执行状态，便于主图内节点传递。
- **模式兼容**：通过 `chat_mode` 同时支持 fast 和 expert 两种复杂度。
- **持久化明确**：Graph 编译时统一接入 PostgreSQL checkpointer。

---

### 十、结构约束与风险

| 问题                | 表现                                       | 影响                                          |
| ------------------- | ------------------------------------------ | --------------------------------------------- |
| 主图职责过重        | 一个 `agent` 处理所有业务模块和执行策略    | 代码理解、变更评估和问题定位成本升高          |
| `nodes.py` 过大     | 大量初始化、节点逻辑、路由逻辑集中在单文件 | 模块边界不清晰，单点复杂度持续增加            |
| 业务边界不物理隔离  | 业务模块依赖意图配置区分，而不是独立 graph | 不同业务的 prompt、tools、skills 容易相互干扰 |
| 页面路由重复        | 前端已有业务上下文，但后端仍由主图识别意图 | 增加一次模型判断或路由开销                    |
| Expert 不是一等入口 | DeepAgent 被作为主图的 expert 子能力       | 输出仍需回到主图处理，独立维护成本较高        |
| Skill 粒度集中      | 所有技能挂在 `src/agent/skills/`           | 随业务增长后，Skill 归属和加载范围会变得模糊  |
| 输出链路偏长        | 子流程结果需要经过主图生成或处理           | 首 token 和最终响应速度受主图链路影响         |

---

### 十一、架构边界

该架构可以概括为：

```text
1 个 agent graph
  ├─ 主图内部上下文处理
  ├─ 主图内部权限校验
  ├─ 主图内部意图识别
  ├─ 主图内部工具执行
  ├─ 主图内部知识检索
  ├─ 主图内部 Planner/Replanner
  └─ Expert 模式 DeepAgent 子图
```

这个设计的边界是清晰的：`agent` 是唯一对外入口，所有业务能力都在主图中完成调度和收口；DeepAgent、Planner、RAG、Text2SQL、MCP 等能力都是主图内部的执行策略。

因此，系统的扩展重点主要集中在三个位置：

| 扩展位置    | 扩展方式                                                    |
| ----------- | ----------------------------------------------------------- |
| 意图配置    | 在 `intent_config.py` 中新增 route examples、工具类型和模板 |
| 工具能力    | 在 `tools.py` 或 MCP/Text2SQL 适配层中扩展新工具            |
| Expert 技能 | 在 `src/agent/skills/` 下新增或调整 `SKILL.md`              |

---

### 十二、结论

本架构是一个典型的集中式 LangGraph Agent：

```text
单 Graph 入口 + 主图集中编排 + fast/expert 双模式 + DeepAgent 子图增强
```

它适合早期快速集成多种能力，也能在一个入口内统一处理权限、检索、工具调用和最终生成。

随着业务模块增多，系统复杂度主要会集中在主图内部：页面模块、业务技能、工具范围、输出格式和执行策略都需要在同一个 `agent` 体系内协同维护。

整体来看，该设计以 `agent` 作为统一入口，通过主图内部节点和条件路由完成多类业务能力的编排与收口。
