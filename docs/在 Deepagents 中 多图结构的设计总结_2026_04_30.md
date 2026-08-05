# 多图的 Deep Agents 架构设计总结

### 一、顶层 Graph 设计：按业务入口划分

| | 修改前：Main Agent + SubAgent | 修改后：多顶层 Graph |
| **业务入口** | 调用方进入统一 `agent`，再由 Main Agent 判断调用哪个 SubAgent | 调用方直接指定目标 graph，LangGraph 直接进入业务入口 |
| **Skills 绑定** | SubAgent 绑定各自 skills，但结果仍需回到 Main Agent | 每个顶层 graph 直接绑定自己的 `skills/` 目录 |
| **Tools 绑定** | SubAgent 绑定工具，Main Agent 仍承担调度与结果搬运 | 每个业务 graph 直接绑定所需工具，不再通过父 Agent 调度 |
| **职责边界** | 业务模块被建模为父 Agent 的工具能力 | 业务模块成为独立 graph，职责边界与页面入口一致 |
| **最终输出** | SubAgent 输出被包装为 ToolMessage，再由 Main Agent 解包/转述 | 业务 graph 直接生成最终回答并流式输出 |

**当前 7 个 Graph**：

| Graph 名称             | 代码目录                       | 负责模块               | Skills 目录                        | 绑定工具范围                                               |
| ---------------------- | ------------------------------ | ---------------------- | ---------------------------------- | ---------------------------------------------------------- |
| `basic-qa`             | `src/basic_qa/`                | 基础问答               | `src/basic_qa/skills/`             | 查询、排名、达标、对比、站点、区域、趋势等基础空气质量工具 |
| `intelligent-analysis` | `src/intelligent_analysis/`    | 智能分析               | `src/intelligent_analysis/skills/` | 基础空气质量工具 + 分析类能力                              |
| `data-analysis`        | `src/data_analysis/`           | 业务数据分析 DeepAgent | `src/data_analysis/skills/`        | 基础空气质量工具 + 数据分析能力                            |
| `intelligent-report`   | `src/intelligent_report/`      | 智能报告               | `src/intelligent_report/skills/`   | 当前预留，按报告场景独立扩展                               |
| `deep-research`        | `src/deep_research/`           | 深度研究               | `src/deep_research/skills/`        | 当前预留，按研究场景独立扩展                               |
| `intelligent-tracing`  | `src/intelligent_tracing/`     | 智能溯源               | `src/intelligent_tracing/skills/`  | 当前预留，按溯源场景独立扩展                               |
| `data_analysis`        | `src/data_analysis_assistant/` | 原有数据分析辅助图     | 独立提示词体系                     | 保留既有流程                                               |

**核心改进**：

- **业务直达**：页面已经知道业务模块，调用方直接选择 graph，避免 Main Agent 再做一次 LLM 路由判断。
- **输出直达**：业务 graph 直接面向用户输出，不再经过 SubAgent → ToolMessage → Main Agent 的二次搬运。
- **流式更早**：最终 token 由业务 graph 直接产生，前端无需等待父 Agent 解包工具结果后再开始输出。
- **认知聚焦**：每个 graph 只加载自己的 system prompt、skills 和工具范围，模型决策更稳定。
- **代码边界清晰**：`src` 下按 graph 平铺，`src/common/` 只放共享能力，读代码时能直接定位业务入口。
- **独立演进**：各 graph 可独立调整 prompt、skills、tools、middleware、model 和输出格式。

---

### 二、目录设计：7 个 Graph 目录 + Common 共享层

| 目录                           | 职责                                               |
| ------------------------------ | -------------------------------------------------- |
| `src/basic_qa/`                | `basic-qa` graph，基础问答业务入口                 |
| `src/intelligent_analysis/`    | `intelligent-analysis` graph，智能分析业务入口     |
| `src/data_analysis/`           | `data-analysis` graph，业务数据分析 DeepAgent 入口 |
| `src/intelligent_report/`      | `intelligent-report` graph，报告生成业务入口       |
| `src/deep_research/`           | `deep-research` graph，深度研究业务入口            |
| `src/intelligent_tracing/`     | `intelligent-tracing` graph，污染溯源业务入口      |
| `src/data_analysis_assistant/` | 保留原有 `data_analysis` graph                     |
| `src/common/`                  | 共享配置、模型、工具、中间件、MCP 和 checkpoint    |

**每个业务 Graph 的标准结构**：

| 文件/目录  | 职责                                                                                       |
| ---------- | ------------------------------------------------------------------------------------------ |
| `graph.py` | LangGraph 入口，直接声明当前业务 graph 的 prompt、skills、tools、middleware 并导出 `graph` |
| `skills/`  | 当前业务 graph 独有技能目录                                                                |

**Common 层职责**：

| 模块                   | 职责                                                          |
| ---------------------- | ------------------------------------------------------------- |
| `common/config/`       | 环境配置、PG checkpointer、Vanna 适配等共享配置               |
| `common/middleware/`   | 权限、时间上下文、模式上下文、富输出、旧图表兼容等 middleware |
| `common/tools.py`      | 共享工具定义                                                  |
| `common/mcp_client.py` | MCP 工具初始化与缓存                                          |
| `common/models.py`     | 模型注册表                                                    |

---

### 三、Skill 设计：Graph 内聚 + 三层文件体系

|              | 修改前                                                  | 修改后                                    |
| ------------ | ------------------------------------------------------- | ----------------------------------------- |
| **归属边界** | 统一放在 `src/agent/skills/` 下，再由 SubAgent 配置引用 | 每个 graph 拥有自己的 `skills/` 目录      |
| **加载范围** | 业务技能虽按目录分组，但架构上仍挂在同一个父 Agent 下   | 顶层 graph 只加载本目录下的技能           |
| **模式处理** | 通过 runtime context 和 Skill 内部指引区分 fast/expert  | 保留 fast/expert，在 graph 内部按需生效   |
| **演进方式** | 修改业务技能时容易和父 Agent/SubAgent 配置耦合          | 修改某个 graph 的 skills 不影响其他 graph |

**三层文件的职责**：

| 文件                     | 职责                                           | 何时加载                             | 谁加载           |
| ------------------------ | ---------------------------------------------- | ------------------------------------ | ---------------- |
| **SKILL.md**             | 总纲：元数据、适用场景、通用流程、模式路由指引 | Agent 启动或 SkillsMiddleware 扫描时 | SkillsMiddleware |
| **references/fast.md**   | 快速模式：精简流程、关键工具调用、标准化输出   | 业务执行时按需读取                   | 当前业务 graph   |
| **references/expert.md** | 专家模式：深度分析流程、补充校验、专业输出     | 业务执行时按需读取                   | 当前业务 graph   |

**核心改进**：

- **技能就近维护**：例如基础问答技能只在 `src/basic_qa/skills/` 下维护。
- **上下文更小**：每个 graph 的 SkillsMiddleware 只披露本业务域的技能元数据。
- **模式不扩散**：fast/expert 是 graph 内部执行策略，不再影响 graph 选择。
- **业务隔离更强**：报告、研究、溯源等模块可以独立扩展自己的技能体系。

---

### 四、执行链路变化

**修改前链路**：

```text
用户请求
  ↓
agent graph / Main Agent
  ↓
读取 module / subagent_type / mode
  ↓
Main Agent LLM 判断是否调用 task
  ↓
task(subagent_type=xxx)
  ↓
业务 SubAgent
  ↓
SubAgent 选择 skill / tool 并生成 rendered_text
  ↓
ToolMessage 返回 Main Agent
  ↓
Main Agent 解包 / 搬运 / 可能二次整理
  ↓
最终输出给用户
```

**修改后链路**：

```text
用户请求
  ↓
调用方直接选择 graph，例如 basic-qa
  ↓
basic_qa graph / 独立 DeepAgent
  ↓
加载本 graph 的 prompt / skills / tools / middleware
  ↓
直接生成最终回答并流式输出
```

**关键区别**：

| 环节   | 修改前                                           | 修改后                      |
| ------ | ------------------------------------------------ | --------------------------- |
| 路由   | Main Agent 通过 LLM 判断                         | 调用方确定性选择 graph      |
| 委派   | 通过 `task` 工具调用 SubAgent                    | 无父子委派                  |
| 输出   | SubAgent 结果作为 ToolMessage 返回父 Agent       | 业务 graph 直接输出         |
| 流式   | 父 Agent 最终输出才是用户可见内容                | 业务 graph token 直接可见   |
| 复杂度 | 父 Agent prompt + SubAgent prompt + Skill prompt | Graph prompt + Skill prompt |

---

### 五、性能提升原因分析

这次速度提升 3 倍以上，主要来自架构链路缩短，而不是单个函数优化。

**1. 去掉 Main Agent 的一次 LLM 路由**

修改前，即使页面已经知道用户在哪个模块，Main Agent 仍然需要通过 prompt 判断是否调用 `task`、调用哪个 SubAgent。修改后 graph 名称就是业务路由结果，直接进入目标 graph。

**2. 去掉 `task` 工具调用开销**

DeepAgents 的 SubAgent 通过 `task` 工具执行，适合临时委派复杂子任务。但这里 6 个模块是固定业务入口，不是临时子任务。去掉 `task` 后，减少了工具调用生成、调度、SubAgent 上下文初始化和结果回传成本。

**3. 去掉 ToolMessage 包装/解包**

修改前 SubAgent 输出要包装成结构化结果，例如 `rendered_text`，再由 Main Agent 读取并转述。修改后业务 graph 的输出就是用户最终答案，不再经历工具消息转换。

**4. Prompt 负担明显降低**

修改前模型需要同时遵守 Main Agent 路由规则、task 调用规则、SubAgent 结构化返回规则和 Skill 输出规范。修改后只需要关注当前业务 graph 的职责和当前 Skill 的流程。

**5. 流式输出更早开始**

修改前用户可见 token 往往要等 SubAgent 完成并返回父 Agent 后才开始。修改后业务 graph 直接产生最终 token，LangGraph 的 messages 流可以更早推送。

**6. LangGraph 多 Graph 特性更匹配业务入口**

LangGraph 的 graph 本身就是稳定 API 入口。页面已经按业务模块划分时，用多个 graph 承载多个业务入口，比让一个 LLM 父 Agent 再做模块路由更直接、更稳定。

---

### 六、DeepAgents 与 LangGraph 的职责重新划分

| 能力                 | 适合承担的职责                                          | 本方案中的使用方式                               |
| -------------------- | ------------------------------------------------------- | ------------------------------------------------ |
| **LangGraph Graph**  | 稳定业务入口、独立执行单元、部署和调用边界              | 6 个业务模块都作为顶层 graph 暴露                |
| **DeepAgent**        | 单个业务入口内部的规划、工具调用、skills 使用、最终输出 | 每个业务 graph 都是独立 DeepAgent                |
| **SubAgent**         | graph 内部临时委派、上下文隔离、并行处理复杂子任务      | 不再作为页面级业务模块边界，仅保留为未来可选机制 |
| **SkillsMiddleware** | 渐进式披露当前 graph 的技能元数据                       | 每个 graph 只扫描自己的 `skills/`                |
| **Middleware**       | 权限、时间、模式、富输出等横切逻辑                      | 由每个业务 `graph.py` 按自身需求显式装配         |

**新的设计原则**：

- 页面级业务模块 = 顶层 LangGraph graph。
- 单个 graph 内部的复杂委派 = 可选 SubAgent。
- 共享能力放 `common`，业务能力放各自 graph 目录。
- graph 名称是业务路由，`module` 不再承担 6 个业务 graph 的选择职责。

---

### 七、结论

本次改造的本质是把边界从“框架层父子 Agent”调整为“产品层业务 Graph”：

```text
修改前：1 个 Main Agent + 6 个业务 SubAgent
修改后：6 个业务顶层 DeepAgent graph + 1 个保留辅助 graph + common 共享层
```

因此带来的收益是系统性的：

- 执行链路更短
- LLM 调用更少
- ToolMessage 包装更少
- Prompt 更聚焦
- 首 token 更快
- 模块边界更清晰
- 业务维护成本更低

这也是测试中运行速度明显提升的主要原因。
