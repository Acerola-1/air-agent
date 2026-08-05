# 渐进式披露 Skill 和 Tool 的最佳实践

## 一、背景与问题

当前项目已经从 `Main Agent + SubAgent` 演进为多顶层 Graph 架构。每个业务入口都拥有独立的 `system_prompt`、`skills/` 目录、工具集合和 middleware，这解决了业务模块间的路由和输出搬运问题。

但在单个业务 Graph 内部，DeepAgent 仍然面临一个新的选择压力：

```text
用户问题
  ↓
业务 Graph
  ↓
模型同时看到：
  - 当前 Graph 的多个 Skill 元数据
  - 当前 Graph 绑定的大量 MCP / 本地工具
  - DeepAgents 内置工具
  ↓
模型自行判断要读哪个 Skill、调用哪个 Tool
```

这个流程的问题不在于 Skill 文档是否存在，而在于“发现 Skill”和“选择 Tool”仍然高度依赖模型自主判断。

随着 Skill 和 Tool 数量增长，模型容易出现以下问题：

- **漏选 Skill**：系统提示词中已经列出匹配 Skill，但模型直接凭常识回答，没有读取完整 Skill。
- **误选 Skill**：多个 Skill 描述相近时，模型根据关键词误判业务边界。
- **跳过 Skill 文件**：模型知道 Skill 名称，但没有读取 `SKILL.md` 或模式文件，导致执行流程不完整。
- **工具过多降智**：模型在大量 MCP 工具中选择时，容易调用相似但不适用的工具。
- **allowed-tools 只是软约束**：`SKILL.md` frontmatter 中已经声明 `allowed-tools`，但当前默认只是展示给模型看，不是运行时硬约束。
- **调试困难**：当回答错误时，很难判断是 Skill 没发现、Skill 没读取、还是 Tool 选错。

这些问题说明：仅靠 DeepAgents 默认 `SkillsMiddleware` 的“全量 Skill 元数据注入 + 模型自主发现”不够稳定。

## 二、参考启发：Mashibing SkillAgent 原型

`docs/mashibing/PythonProject-test/src/agent/skills_agent.py` 提供了一个有价值的原型。

它的核心思想是：

```text
create_agent(
    tools=[load_skill],
    middleware=[SkillMiddleware(categorized_tools)],
    state_schema=SkillState,
)
```

表面上，Agent 声明时只提供一个入口工具 `load_skill`。但 `SkillMiddleware` 内部会通过 `self.tools` 预注册所有潜在业务工具：

```text
SkillMiddleware.tools = 所有业务工具 + load_skill
```

运行时，每次模型调用前，middleware 根据 `state.skills_loaded` 动态构造 `request.tools`：

```text
未加载 Skill：
  模型只看到 load_skill

已加载 gaode_navigation：
  模型看到 load_skill + 高德工具

已加载 railway_booking：
  模型看到 load_skill + 铁路工具
```

这个原型证明了三个关键点：

1. **Agent 声明时只传入口工具是可行的**  
   DeepAgents / LangChain 的内置工具和 middleware 工具会在框架层合并，不要求所有业务工具都放在 `tools=` 参数里。

2. **业务工具必须预注册**  
   动态披露不是“临时创造工具”。所有可能被调用的业务工具仍然需要通过 `middleware.tools` 进入 ToolNode，否则模型即使看到了工具名也无法执行。

3. **工具可见性可以在 model call 前动态过滤**  
   `request.override(tools=dynamic_tools)` 可以让模型只看到当前 Skill 对应的工具集合。

Mashibing 原型的局限也很清楚：

- `load_skill` 依赖模型传入准确 Skill 名称，没有语义检索。
- Skill 与 Tool 的映射是代码硬编码，不是来自 Skill 元数据。
- 支持多个 `skills_loaded`，容易在复杂业务中重新膨胀工具集合。
- Skill 内容以内存列表维护，不适合当前项目已有的文件化 Skill 体系。

因此，当前项目应吸收它的 middleware 和动态工具披露思路，但用更适合本项目的 `find_skill + allowed-tools + SkillState` 方案替代。

## 三、目标架构

推荐的目标链路如下：

```text
用户问题
  ↓
业务 Graph
  ↓
模型初始只看到：
  - find_skill
  - DeepAgents 内置工具
  - 必要系统工具
  ↓
find_skill(question)
  ↓
语义匹配 Skill
  ↓
返回并写入 SkillState：
  - selected_skill
  - skill_path
  - description
  - allowed_tools
  - similarity_score
  ↓
SkillToolFilterMiddleware
  ↓
后续模型调用只看到：
  - find_skill
  - DeepAgents 内置工具
  - 当前 Skill 的 allowed-tools
  ↓
模型读取 Skill 文件并执行
```

这套方案把 Skill 和 Tool 的披露从“提示词建议”提升为“状态驱动的运行时约束”。

## 四、核心组件设计

### 1. `find_skill` 工具

`find_skill` 是业务 Graph 暴露给模型的 Skill 入口工具。

职责：

- 接收用户问题或模型整理后的查询文本。
- 使用向量匹配在当前 Graph 的 `skills/` 目录中寻找最匹配 Skill。
- 返回结构化结果。
- 将匹配结果写入 `SkillState`。

建议返回结构：

```json
{
    "matched": true,
    "skill_name": "air-quality-basic-query",
    "skill_path": "src/basic_qa/skills/air-quality-basic-query/SKILL.md",
    "description": "查询城市指定时间的基础空气质量数据、AQI、空气质量等级...",
    "allowed_tools": [
        "helper_get_latest_time",
        "text2sql_today_single_city_aqi_route",
        "text2sql_yesterday_single_index_aqi_route",
        "mcp_city_common_get_air_quality_realtime_stat"
    ],
    "similarity_score": 0.87,
    "next_step": "请读取 skill_path 对应的完整 Skill 说明，再执行查询。"
}
```

当未命中时：

```json
{
    "matched": false,
    "skill_name": null,
    "skill_path": null,
    "allowed_tools": [],
    "similarity_score": null,
    "next_step": "未找到明确匹配 Skill，请澄清问题或使用通用能力回答。"
}
```

实现上可以复用当前已有的 `src/common/skill_router.py`：

- 已有 `SkillSemanticRouter`
- 已有地名归一化
- 已有 SiliconFlow embedding encoder
- 已有语义匹配缓存

需要补齐：

- 解析 `allowed-tools`
- 返回完整 Skill metadata
- 提供面向 LangChain 的 `@tool` 包装
- 使用 `Command(update=...)` 写入 `SkillState`

### 2. `SkillState`

`SkillState` 用于保存当前会话或当前轮次选择的 Skill。

建议字段：

```python
class SkillState(AgentState):
    selected_skill: str | None
    selected_skill_path: str | None
    selected_skill_description: str | None
    selected_skill_allowed_tools: list[str]
    selected_skill_score: float | None
```

默认建议采用“单选 Skill”模式，而不是多个 `skills_loaded`。

原因：

- 当前空气质量业务大多数问题可以落到一个主 Skill。
- 单选状态可以避免多个 Skill 的 `allowed-tools` 合并后重新膨胀。
- 调试更清晰：一轮回答对应一个主 Skill。

复杂问题可以后续扩展为：

- 主 Skill + 辅助 Skill
- 多阶段 Skill chain
- 支持显式重置或切换 Skill

但第一阶段不建议一开始就支持任意多 Skill 累积。

### 3. `SkillToolRegistryMiddleware`

该 middleware 的职责是预注册所有可能被动态披露的业务工具。

为什么需要它：

- `tools=[find_skill]` 只会注册 `find_skill`。
- DeepAgents 会自动注册内置工具。
- 但业务 MCP 工具如果没有通过 `tools=` 或 `middleware.tools` 注册，就无法被 ToolNode 执行。

推荐做法：

```text
create_deep_agent(
    tools=[find_skill],
    middleware=[
        SkillToolRegistryMiddleware(all_business_tools),
        SkillToolFilterMiddleware(),
        ...
    ],
)
```

其中：

```python
SkillToolRegistryMiddleware.tools = all_business_tools
```

这个 middleware 不需要改模型请求，只负责让 ToolNode 知道所有业务工具。

### 4. `SkillToolFilterMiddleware`

该 middleware 负责动态过滤模型可见工具。

规则：

```text
未选中 Skill：
  保留 find_skill
  保留 DeepAgents 内置工具
  隐藏业务工具

已选中 Skill：
  保留 find_skill
  保留 DeepAgents 内置工具
  仅披露 selected_skill_allowed_tools 中声明的业务工具
```

实现方式：

```python
filtered_tools = [
    tool for tool in request.tools
    if is_builtin_tool(tool)
    or tool_name(tool) == "find_skill"
    or tool_name(tool) in selected_skill_allowed_tools
]

request = request.override(tools=filtered_tools)
```

注意：DeepAgents 的内置工具不要被 `allowed-tools` 抹除。

内置工具包括但不限于：

- `read_file`
- `ls`
- `grep`
- `glob`
- `write_todos`
- `task`
- 其他由 DeepAgents middleware 注入的工具

系统必要工具也可以配置白名单，例如：

- `find_skill`
- `get_beijing_time`

### 5. Skill 文档与 `allowed-tools`

`SKILL.md` 的 frontmatter 应成为 Tool 披露的唯一事实来源。

示例：

```yaml
---
name: air-quality-basic-query
description: |
    查询城市指定时间的基础空气质量数据、AQI、空气质量等级、首要污染物和各污染物浓度
allowed-tools: helper_get_latest_time text2sql_today_single_city_aqi_route text2sql_yesterday_single_index_aqi_route mcp_city_common_get_air_quality_realtime_stat
---
```

最佳实践：

- `description` 负责“什么时候用这个 Skill”。
- `allowed-tools` 负责“这个 Skill 运行时能用哪些业务工具”。
- Skill 正文负责“如何按步骤执行”。
- `references/fast.md` 和 `references/expert.md` 负责不同模式的细化流程。

## 五、和当前方案的对比

| 维度          | 当前默认 SkillsMiddleware         | 渐进式披露方案                         |
| ------------- | --------------------------------- | -------------------------------------- |
| Skill 发现    | 模型阅读全量 Skill 列表后自主判断 | `find_skill` 语义匹配                  |
| Skill 状态    | 没有显式运行时状态                | `SkillState` 记录当前选中 Skill        |
| Tool 可见性   | Graph 绑定的业务工具基本全量可见  | 根据 `allowed-tools` 动态披露          |
| allowed-tools | 提示词软约束                      | middleware 硬过滤                      |
| 可调试性      | 难以判断错在发现还是执行          | 可记录匹配分数、Skill、工具集合        |
| 扩展成本      | Skill 越多 prompt 越长、选择越难  | Skill 越多主要影响检索索引             |
| 失败兜底      | 依赖模型自我修正                  | 可定义未命中、低分、工具缺失的明确策略 |

## 六、推荐执行链路

### 普通命中场景

```text
用户：今天洛阳市空气质量怎么样？
  ↓
模型调用 find_skill("今天洛阳市空气质量怎么样？")
  ↓
find_skill 命中 air-quality-basic-query
  ↓
SkillState.selected_skill_allowed_tools = [...]
  ↓
middleware 披露该 Skill 的 allowed-tools
  ↓
模型读取 SKILL.md
  ↓
模型按 Skill 指令调用 helper_get_latest_time / text2sql / MCP 工具
  ↓
最终回答
```

### 未命中场景

```text
用户问题
  ↓
find_skill 未命中或低于阈值
  ↓
SkillState 不设置 selected_skill
  ↓
业务工具继续隐藏
  ↓
模型澄清问题，或使用通用非业务能力回答
```

### 切换 Skill 场景

同一线程中用户提出新问题时，应允许重新调用 `find_skill` 覆盖当前 `SkillState`。

推荐规则：

- 每个新 HumanMessage 默认允许重新匹配。
- `find_skill` 成功后覆盖旧 Skill。
- 如果新问题是追问，模型可以带上下文调用 `find_skill`，也可以沿用当前 Skill。
- 可在 `SkillState` 中记录 `selected_at_message_id`，便于判断是否跨轮复用。

## 七、落地计划

### 第一阶段：基础能力

1. 扩展 `src/common/skill_router.py`
    - 解析 `allowed-tools`
    - 使用更稳妥的 YAML 解析方式
    - 返回完整 Skill metadata

2. 新增 `find_skill` 工具
    - 基于当前 Graph 的 skills 目录构建 router
    - 返回结构化匹配结果
    - 使用 `Command(update=...)` 写入 `SkillState`

3. 新增 `SkillState`
    - 保存当前选中 Skill
    - 保存 `allowed_tools`
    - 保存匹配分数和路径

4. 新增工具注册 middleware
    - 通过 `middleware.tools` 预注册所有业务工具
    - 保持 `create_deep_agent(tools=[find_skill])` 的声明简洁

5. 新增工具过滤 middleware
    - 未选中 Skill 时隐藏业务工具
    - 选中 Skill 后仅披露 `allowed-tools`
    - 保留 DeepAgents 内置工具和入口工具

### 第二阶段：接入业务 Graph

1. 选择 `basic-qa` 作为首个试点。
2. 调整 graph 创建方式：
    - `tools=[find_skill]`
    - 业务 MCP 工具改由 registry middleware 预注册
    - 加入 filter middleware
3. 调整 system prompt：
    - 明确要求业务问题先调用 `find_skill`
    - 弱化“从全量 Skill 列表自行发现”的提示
4. 验证典型问题：
    - 基础空气质量查询
    - 排名考核
    - 达标可行性
    - 对比分析
    - 趋势分析

### 第三阶段：推广和优化

1. 推广到 `intelligent-analysis`。
2. 为每个 Graph 维护独立 router 实例。
3. 增加调试日志：
    - 用户问题
    - 命中 Skill
    - 相似度
    - 披露工具列表
    - 未命中原因
4. 增加单元测试：
    - `allowed-tools` 解析
    - 工具过滤规则
    - 内置工具保留规则
    - 未命中兜底
5. 增加回归测试：
    - 典型业务问题能命中正确 Skill
    - 不在 `allowed-tools` 中的业务工具不会暴露给模型

## 八、关键风险与规避

### 风险一：`find_skill` 低分误命中

规避：

- 保留阈值，例如 `0.80`。
- 低分时返回未命中。
- 支持返回 top-k 候选供模型二次判断，但第一阶段建议只启用 top-1。

### 风险二：`allowed-tools` 配置遗漏

规避：

- 启动时校验 Skill 声明的工具名是否存在于工具注册表。
- 对缺失工具记录 warning。
- 测试覆盖每个 Skill 的工具名完整性。

### 风险三：业务工具被全部隐藏导致无法执行

规避：

- `find_skill` 成功后必须写入 `selected_skill_allowed_tools`。
- 工具过滤 middleware 需要日志输出最终可见工具。
- 低风险阶段可以配置 debug 开关，允许观测模式只记录不隐藏。

### 风险四：内置工具误删

规避：

- 内置工具使用保留白名单或来源标记。
- `find_skill`、文件读取、todo 等基础工具永远保留。
- 对 DeepAgents 版本升级后新增内置工具做回归验证。

### 风险五：多轮对话 Skill 状态过期

规避：

- 每轮新问题允许重新调用 `find_skill`。
- 可记录当前 Skill 对应的 HumanMessage 序号。
- 当用户明显切换业务意图时覆盖旧 Skill。

## 九、推荐原则

1. **Skill 发现工具化**  
   不再让模型只靠全量 Skill 列表自主发现，业务问题优先调用 `find_skill`。

2. **Tool 披露状态化**  
   工具可见性由 `SkillState` 决定，而不是由 prompt 文本建议。

3. **业务工具预注册，运行时再过滤**  
   所有可能执行的业务工具通过 middleware 注册，模型每轮只看到当前允许的子集。

4. **`allowed-tools` 成为执行约束**  
   Skill frontmatter 中的 `allowed-tools` 不只是文档字段，而是动态工具披露的依据。

5. **先单 Skill，再考虑多 Skill**  
   第一阶段保持一个问题一个主 Skill，避免工具集合重新膨胀。

6. **保留 DeepAgents 内置工具**  
   文件读取、todo、任务管理等框架工具不受业务 Skill 的 `allowed-tools` 限制。

7. **可观测优先**  
   Skill 命中、分数、工具披露列表必须有日志，否则问题定位会很困难。

## 十、结论

渐进式披露 Skill 和 Tool 的核心价值，是把模型的选择空间分两步收敛：

```text
第一步：find_skill 收敛业务流程
第二步：allowed-tools 收敛工具集合
```

这比“把所有 Skill 和 Tool 都交给模型自己判断”更稳定，也更符合当前项目的工程化方向。

当前项目已经具备落地基础：

- Graph 已按业务拆分。
- Skill 已文件化，并包含 `allowed-tools`。
- `src/common/skill_router.py` 已有语义匹配基础。
- LangChain middleware 支持通过 `request.override(tools=...)` 动态过滤工具。
- Mashibing 原型已经验证了“入口工具 + middleware 预注册 + 动态披露”的可行性。

因此，建议后续以 `basic-qa` 为试点，将当前 DeepAgents 默认 Skill 发现机制升级为 `find_skill + SkillState + allowed-tools 动态工具披露`，验证稳定后再推广到其他业务 Graph。
