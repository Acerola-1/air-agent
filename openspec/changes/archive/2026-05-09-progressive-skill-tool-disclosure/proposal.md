## 原因

业务 Graph 目前同时向模型暴露图本地的 Skill 元数据和广泛的 MCP 工具集。随着 Skill 和 Tool 数量的增长，DeepAgent 越来越依赖脆弱的自主发现：它可能跳过匹配的 Skill、读取错误的 Skill，或者尽管 `SKILL.md` 中记录了 `allowed-tools`，仍然调用不相关的 Tool。

我们需要一种运行时机制，将 Skill 发现和 Tool 可用性转变为显式的、可观察的、状态驱动的行为。

## 变更内容

- 添加图本地的 `find_skill` 入口工具，将用户请求语义匹配到 Skill 元数据并返回 `allowed_tools`。
- 将选定的 Skill 结果存储在图状态中，以便后续模型调用知道哪个 Skill 处于活跃状态。
- 通过中间件注册所有图业务工具，同时最初仅向模型暴露入口工具加内置/系统工具。
- 添加中间件，根据所选 Skill 的 `allowed-tools` 动态过滤模型可见的工具。
- 在 `basic-qa` 中试点新的渐进式披露流程，然后再将相同模式应用于其他业务 Graph。
- 保持 DeepAgents 内置工具可用；`allowed-tools` 约束业务工具，而非框架必需的工具。

## 能力

### 新增能力

- `progressive-skill-tool-disclosure`：由 `find_skill`、Skill 状态和 `allowed-tools` 驱动的运行时 Skill 发现和动态 Tool 披露。

### 修改能力

- `skill-three-layer-system`：`allowed-tools` 从展示给模型的元数据变更为运行时强制执行的业务 Tool 可见性契约。
- `business-graph-architecture`：业务 Graph 可以在 `tools=` 中仅声明 Skill 入口工具，同时通过中间件注册业务工具以实现动态披露。

## 影响

- 受影响的代码：
  - `src/common/skill_router.py`
  - `src/common/tools.py` 或新的共享 Skill Tool 模块
  - `src/common/middleware/`
  - `src/basic_qa/graph.py`
  - `tests/unit_tests/` 下的测试
- 运行时行为：
  - 模型以较小的可见业务工具集开始。
  - 第一个业务步骤应调用 `find_skill`；匹配后，只有所选 Skill 允许的业务工具可见。
- 依赖项：
  - 复用现有的 `semantic-router`、OpenAI 兼容的 SiliconFlow 嵌入客户端和 LangChain 中间件 API。
