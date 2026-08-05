# DeepAgent 统一底座重构计划

> 合并自 `architecture_evaluation.md` + `skill_router_evaluation.md`，整合最终决策

## 一、重构目标与核心诉求

### 1.1 当前痛点

| 痛点                 | 具体表现                                                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **提示词三处散落**   | Expert 模式提示词在 `skills/` 目录；Fast 模式提示词在 `prompts.py` 和 MCP 远程模板中；维护困难，修改容易遗漏      |
| **路由与实现强耦合** | `intent_config.py` 同时承载路由（utterances）和实现配置（template_name、mcp_tool_name、tools、display），职责不清 |
| **图节点冗余**       | planner/executor/replanner 三节点循环、knowledge_agent 单独分支、parallel_tool_executor 硬编码流程，图结构复杂    |
| **能力扩展成本高**   | 新增意图需改 intent_config.py + prompts.py + 远程模板 + graph.py 路由函数，改 4 处文件                            |
| **模式逻辑分裂**     | Fast/Expert 在 `chat_mode_init` 后走完全不同的代码路径，行为一致性难保证                                          |

### 1.2 重构目标

**以 DeepAgent 为统一底座，Skills 为可插拔能力单元，语义路由为精准入口。**

1. **提示词统一归 Skills** — 消除 prompts.py 和远程模板中的散落提示词，所有提示词由 Skill 文件定义
2. **图结构极简** — 权限校验后直接进入 DeepAgent，去掉 planner/executor/replanner、parallel_tool_executor、knowledge_agent 等冗余节点
3. **能力可插拔** — 新增能力 = 新增 SKILL.md + 注册工具，不改代码
4. **语义路由保留为 Tool** — intent_config.py 转为 DeepAgent 可调用的工具，精准匹配 15 个模板问题；未命中时由 DeepAgent 自主决策

---

## 二、架构设计

### 2.1 新旧架构对比

```
┌─────────────────────────────────────────────────────────────────────┐
│  当前架构                                                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  START → build_contextual_question → fetch_time_context             │
│        → parse_region_expansion → permission_agent                  │
│        → permission_eval → chat_mode_init                           │
│            ├─ fast:  unified_intent_match → (5路分支)                │
│            │        ├─ parallel_tool_executor → generate            │
│            │        ├─ knowledge_agent → knowledge_retrieve          │
│            │        │       → grade_documents → rewrite/END         │
│            │        ├─ planner → executor → replanner → generate    │
│            │        └─ generate (DIRECT)                            │
│            └─ expert: expert_subagent (DeepAgent) → END             │
│        → expand_question → END                                      │
│                                                                     │
│  节点数: 17+  |  路由函数: 6个  |  提示词来源: 3处                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  重构后架构                                                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  START → build_contextual_question → permission_agent               │
│        → permission_eval → chat_mode_init → deepagent (统一底座)    │
│        → expand_question → END                                      │
│                                                                     │
│  chat_mode_init:                                                    │
│    - 读取 Configuration.chat_mode 写入 state                       │
│    - 不再做模式分叉，统一指向 deepagent_executor                    │
│                                                                     │
│  deepagent 内部 (根据 chat_mode 选择实例):                          │
│    - Fast:  硅基流动 DeepSeek-V3 + skills_fast/ 目录               │
│    - Expert: 火山引擎 DeepSeek-V3 + skills_expert/ 目录            │
│    - 可调用 intent_match_tool (语义路由，精准匹配15个模板)           │
│    - 可调用 parse_region_tool (地址解析)                             │
│    - 可调用 get_latest_time_tool (获取最新时间)                      │
│    - 可调用 text2sql_tool / MCP 工具 / knowledge_retriever_tool / websearch                        │
│    - 根据 Skills 配置决定工具调用流程和输出规范                      │
│    - 未命中模板时，自主决策调用哪些工具                              │
│                                                                     │
│  节点数: 7   |  路由函数: 4个  |  提示词来源: 1处 (Skills)          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计决策

| 决策                       | 选择                            | 理由                                                                      |
| -------------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| 意图匹配方式               | 语义路由转为 Tool               | 15 个模板问题需要精准可控的回答，语义路由 ~50ms 比 LLM 自主判断更可靠更快 |
| 未匹配意图处理             | DeepAgent 自主决策              | DeepAgent 具备 plan 能力，能自行判断调用哪些工具                          |
| fetch_time_context         | 转为 Tool                       | 不是每次查询都需要时间上下文，由 Skill/Agent 决定是否调用                 |
| parse_region_expansion     | 转为 Tool                       | 不是每次查询都需要地址解析，由 Skill/Agent 决定是否调用                   |
| planner/executor/replanner | 移除                            | DeepAgent 内置 plan-revise 循环，不需要外层三节点循环                     |
| knowledge_agent 分支       | 转为 `knowledge_retriever_tool` | 知识检索封装为独立工具，由 Skill/Agent 按需调用                           |
| parallel_tool_executor     | 移除                            | DeepAgent 自行管理工具调用，包括并行调用                                  |
| prompts.py 中的提示词      | 迁移到 Skills                   | 统一提示词管理入口                                                        |

### 2.3 语义路由作为 Tool 的设计

将 `intent_config.py` 的语义路由能力封装为 DeepAgent 可调用的工具：

```python
@tool
def intent_match_tool(question: str) -> dict:
    """根据用户问题匹配预设的意图模板。

    当用户问题属于已知的15个空气质量查询模板时，返回匹配结果和对应的Skill名称。
    当无法匹配时返回未命中，由Agent自行决策后续操作。

    Args:
        question: 用户的问题文本

    Returns:
        匹配结果字典，包含:
        - matched: bool, 是否匹配到预设意图
        - skill_name: str|None, 匹配到的Skill名称
        - intent_type: str|None, 意图类型 (mcp/text2sql)
        - display_type: str|None, 前端展示类型 (inline/canvas)
        - confidence: float, 匹配置信度
    """
    result = match_intent(question)
    if result is None:
        return {"matched": False, "skill_name": None, "intent_type": None,
                "display_type": None, "confidence": 0.0}

    # Route name → Skill 目录名称的映射
    ROUTE_TO_SKILL = {
        "broadcastHour": "broadcast-hour",
        "integratedIndexRatio": "integrated-index-ratio",
        "oneIndexManyCityLine": "one-index-many-city-line",
        "primaryPollutantProportionAnaly": "primary-pollutant-proportion",
        "compositeIndexSixFactorRatio": "composite-index-six-factor",
        "cityFactorRankings": "city-factor-rankings",
        "cityCalculations": "city-calculations",
        "city_compliance_prediction": "city-compliance-prediction",
        "yesterday_single_index_aqi_route": "yesterday-single-city-aqi",
        "today_single_city_aqi_route": "today-single-city-aqi",
        "air_quality_trends_route": "air-quality-trends",
        "monthly_and_yearly_city_comparison_route": "monthly-yearly-city-comparison",
        "province_city_rank_route": "province-city-rank",
        "station_worst_route": "station-worst",
        "station_pollution_level_route": "station-pollution-level",
    }

    skill_name = ROUTE_TO_SKILL.get(result.name)
    return {
        "matched": True,
        "skill_name": skill_name,
        "intent_type": result.type.value,
        "display_type": result.display.value,
        "confidence": match.similarity_score,
    }
```

**关键设计点：**

- 工具描述中明确说明"15 个已知模板"和"未命中时自行决策"，引导 DeepAgent 优先尝试此工具
- 返回结构化结果，DeepAgent 可据此读取对应 Skill 文件
- 未命中时 DeepAgent 不受约束，可自主规划工具调用

### 2.4 地址解析 Tool 的设计

将 `parse_region_expansion` 的核心逻辑提取为工具：

```python
@tool
def parse_region_tool(question: str) -> dict:
    """解析用户问题中的行政区划信息，支持省市区的展开和查询。

    当用户问题包含省/市/区等地名且需要解析其下级区域列表时调用此工具。
    例如"浙江省的所有城市"会返回浙江省下辖城市列表。

    Args:
        question: 用户的问题文本

    Returns:
        解析结果字典，包含:
        - has_region: bool, 是否包含行政区划信息
        - regions: list[str], 解析后的区域列表
        - raw_entities: list[dict], 原始提取的行政区划实体
    """
```

**与原节点的区别：**

- 原节点：每次查询都执行，结果写入 `region_expansion` 字段，下游节点被动消费
- 新工具：按需调用，只有 Skill/Agent 判断需要地址信息时才调用

### 2.5 时间获取 Tool 的设计

```python
@tool
def get_latest_time_tool(thread_id: str) -> dict:
    """获取系统数据最新时间信息。

    返回各类数据的最新更新时间戳，用于确定查询的时间范围。
    结果会被缓存20分钟。

    Args:
        thread_id: 会话ID，用于获取对应权限的时间数据

    Returns:
        时间信息字典，包含:
        - time_mapping: dict, 各数据类型的最新更新时间
    """
```

**与原节点的区别：**

- 原节点：每次查询都执行，Redis 缓存命中时 ~5ms，未命中时 ~500ms
- 新工具：按需调用，Skill 中可明确指定"步骤1：获取最新时间"
- Redis 缓存逻辑保留在工具内部

### 2.6 知识检索 Tool 的设计

将 `knowledge_agent` + `knowledge_retrieve` + `grade_documents` 整合封装为独立工具：

```python
@tool
def knowledge_retriever_tool(query: str, top_k: int = 5) -> dict:
    """检索环境空气知识库，返回与查询相关的政策法规和技术文档。

    用于环保领域知识性问题的检索，如政策法规、技术标准、环境术语解释等。
    当用户问题属于知识查询类（非数据查询）时调用此工具。

    Args:
        query: 检索查询文本
        top_k: 返回的最大文档数量，默认5

    Returns:
        检索结果字典，包含:
        - success: bool, 检索是否成功
        - documents: list[dict], 检索到的文档列表，每项包含:
          - content: str, 文档内容
          - source: str, 文档来源
          - score: float, 相似度分数
        - query: str, 原始查询文本
    """
```

**与原 knowledge_agent 分支的区别：**

- 原 knowledge_agent：独立图分支，包含 retrieve → grade_documents → rewrite 循环，流程复杂
- 新工具：单次调用返回结果，文档相关性判断由 DeepAgent 自行决策
- 如果检索结果不足，DeepAgent 可自主优化查询并再次调用，比硬编码的 rewrite 循环更灵活

---

## 三、Skills 体系设计

### 3.1 Fast/Expert 双模式设计

#### 3.1.1 模式差异分析

| 维度         | Fast 模式                      | Expert 模式                                |
| ------------ | ------------------------------ | ------------------------------------------ |
| **模型**     | DeepSeek-V3 (硅基流动)         | DeepSeek-V3 (火山引擎)                     |
| **工具范围** | 精简，仅核心业务工具           | 完整，包含网格数据、火点等辅助工具         |
| **输出深度** | 简洁：开篇速答 + 3-5句核心结论 | 详尽：完整分析报告（趋势、对标、管控建议） |
| **分析步骤** | 最少步骤完成数据获取和回答     | 多步骤深度分析，可补充网格/火点/轨迹数据   |
| **提示词**   | `skills_fast/` 下的 SKILL.md   | `skills_expert/` 下的 SKILL.md             |

#### 3.1.2 双模式架构设计

Fast 和 Expert 模式的 Skills 内容**完全不一致**（不仅是补充关系），因此采用**两套独立 Skills 目录**的设计：

```
┌──────────────────────────────────────────────────────────┐
│  skills_fast/                                            │
│  ├── AGENTS.md           # Fast 模式全局约束             │
│  ├── broadcast-hour/                                     │
│  │   └── SKILL.md        # Fast 模式的小时播报流程        │
│  ├── ...                                                 │
├──────────────────────────────────────────────────────────┤
│  skills_expert/                                          │
│  ├── AGENTS.md           # Expert 模式全局约束            │
│  ├── broadcast-hour/                                     │
│  │   └── SKILL.md        # Expert 模式的小时播报流程      │
│  ├── ...                                                 │
└──────────────────────────────────────────────────────────┘
```

**为什么不用 SKILL.md + EXPERT.md 继承模式？**

- 两种模式的 Skill 内容**不是补充关系，而是完全独立**：Fast 模式流程简洁、工具少、输出短；Expert 模式流程复杂、工具多、输出详尽
- 继承模式会导致 SKILL.md 膨胀（同时包含两种模式的流程），且 AGENTS-FAST.md 需要大量"禁止"规则来限制 Expert 内容
- 独立目录更清晰：每个 SKILL.md 只描述自己模式的内容，无需模式判断逻辑

**DeepAgent 如何感知模式？**

通过两个独立的 DeepAgent 实例实现，各自指向不同的 Skills 目录和 AGENTS.md：

```
┌─────────────────────────────┐  ┌─────────────────────────────┐
│  Fast 模式 DeepAgent        │  │  Expert 模式 DeepAgent      │
│  model: 硅基流动 DeepSeek   │  │  model: 火山引擎 DeepSeek   │
│  skills: skills_fast/       │  │  skills: skills_expert/     │
│  memory:                    │  │  memory:                    │
│    - skills_fast/AGENTS.md  │  │    - skills_expert/AGENTS.md│
│  行为:                      │  │  行为:                      │
│    - 读取 skills_fast/ 下的 │  │    - 读取 skills_expert/ 下的│
│      SKILL.md               │  │      SKILL.md               │
│    - 简洁流程 + 快速输出    │  │    - 深度流程 + 详尽输出    │
└─────────────────────────────┘  └─────────────────────────────┘
```

#### 3.1.3 模式选择机制

`deepagent_executor` 节点根据 `Configuration.chat_mode` 选择对应的 DeepAgent 实例：

```python
async def deepagent_executor(state: AgentState, config: RunnableConfig):
    chat_mode = Configuration.from_runnable_config(config).chat_mode
    question = _extract_question(state)

    if chat_mode == "expert":
        wrapper = _get_expert_wrapper()   # 火山引擎 DeepSeek-V3
    else:
        wrapper = _get_fast_wrapper()     # 硅基流动 DeepSeek-V3

    return await wrapper.run(question, state, config)
```

两个 DeepAgent 实例在首次调用时延迟初始化，各自持有不同的模型和 memory 配置，但共享同一个 Skills 目录。

### 3.2 目录结构

两套独立的 Skills 目录，各自包含完整的 AGENTS.md 和 Skill 子目录：

```
src/agent/
├── skills_fast/                             # Fast 模式 Skills
│   ├── AGENTS.md                            # Fast 模式全局约束（角色、简洁输出规范、工具使用指引）
│   ├── broadcast-hour/
│   │   └── SKILL.md                         # Fast 模式的小时播报流程（简洁）
│   ├── integrated-index-ratio/
│   │   └── SKILL.md
│   ├── one-index-many-city-line/
│   │   └── SKILL.md
│   ├── primary-pollutant-proportion/
│   │   └── SKILL.md
│   ├── composite-index-six-factor/
│   │   └── SKILL.md
│   ├── city-factor-rankings/
│   │   └── SKILL.md
│   ├── city-calculations/
│   │   └── SKILL.md
│   ├── city-compliance-prediction/
│   │   └── SKILL.md
│   ├── yesterday-single-city-aqi/
│   │   └── SKILL.md
│   ├── today-single-city-aqi/
│   │   └── SKILL.md
│   ├── air-quality-trends/
│   │   └── SKILL.md
│   ├── monthly-yearly-city-comparison/
│   │   └── SKILL.md
│   ├── province-city-rank/
│   │   └── SKILL.md
│   ├── station-worst/
│   │   └── SKILL.md
│   ├── station-pollution-level/
│   │   └── SKILL.md
│   └── knowledge-query/
│       └── SKILL.md
│
├── skills_expert/                           # Expert 模式 Skills
│   ├── AGENTS.md                            # Expert 模式全局约束（角色、详尽输出规范、深度分析指引）
│   ├── broadcast-hour/
│   │   └── SKILL.md                         # Expert 模式的小时播报流程（详尽分析 + 额外工具 + 管控建议）
│   ├── integrated-index-ratio/
│   │   └── SKILL.md
│   ├── one-index-many-city-line/
│   │   └── SKILL.md
│   ├── primary-pollutant-proportion/
│   │   └── SKILL.md
│   ├── composite-index-six-factor/
│   │   └── SKILL.md
│   ├── city-factor-rankings/
│   │   └── SKILL.md
│   ├── city-calculations/
│   │   └── SKILL.md
│   ├── city-compliance-prediction/
│   │   └── SKILL.md
│   ├── yesterday-single-city-aqi/
│   │   └── SKILL.md
│   ├── today-single-city-aqi/
│   │   └── SKILL.md
│   ├── air-quality-trends/
│   │   └── SKILL.md
│   ├── monthly-yearly-city-comparison/
│   │   └── SKILL.md
│   ├── province-city-rank/
│   │   └── SKILL.md
│   ├── station-worst/
│   │   └── SKILL.md
│   ├── station-pollution-level/
│   │   └── SKILL.md
│   └── knowledge-query/
│       └── SKILL.md
```

**说明**：

- 两套目录结构完全对称（同名 Skill 子目录），但每个 SKILL.md 的内容不同
- Fast 模式的 SKILL.md：简洁流程、较少工具调用、简短输出
- Expert 模式的 SKILL.md：完整分析流程、额外工具（网格/火点/轨迹）、详尽输出规范
- 如果某种模式不需要某个 Skill（如 Expert 不需要某个简单查询），对应目录可以不存在

### 3.3 SKILL.md 格式规范

采用 YAML frontmatter + Markdown body 的单文件格式，每个 SKILL.md 只描述当前模式的内容：

**Fast 模式示例** (`skills_fast/broadcast-hour/SKILL.md`)：

```markdown
---
name: broadcast-hour
description: 小时播报数据查询（快速模式）
version: 1.0.0
allowed-tools:
    - broadcastHour
    - intent_match_tool
    - parse_region_tool
    - get_latest_time_tool
---

# 小时播报数据查询

## 功能说明

查询某地区某日六参数据的小时级播报。

## 执行流程

1. 调用 `get_latest_time_tool` 获取最新数据时间
2. 调用 `broadcastHour` 获取数据（如用户询问历史日期，使用该日23:00的小时时间；如为当日，使用最新小时时间）
3. 基于数据内容，输出 3-5 句核心结论

## 数据缺失处理

任一维度数据缺失时，不分析和输出对应板块的内容，核心结论不依赖缺失维度。
```

**Expert 模式示例** (`skills_expert/broadcast-hour/SKILL.md`)：

```markdown
---
name: broadcast-hour
description: 小时播报数据分析（专家模式）
version: 1.0.0
allowed-tools:
    - broadcastHour
    - gridData
    - intent_match_tool
    - parse_region_tool
    - get_latest_time_tool
---

# 小时播报数据分析

## 功能说明

深度分析某地区某日六参数据的小时级播报，包含趋势研判、异常风险预警和管控建议。

## 执行流程

1. 调用 `get_latest_time_tool` 获取最新数据时间
2. 调用 `parse_region_tool` 解析区域信息（如需）
3. 调用 `broadcastHour` 获取数据
4. 可选：调用 `gridData` 获取1km近地面网格数据，用于异常污染空间分布研判
5. 生成完整分析报告

## 核心总结要求（优先级：预警/趋势＞概况＞对标＞应急＞管控建议）

1. 实时概况速览：精准概括截至当前时段的空气质量整体等级、首要污染物、浓度变化趋势
2. 双维度异常风险预警：结合气象实况和网格数据，判定污染来源
3. 对标差距专业分析：精准定位与对标城市的核心差距时段和因子
4. 临近趋势预判：基于未来0-12小时气象与空气质量临近预报
5. 重污染过程跟踪：若处于应急响应期间，总结污染全流程特征
6. 值守管控建议：给出可直接落地的应急值守管控建议

## 外部数据适配与使用规则

| 适配外部数据            | 使用场景约束       | 体量控制   | 缺失处理        |
| ----------------------- | ------------------ | ---------- | --------------- |
| 1km近地面六因子网格数据 | 可选，空间分布研判 | ≤100个网格 | 缺失/超量时跳过 |

## 数据缺失处理

任一维度数据缺失时，不分析和输出对应板块的内容，核心结论不依赖缺失维度。
```

**关键设计点：**

1. 每个 SKILL.md 只描述当前模式的流程，不需要模式判断逻辑
2. `allowed-tools` 仅列出当前模式需要的工具（Expert 模式比 Fast 多 `gridData` 等）
3. 两种模式的 SKILL.md 内容完全独立，互不影响

### 3.4 AGENTS.md 设计

两套独立的 AGENTS.md，分别位于各自的 Skills 目录下：

#### `skills_fast/AGENTS.md`（Fast 模式）

```markdown
# 环境空气数据分析助手

## 角色定位

你是一个环境空气质量数据的快速查询助手，为用户提供简洁、准确的数据查询结果。

## 行为约束

1. **意图匹配**：优先调用 `intent_match_tool`，判断用户问题是否属于已知模板
    - 如果命中已知模板：读取对应的 SKILL.md，严格按流程执行
    - 如果未命中：自行分析用户意图，选择合适的工具组合

2. **工具使用优先级**：
    - 意图匹配 → 时间获取 → 地址解析 → 业务工具调用

3. **输出规范**：
    - 开篇速答（≤30字）
    - 3-5 句核心结论
    - 不展开深度分析、对标分析、管控建议

4. **效率优先**：以最少的工具调用步骤完成回答

5. **数据缺失处理**：任一维度数据缺失时，不分析和输出对应板块的内容，核心结论不依赖缺失维度
```

#### `skills_expert/AGENTS.md`（Expert 模式）

```markdown
# 环境空气数据分析专家

## 角色定位

你是一个资深环境空气数据分析专家，为用户提供专业、详尽的空气质量分析报告。

## 行为约束

1. **意图匹配**：优先调用 `intent_match_tool`，判断用户问题是否属于已知模板
    - 如果命中已知模板：读取对应的 SKILL.md，严格按流程执行
    - 如果未命中：自行分析用户意图，选择合适的工具组合

2. **工具使用优先级**：
    - 意图匹配 → 时间获取 → 地址解析 → 核心业务工具 → 补充数据工具（网格/火点/轨迹）

3. **输出规范**：
    - 完整的分析报告结构
    - 包含趋势分析、对标分析、管控建议
    - 核心结论不依赖缺失维度
    - 遵守 SKILL.md 中的核心总结要求优先级

4. **深度优先**：充分利用可用工具获取补充数据，提供专业研判

5. **数据缺失处理**：任一维度数据缺失时，不分析和输出对应板块的内容，核心结论不依赖缺失维度
```

### 3.5 TEXT2SQL 工具与 Skill 设计

#### text2sql_tool 设计

将 Text2SQL 封装为独立工具，保留 `template_name` 参数：

```python
@tool
def text2sql_tool(question: str, template_name: str | None = None) -> dict:
    """将自然语言问题转换为 SQL 查询并执行，返回查询结果。

    用于空气质量数据的数据库查询，支持城市AQI、趋势对比、站点数据等查询。
    当 intent_match_tool 返回 intent_type 为 text2sql 时使用此工具。

    Args:
        question: 用户的自然语言查询问题
        template_name: SQL生成模板名称（可选），用于提高SQL生成准确性。
            可选值由 intent_match_tool 返回结果中的 skill_name 决定。

    Returns:
        查询结果字典，包含:
        - success: bool, 查询是否成功
        - sql: str, 生成的 SQL 语句
        - data: list, 查询结果数据
        - error: str|None, 错误信息
    """
```

**设计要点：**

- `template_name` 参数保留，由 Skill 在调用时传入，确保 Vanna SQL 生成质量
- 工具描述中说明 `template_name` 由 `intent_match_tool` 返回结果决定，引导 DeepAgent 正确传参
- Skill 中明确指定 `template_name` 值，例如 `yesterday-single-city-aqi` Skill 中写明"调用 text2sql_tool，传入 template_name='text2sql_yesterday_single_index_aqi_route'"

#### TEXT2SQL 类型 Skill 示例

**Fast 模式** (`skills_fast/yesterday-single-city-aqi/SKILL.md`)：

```markdown
---
name: yesterday-single-city-aqi
description: 查询昨日某城市空气质量指数（快速模式）
version: 1.0.0
allowed-tools:
    - text2sql_tool
    - intent_match_tool
    - parse_region_tool
    - get_latest_time_tool
---

# 昨日城市空气质量查询

## 功能说明

查询昨日某城市（或多个城市）的空气质量指数数据。

## 执行流程

1. 调用 `get_latest_time_tool` 获取最新数据时间，确认"昨日"的具体日期
2. 如问题包含多个地名，调用 `parse_region_tool` 解析
3. 调用 `text2sql_tool`，传入 template_name='text2sql_yesterday_single_index_aqi_route'
4. 基于查询结果生成简洁回答：开篇速答 + 关键指标数值
```

**Expert 模式** (`skills_expert/yesterday-single-city-aqi/SKILL.md`)：

```markdown
---
name: yesterday-single-city-aqi
description: 查询昨日某城市空气质量指数（专家模式）
version: 1.0.0
allowed-tools:
    - text2sql_tool
    - intent_match_tool
    - parse_region_tool
    - get_latest_time_tool
---

# 昨日城市空气质量查询

## 功能说明

查询并深度分析昨日某城市（或多个城市）的空气质量指数数据。

## 执行流程

1. 调用 `get_latest_time_tool` 获取最新数据时间
2. 调用 `parse_region_tool` 解析区域信息
3. 调用 `text2sql_tool`，传入 template_name='text2sql_yesterday_single_index_aqi_route'
4. 基于查询结果生成详尽分析，包含多城市对比（如有）、趋势变化、核心差距分析
```

**说明**：TEXT2SQL 类型的 Skill 两种模式差异主要体现在输出详尽程度上，核心流程相似但输出规范不同。

### 3.6 Knowledge 类型的 Skill 设计

**Fast 模式** (`skills_fast/knowledge-query/SKILL.md`)：

```markdown
---
name: knowledge-query
description: 环境保护知识、政策法规检索（快速模式）
version: 1.0.0
allowed-tools:
    - knowledge_retriever_tool
    - intent_match_tool
---

# 环保知识检索

## 功能说明

检索环境空气质量知识库和政策法规库，回答环保领域知识性问题。

## 执行流程

1. 调用 `intent_match_tool` 确认不属于数据查询类意图
2. 调用 `knowledge_retriever_tool` 检索相关知识文档
3. 基于检索结果生成简洁回答
```

**Expert 模式** (`skills_expert/knowledge-query/SKILL.md`)：

```markdown
---
name: knowledge-query
description: 环境保护知识、政策法规深度检索（专家模式）
version: 1.0.0
allowed-tools:
    - knowledge_retriever_tool
    - intent_match_tool
---

# 环保知识深度检索

## 功能说明

深度检索环境空气质量知识库和政策法规库，提供有依据的专业解答。

## 执行流程

1. 调用 `intent_match_tool` 确认不属于数据查询类意图
2. 调用 `knowledge_retriever_tool` 检索相关知识文档
3. 如果检索结果不足，优化查询并再次调用 `knowledge_retriever_tool`
4. 基于检索结果生成详尽回答，引用相关政策法规依据
```

---

## 四、图结构改造

### 4.1 新图结构

```python
"""重构后的 LangGraph 图结构."""

from langgraph.graph import END, START, StateGraph
from agent.nodes import (
    AgentState,
    build_contextual_question,
    chat_mode_init,
    deepagent_executor,
    expand_question,
    generate,
    get_checkpointer,
    permission_agent,
    permission_eval,
    permission_interrupt_node,
    permission_mcp_tools,
    route_after_permission_agent,
    route_after_permission_eval,
    route_after_permission_interrupt,
)

workflow = StateGraph(AgentState)

# 基础节点
workflow.add_node("build_contextual_question", build_contextual_question)
workflow.add_node("permission_agent", permission_agent)
workflow.add_node("permission_eval", permission_eval)
workflow.add_node("permission_interrupt", permission_interrupt_node)
workflow.add_node("chat_mode_init", chat_mode_init)

# 统一 DeepAgent 执行节点（替代 expert_subagent + unified_intent_match +
#   parallel_tool_executor + knowledge_agent + planner/executor/replanner）
workflow.add_node("deepagent_executor", deepagent_executor)

# 生成节点（仅用于权限拒绝等兜底场景）
workflow.add_node("generate", generate)
workflow.add_node("expand_question", expand_question)

# 权限校验工具节点
permission_retrieve = ToolNode(permission_mcp_tools)
workflow.add_node("permission_retrieve", permission_retrieve)

# =========================================================================
# 边定义
# =========================================================================

workflow.add_edge(START, "build_contextual_question")
workflow.add_edge("build_contextual_question", "permission_agent")

# 权限校验流程
workflow.add_conditional_edges(
    "permission_agent",
    route_after_permission_agent,
    {
        "tools": "permission_retrieve",
        "chat_mode_init": "chat_mode_init",
        "generate": "generate",
    },
)
workflow.add_edge("permission_retrieve", "permission_eval")
workflow.add_conditional_edges(
    "permission_eval",
    route_after_permission_eval,
    {
        "chat_mode_init": "chat_mode_init",
        "generate": "generate",
        "permission_interrupt": "permission_interrupt",
    },
)
workflow.add_conditional_edges(
    "permission_interrupt",
    route_after_permission_interrupt,
    {
        "permission_agent": "permission_agent",
        "generate": "generate",
    },
)

# chat_mode_init → deepagent_executor（统一入口，不再按模式分叉）
workflow.add_edge("chat_mode_init", "deepagent_executor")

# DeepAgent 执行流程
workflow.add_edge("deepagent_executor", "expand_question")

# 生成流程（权限拒绝等场景的兜底）
workflow.add_edge("generate", "expand_question")
workflow.add_edge("expand_question", END)

# 编译图
checkpointer = get_checkpointer()
graph = workflow.compile(checkpointer=checkpointer)
```

### 4.2 节点变化汇总

| 节点                        | 处理方式                                   | 说明                                                                               |
| --------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------- |
| `build_contextual_question` | **保留**                                   | 上下文问题构建仍需要                                                               |
| `fetch_time_context`        | **移除** → 转为 `get_latest_time_tool`     | 不再每次都执行，由 Skill 按需调用                                                  |
| `parse_region_expansion`    | **移除** → 转为 `parse_region_tool`        | 不再每次都执行，由 Skill 按需调用                                                  |
| `permission_agent`          | **保留**                                   | 权限校验仍是前置步骤                                                               |
| `permission_eval`           | **保留**                                   | 权限评估                                                                           |
| `permission_interrupt`      | **保留**                                   | 登录中断                                                                           |
| `chat_mode_init`            | **保留**                                   | 仍需读取 `Configuration.chat_mode` 并写入 state，`deepagent_executor` 据此选择实例 |
| `unified_intent_match`      | **移除** → 转为 `intent_match_tool`        | 语义路由从图节点变为 DeepAgent 可调用的工具                                        |
| `parallel_tool_executor`    | **移除**                                   | DeepAgent 自行管理工具调用                                                         |
| `knowledge_agent`           | **移除** → 转为 `knowledge_retriever_tool` | 封装为独立工具，由 Skill/Agent 按需调用                                            |
| `knowledge_retrieve`        | **移除**                                   | 合并入 `knowledge_retriever_tool`                                                  |
| `grade_documents`           | **移除**                                   | 文档相关性判断由 DeepAgent 自行决策                                                |
| `rewrite`                   | **移除**                                   | knowledge 分支整体移除，DeepAgent 可自行优化检索查询                               |
| `planner`                   | **移除**                                   | DeepAgent 内置 plan 能力                                                           |
| `executor`                  | **移除**                                   | 同上                                                                               |
| `replanner`                 | **移除**                                   | 同上                                                                               |
| `expert_subagent`           | **移除** → 合并为 `deepagent_executor`     | 统一入口                                                                           |
| `generate`                  | **保留，职责变化**                         | 仅处理权限拒绝等兜底场景，DeepAgent 正常流程不经过此节点                           |
| `expand_question`           | **保留**                                   | 追问生成仍需要                                                                     |

### 4.3 路由函数变化

| 路由函数                           | 处理方式                                          |
| ---------------------------------- | ------------------------------------------------- |
| `route_after_permission_agent`     | 保留，目标含 `chat_mode_init`                     |
| `route_after_permission_eval`      | 保留，目标含 `chat_mode_init`                     |
| `route_after_permission_interrupt` | 保留不变                                          |
| `route_after_chat_mode_init`       | **简化**：不再分叉，统一指向 `deepagent_executor` |
| `route_after_unified_intent_match` | **移除**                                          |
| `route_after_replanner`            | **移除**                                          |

---

## 五、核心实现：deepagent_executor 节点

### 5.1 节点设计

```python
async def deepagent_executor(
    state: AgentState, config: RunnableConfig
) -> Dict[str, Any]:
    """DeepAgent 统一执行节点.

    替代原来的 expert_subagent + unified_intent_match +
    parallel_tool_executor + planner/executor/replanner。

    根据 chat_mode 选择对应的 DeepAgent 实例：
    - fast: 硅基流动 DeepSeek-V3 + skills_fast/ 目录
    - expert: 火山引擎 DeepSeek-V3 + skills_expert/ 目录
    """
    chat_mode = Configuration.from_runnable_config(config).chat_mode
    question = _extract_question(state)

    if chat_mode == "expert":
        wrapper = _get_expert_wrapper()
    else:
        wrapper = _get_fast_wrapper()

    return await wrapper.run(question, state, config)
```

### 5.2 双 DeepAgent 实例管理

```python
_fast_wrapper: Optional[DeepAgentWrapper] = None
_expert_wrapper: Optional[DeepAgentWrapper] = None

SKILLS_FAST_DIR = str(Path(__file__).parent / "skills_fast")
SKILLS_EXPERT_DIR = str(Path(__file__).parent / "skills_expert")

def _get_fast_wrapper() -> DeepAgentWrapper:
    """获取 Fast 模式 DeepAgent（延迟初始化）."""
    global _fast_wrapper
    if _fast_wrapper is None:
        _fast_wrapper = DeepAgentWrapper(
            model=MODEL_CHAT_DEEPSEEK_V3,          # 硅基流动 DeepSeek-V3
            skills=[SKILLS_FAST_DIR],
            memory=[str(Path(SKILLS_FAST_DIR) / "AGENTS.md")],
            tools=_build_deepagent_tools(chat_mode="fast"),
        )
    return _fast_wrapper


def _get_expert_wrapper() -> DeepAgentWrapper:
    """获取 Expert 模式 DeepAgent（延迟初始化）."""
    global _expert_wrapper
    if _expert_wrapper is None:
        _expert_wrapper = DeepAgentWrapper(
            model=MODEL_CHAT_DOUBAO,                # 火山引擎 DeepSeek-V3
            skills=[SKILLS_EXPERT_DIR],
            memory=[str(Path(SKILLS_EXPERT_DIR) / "AGENTS.md")],
            tools=_build_deepagent_tools(chat_mode="expert"),
        )
    return _expert_wrapper
```

**两个实例指向不同的 Skills 目录**，各自有独立的 AGENTS.md 和 Skill 内容：

- Fast 模式：`skills_fast/` 目录，简洁流程 + 快速输出
- Expert 模式：`skills_expert/` 目录，深度流程 + 详尽输出

### 5.3 DeepAgent 工具注册

两种模式工具列表不同（Expert 模式包含额外工具），通过 `chat_mode` 参数控制：

```python
def _build_deepagent_tools(chat_mode: str = "fast") -> List[BaseTool]:
    """构建 DeepAgent 可用的工具列表.

    Args:
        chat_mode: "fast" 或 "expert"，Expert 模式包含额外工具
    """
    tools = []

    # 1. 语义路由工具（精准匹配15个模板）
    tools.append(intent_match_tool)

    # 2. 辅助工具
    tools.append(parse_region_tool)
    tools.append(get_latest_time_tool)

    # 3. MCP 业务工具（从 ipp_mcp_tools 获取）
    tools.extend(ipp_mcp_tools)

    # 4. Text2SQL 工具（封装 Vanna，保留 template_name 参数）
    tools.append(text2sql_tool)

    # 5. 知识检索工具（封装 knowledge_agent + knowledge_retrieve + grade_documents）
    tools.append(knowledge_retriever_tool)

    # 6. 网页搜索工具
    tools.append(global_websearch_tool)

    # 7. Expert 模式额外工具
    if chat_mode == "expert":
        tools.extend(datacenter_mcp_tools)  # 网格数据、火点等辅助工具

    return tools
```

### 5.4 DeepAgentWrapper 适配

当前 `DeepAgentWrapper` 仅服务于 Expert 模式，改造为通用执行器：

```python
class DeepAgentWrapper:
    """DeepAgent 统一包装器.

    改造要点：
    - 同时服务 Fast 和 Expert 模式
    - 通过构造函数传入不同的 model、skills 目录和 memory
    - 工具列表按模式配置
    - 保留流式输出能力
    """

    def __init__(
        self,
        model: str | BaseChatModel,
        skills: List[str],
        memory: List[str],
        tools: Sequence[BaseTool],
    ) -> None:
        self._model = model
        self._skills = skills
        self._memory = memory
        self._tools = tools
        self._agent: Optional[CompiledStateGraph] = None

    async def run(
        self,
        question: str,
        state: Dict[str, Any],
        config: RunnableConfig,
    ) -> Dict[str, Any]:
        """执行 DeepAgent（替代原 run_expert_subagent）.

        核心流程与当前 run_expert_subagent 一致：
        1. 获取/创建 agent（延迟初始化）
        2. astream 流式执行
        3. 逐 token 推送
        4. 提取 chart_data
        5. 返回最终结果
        """
        ...
```

### 5.5 模型配置

| 模式   | 模型                   | 接入方式                     | temperature |
| ------ | ---------------------- | ---------------------------- | ----------- |
| Fast   | DeepSeek-V3 (硅基流动) | `ChatOpenAI` via SiliconFlow | 0.1         |
| Expert | DeepSeek-V3 (火山引擎) | `ChatOpenAI` via Volcano ARK | 0.2         |

两种模式使用相同的基座模型（DeepSeek-V3），但接入不同的平台。Fast 模式使用硅基流动的 API，Expert 模式使用火山引擎的 API。当前没有额外的模型资源，后续如果需要可以：

- 在 `Configuration` 中增加 `model` 配置项
- 或为 Fast 模式引入更轻量的模型（如 Qwen3-30B）以降低成本

---

## 六、State 精简

### 6.1 AgentState 变化

```python
class AgentState(TypedDict, total=False):
    """主工作流状态 - 重构后."""

    # === 保留字段 ===
    messages: Annotated[Sequence[BaseMessage], add_messages]
    contextual_question: Optional[str]
    time_context: Optional[Dict[str, Any]]       # 保留，可能仍被其他节点引用
    expanded_questions: List[str]
    permission_success: Optional[bool]
    permission_message: Optional[str]
    permission_type: Optional[str]
    login_required: Optional[bool]
    logged_in_user_id: Optional[str]

    # === 新增字段 ===
    llm_data: Optional[str]                        # DeepAgent 生成的文本结果

    # === 保留但角色变化的字段 ===
    chat_mode: Optional[str]                       # "fast" | "expert"，由 chat_mode_init 写入，deepagent_executor 据此选择实例

    # === 移除字段 ===
    # intent_type          — 不再需要，DeepAgent 内部处理
    # intent_name          — 同上
    # template_name        — 不再需要，Skill 内部管理
    # mcp_tool_name        — 同上
    # remote_template      — 同上
    # text2sql_result      — 同上
    # display_type         — 由 Skill / DeepAgent 在输出时决定
    # rewrite_count        — knowledge 分支移除
    # continuous_conversation — 未使用
    # region_expansion     — 转为 Tool，不再写入 State
    # planner_state        — planner 分支移除
    # execution_results    — 同上
    # planner_retry_count  — 同上
    # planner_complete     — 同上
    # final_answer         — 未使用
    # chat_mode            — 保留，deepagent_executor 据此选择实例
    # expert_chart_data_list — chart_data 由 DeepAgent 直接推送
```

---

## 七、提示词迁移计划

### 7.1 prompts.py 中的提示词处理

| 提示词函数                                    | 处理方式                                                                  | 迁移目标                                      |
| --------------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------------- |
| `contextual_question_system_prompt`           | 保留在 prompts.py                                                         | build_contextual_question 仍在使用            |
| `contextual_question_user_prompt`             | 保留在 prompts.py                                                         | 同上                                          |
| `permission_agent_system_prompt`              | 保留在 prompts.py                                                         | 权限节点仍在使用                              |
| `build_time_hint_prompt`                      | **废弃**                                                                  | 由 Skill 通过 `get_latest_time_tool` 自行获取 |
| `build_region_hint_prompt`                    | **废弃**                                                                  | 由 Skill 通过 `parse_region_tool` 自行获取    |
| `region_extraction_prompt`                    | **迁入** `parse_region_tool`                                              | 工具内部使用                                  |
| `planner_prompt`                              | **废弃**                                                                  | DeepAgent 内置 plan                           |
| `replanner_prompt`                            | **废弃**                                                                  | 同上                                          |
| `complexity_classifier_prompt`                | **废弃**                                                                  | DeepAgent 自行判断复杂度                      |
| `knowledge_agent_prompt`                      | **迁入** `knowledge_retriever_tool` + `skills_*/knowledge-query/SKILL.md` | 工具描述 + Skill 内定义                       |
| `mcp_agent_prompt`                            | **迁入** 各 MCP Skill 的 SKILL.md                                         | 分散到对应 Skill                              |
| `text2sql_agent_prompt`                       | **迁入** 各 TEXT2SQL Skill 的 SKILL.md                                    | 分散到对应 Skill                              |
| `template_generate_system_prompt`             | **废弃**                                                                  | 远程模板逻辑由 Skill 处理                     |
| `generic_generate_system_prompt`              | **废弃**                                                                  | DeepAgent 直接输出                            |
| `mcp_generate_system_prompt`                  | **废弃**                                                                  | 同上                                          |
| `mcp_generate_fallback_system_prompt`         | **废弃**                                                                  | 同上                                          |
| `text2sql_with_remote_template_system_prompt` | **废弃**                                                                  | 同上                                          |
| `build_format_guidelines`                     | **迁入** AGENTS.md                                                        | 作为通用输出规范的一部分                      |
| `expand_question_prompt`                      | 保留在 prompts.py                                                         | expand_question 节点仍在使用                  |
| `knowledge_retrieval_route_prompt`            | **迁入** `knowledge-query/SKILL.md`                                       | Skill 内定义                                  |
| `document_grading_prompt`                     | **评估是否保留**                                                          | DeepAgent 可能不需要显式文档评分              |
| `rewrite_query_prompt`                        | **废弃**                                                                  | knowledge 分支移除                            |

### 7.2 远程模板内容与迁移

当前 MCP 远程模板（`ipp_mcp_client.get_prompt()`）用于 TEXT2SQL 和 MCP 类型的 prompt 增强。以下为完整的远程模板内容，需分别整合到 `skills_fast/` 和 `skills_expert/` 对应的 SKILL.md 中：

- Fast 版本：保留核心数据规则和输出格式，去掉深度分析要求
- Expert 版本：保留完整内容，可补充额外分析要求

> **注意**：以下有 2 个模板（`oneIndexManyCityLine`、`primaryPollutantProportionAnaly`）不在提供的远程模板文件中，需另行获取。

---

#### 7.2.1 MCP 类型模板（来源：StatisticCityV5McpPrompts.java）

##### broadcast-hour

模板名：`broadcastHour`，目标：`broadcast-hour/SKILL.md`

```
【数据说明】
工具返回 小时数据 和 全天数据。
小时数据 中 name 为具体时间点（如"01:00"），
若 name 为"日均浓度"，该记录不是时间点，禁止参与任何时段、极值、峰值或排序判断。

【污染等级说明】
1-优、2-良、3-轻度污染、4-中度污染、5-重度污染、6-严重污染。
```

##### integrated-index-ratio

模板名：`integratedIndexRatio`，目标：`integrated-index-ratio/SKILL.md`

```
【核心任务】
基于 llm_data 中的 tableData 数组生成空气质量综合指数占比分析文本：
1）若 tableData 仅包含 1 个城市，输出该城市 1 个完整分析段落；
2）若包含多个城市，按 orderNum 从小到大排序，
   为每个城市分别生成独立分析段落；
3）每个城市的分析必须完全基于该城市自身数据，互不影响。

【核心计算与排序规则（必须严格执行）】
对每一个城市，必须按以下流程处理，禁止跳步：
1）将 pm25、pm10、no2、o38h、co、so2 的字符串数值转换为数值；
2）综合指数 integratedIndex 为各污染物数值之和；
3）逐一计算污染物占比：
   占比 = (该污染物数值 ÷ 综合指数) × 100，结果保留 1 位小数；
4）仅以"占比数值大小"作为唯一依据，
   对所有污染物进行从高到低排序；
5）后续所有"第一大贡献源 / 第二大贡献源 / 第三大贡献源"等描述，
   必须严格来自该排序结果，禁止参考示例、常识或经验进行调整。

【数据有效性规则】
- 若某污染物数值为空、为 0、为 null 或无法解析，
  表述为"暂无有效数据"，不参与占比计算与排序；
- 有效污染物不足 3 项时，仅分析实际存在的数据项；
- 禁止补充、假设或推断任何数据中未提供的内容。
```

##### composite-index-six-factor

模板名：`compositeIndexSixFactorRatio`，目标：`composite-index-six-factor/SKILL.md`

```
【核心计算与排序规则（必须严格执行）】
1) 为你提供的pm25、pm10、no2、o38h、co、so2数据,不是浓度值,是分指数;integratedIndex为综合指数
2）将 pm25、pm10、no2、o38h、co、so2 的字符串数值转换为数值；
3）综合指数 integratedIndex 为各污染物分指数之和；
4）逐一计算污染物占比：
   占比 = (该污染物分指数 ÷ 综合指数) × 100，结果保留 1 位小数；

【数据有效性规则】
- 若某污染物分指数为空、为 0、为 null 或无法解析，
  表述为"暂无有效数据"，不参与占比计算与排序；
- 有效污染物不足 3 项时，仅分析实际存在的数据项；
- 禁止补充、假设或推断任何数据中未提供的内容。

【输出参考】
x月1日-x月x日，
x市综合指数为5.07，PM2.5占比为25.4%，PM10占比为38.7%，NO2占比为10.8%，SO2占比为2.6%，CO-95per占比为3.9%，O3-8h占比为18.5%。
```

##### city-factor-rankings

模板名：`cityFactorRankings`，目标：`city-factor-rankings/SKILL.md`

```
【数据解析（必须严格执行）】
time: 时间,可直接使用
cityCount: 行政区划总数
integratedIndex: 综合指数
integratedIndexRanking: 综合指数排名
integratedIndexRatio: 综合指数同比变化率
integratedIndexRatioRanking 综合指数同比变化率排名
pm25: PM2.5浓度
pm25Ranking: PM2.5浓度排名
pm25Ratio: PM2.5浓度同比变化率
pm25RatioRanking: PM2.5浓度同比变化率排名
pm10: PM10浓度
pm10Ranking: PM10浓度排名
pm10Ratio: PM10浓度同比变化率
pm10RatioRanking: PM10浓度同比变化率排名
goodDays: 优良天天数
goodDaysRatio: 优良天数同比
goodDaysRanking: 优良天数同比排名
【输出参考】
x月 1 日-x 月 x 日，
x 市/县综合指数为 4.916，同比下降4.7%，在x省/市n市/县中排名第 10，PM2.5浓度为 58 微克/立方米，同比下降 14.7%，排名第 5，综合得分为 7.5，排名第 9。
```

##### city-calculations

模板名：`city_calculations`，目标：`city-calculations/SKILL.md`

```
【数据解析（必须严格执行）】
你要根据提供的数据,分析用户指定的城市的某个污染因子的等级情况。
如果countValue小于remaining,则达成目标比较简单,如果countValue大于remaining,说明存在困难;
如果remaining显示'无法完成',则确定今日无法达标.
name:行政区划名称
value:当前小时的浓度值
countValue:累计浓度值
countLevel:累计浓度值等级
remaining:剩余控制值
【各污染因子对应等级的最大值对应参考】
pm25:优35,良75,轻度污染115,中度污染150,重度污染250
pm10:优50,良150,轻度污染250,中度污染350,重度污染420
so2:优50,良150,轻度污染475,中度污染800,重度污染1600
no2:优40,良80,轻度污染180,中度污染280,重度污染565
o3:优100,良160,轻度污染215,中度污染265,重度污染800
co:优2,良4,轻度污染24,中度污染36,重度污染48
aqi:优50,良100,轻度污染150,中度污染200,重度污染300
【输出参考】
用户问题:今日河南省有多少个城市臭氧保良
回答:今日截止20时，O3-8h累计浓度≤160微克/立方米的城市有3个，分别是洛阳市、新乡市、焦作市。
```

##### city-compliance-prediction

模板名：`city_compliance_prediction`，目标：`city-compliance-prediction/SKILL.md`

```
【数据解析（必须严格执行）】
你要根据提供的数据,分析用户指定的城市的某个污染因子的等级情况。
如果countValue小于remaining,则达成目标比较简单,如果countValue大于remaining,说明存在困难;
如果remaining显示'无法完成',则确定今日无法达标.
name:行政区划名称
value:当前小时的浓度值
countValue:累计浓度值
countLevel:累计浓度值等级
remaining:剩余控制值
【各污染因子对应等级的最大值对应参考】
pm25:优35,良75,轻度污染115,中度污染150,重度污染250
pm10:优50,良150,轻度污染250,中度污染350,重度污染420
so2:优50,良150,轻度污染475,中度污染800,重度污染1600
no2:优40,良80,轻度污染180,中度污染280,重度污染565
o3:优100,良160,轻度污染215,中度污染265,重度污染800
co:优2,良4,轻度污染24,中度污染36,重度污染48
aqi:优50,良100,轻度污染150,中度污染200,重度污染300
```

---

#### 7.2.2 TEXT2SQL 类型模板（来源：Text2sqlPrompts.java）

##### yesterday-single-city-aqi

模板名：`text2sql_yesterday_single_index_aqi_route`，目标：`yesterday-single-city-aqi/SKILL.md`

```
你的任务是以【自然、客观、通顺】的方式，回答单一城市昨日空气质量情况。

【表达要求】
1. 以完整陈述句输出，不使用生硬的罗列语气。
2. 只陈述事实，不进行跨城市对比。
3. 数值单位需完整、准确。
4. 不要出现任何占位符说明文字。

【参考表达示例】
X月X日，X市空气质量为X级，AQI为X，首要污染物为X。
当日综合指数为X，PM2.5浓度为X微克/立方米，PM10为X微克/立方米，
NO₂为X微克/立方米，SO₂为X微克/立方米，CO为X毫克/立方米，
O₃-8h为X微克/立方米。
```

##### today-single-city-aqi

模板名：`text2sql_today_single_city_aqi_route`，目标：`today-single-city-aqi/SKILL.md`

```
你的任务是根据城市数量，分别回答今日空气质量情况。

【通用要求】
1. 表达应接近新闻播报或环保简报风格。
2. 不强制进行城市优劣判断，除非差异明显。
3. 禁止出现"整体更好但主要指标更差"这类矛盾表述。
4. 比较城市日空气质量高低，用综合指数排序,综合指数越低，空气质量越好。

【单城市示例】
截至今日X时，X市空气质量为X级，AQI为X，首要污染物为X。
综合指数为X，PM2.5为X微克/立方米，PM10为X微克/立方米，
NO₂为X微克/立方米，O₃-8h为X微克/立方米。

【多城市示例】
截至今日X时，X市空气质量为X级，AQI为X；
Y市空气质量为X级，AQI为X。

从主要污染指标来看，X市PM2.5和PM10水平均低于Y市，
整体空气质量表现相对较好。
```

##### air-quality-trends

模板名：`text2sql_air_quality_trends_route`，目标：`air-quality-trends/SKILL.md`

```
你的任务是总结指定时间段内单一城市空气质量变化趋势。

【表达要求】
1. 以"趋势解读"为核心，而非单纯罗列数据。
2. 同比变化需明确"改善或下降"。
3. 如果指定月份,如 2024年 12月,或得的数据中可能显示数据来自 2024年 12月 1 日,这代表整月数据,而不是单一日期数据。

【参考示例】
X月1日至X月X日，X市综合指数为X，同比下降X%，空气质量有所改善。
其中，PM2.5平均浓度为X微克/立方米，同比下降X%；
PM10为X微克/立方米，同比下降X%。
本月优良天数为X天，较去年同期增加X天。
```

##### monthly-yearly-city-comparison

模板名：`text2sql_current_month_which_is_better_route`，目标：`monthly-yearly-city-comparison/SKILL.md`

```
你的任务是基于综合指数和主要污染物，对城市空气质量进行判断。

【要求】
1. 判断需有依据，不强行比较。
2. 若差异不明显，应明确说明。
3. 比较空气质量高低，用综合指数排序,综合指数越低，空气质量越好。
4. 提供的是月累计或者年数据,因此不能出现类似"根据某日数据"的描述

【示例】
X月X日至X月X日，X市综合指数为X，Y市为X。
从整体污染水平来看，X市空气质量略优于Y市。
```

##### province-city-rank

模板名：`text2sql_province_city_rank_route`，目标：`province-city-rank/SKILL.md`

```
你的任务是需要回答单一城市在省内的污染物浓度排名情况。
【表达要求】
1. 仅陈述客观排名结果，不进行评价性解读。
2. 明确说明污染物类型（如 PM2.5）。
3. 表达应简洁、正式，避免口语化总结。

【参考表达示例】
截至今日X时，X市PM2.5浓度为X微克/立方米，
在X省纳入统计的X个地市中排名第X位。
```

##### station-worst

模板名：`text2sql_station_worst_route`，目标：`station-worst/SKILL.md`

```
S-10 表示国控站，S-11 表示省控站。

你需要指出昨日空气质量表现最差的监测站点。

【表达要求】
1. 以城市整体情况开头，再引出具体站点。
2. "最差"需有明确指标依据（综合指数）。
3. 表达自然、清晰，不使用生硬的比较句式。

【参考表达示例】
昨日X市综合指数为X。
在各监测站点中，X站点（X控）综合指数为X，
为当日全市监测站点中最高，空气质量相对最差。
```

##### station-pollution-level

模板名：`text2sql_station_pollution_level_route`，目标：`station-pollution-level/SKILL.md`

```
你需要说明城市整体空气质量情况，并指出出现轻度污染的具体站点。

【表达要求】
1. 先描述城市整体空气质量，再说明站点情况。
2. 不扩展除"轻度污染"以外的等级说明。
3. 不引入其他站点或不必要对比。

【参考表达示例】
昨日X市空气质量等级为X级，AQI为X，首要污染物为X。
其中，X站点空气质量达到轻度污染水平，
AQI为X，首要污染物为X。
```

---

#### 7.2.3 缺失模板

以下 2 个路由在 `intent_config.py` 中定义，但提供的远程模板文件中不包含对应模板，需另行获取：

| 路由名                            | 目标 Skill                              | 说明             |
| --------------------------------- | --------------------------------------- | ---------------- |
| `oneIndexManyCityLine`            | `one-index-many-city-line/SKILL.md`     | 城市指标对比模板 |
| `primaryPollutantProportionAnaly` | `primary-pollutant-proportion/SKILL.md` | 主污染源占比模板 |

---

#### 7.2.4 迁移完成后的效果

- MCP 远程模板不再被代码调用（`_fetch_remote_template_async` 和 `ipp_mcp_client.get_prompt()` 相关逻辑可移除）
- 所有提示词由 Skills 目录统一管理
- 后续修改提示词只需编辑 Skills 文件，无需连接 MCP 服务端

---

## 八、数据流变化

### 8.1 当前 Fast 模式数据流

```
用户问题
  → build_contextual_question (添加历史上下文)
  → fetch_time_context (获取最新时间，写入 time_context)
  → parse_region_expansion (解析地址，写入 region_expansion)
  → permission_agent (权限校验)
  → unified_intent_match (语义路由，写入 intent_type/intent_name/template_name/mcp_tool_name)
  → parallel_tool_executor (并行执行工具，写入 llm_data/text2sql_result/remote_template)
  → generate (基于 llm_data/text2sql_result + 远程模板 生成回答，流式推送)
  → expand_question
```

### 8.2 重构后数据流

```
用户问题
  → build_contextual_question (添加历史上下文)
  → permission_agent (权限校验)
  → deepagent_executor:
      DeepAgent 内部流程:
        1. 可选: 调用 intent_match_tool → 获取 skill_name
        2. 可选: 调用 get_latest_time_tool → 获取时间上下文
        3. 可选: 调用 parse_region_tool → 解析地址
        4. 如果命中模板: 读取 SKILL.md → 按流程调用工具
        5. 如果未命中: 自主决策调用哪些工具
        6. 生成分析文本，流式推送
        7. 提取 chart_data，推送
  → expand_question
```

**关键区别：**

- `fetch_time_context` 和 `parse_region_expansion` 不再每次都执行
- 语义路由不再是图节点，而是 DeepAgent 可选调用的工具
- 工具调用流程由 Skill 定义或 DeepAgent 自主决策，而非硬编码的图分支
- `generate` 节点不再是必经节点（DeepAgent 直接流式输出）

### 8.3 chart_data 处理

当前：

- Expert 模式：DeepAgent 从 ToolMessage 中提取 chart_data，通过 writer 推送
- Fast 模式：`parallel_tool_executor` 通过 `extract_mcp_data` 提取 chart_data，通过 writer 推送

重构后统一为：

- DeepAgent 从 ToolMessage 中提取 chart_data，通过 writer 推送（与当前 Expert 模式一致）

---

## 九、风险与缓解

### 9.1 高风险

| 风险                      | 影响                                                 | 缓解措施                                                                                                                            |
| ------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Fast 模式响应延迟增加** | 简单查询从 ~2-3s 增加到 ~4-5s                        | 1. Skill 中明确工具调用流程，减少 LLM 决策开销<br>2. `intent_match_tool` 命中后直接走确定流程<br>3. 监控延迟指标，必要时优化        |
| **意图匹配精度变化**      | 语义路由作为 Tool 调用后，DeepAgent 可能不遵循其结果 | 1. 在 AGENTS.md 中强调"命中模板时必须按 SKILL.md 执行"<br>2. 在 intent_match_tool 的返回值中增加强制性提示<br>3. 对比测试验证匹配率 |
| **TEXT2SQL 结果质量**     | 当前 text2sql 流程经过精心调优，迁移后可能退化       | 1. 保留 Vanna 的 template_name 参数<br>2. 逐个 Skill 验证 SQL 生成质量<br>3. 如质量下降，回退到方案 A 的模板增强                    |

### 9.2 中等风险

| 风险                  | 影响                                                | 缓解措施                                                                                                                                              |
| --------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Skills 编写工作量** | 15 个 Skill 需要编写详细的 SKILL.md                 | 1. 先迁移 3 个已有 Skill（验证流程）<br>2. MCP 类型的 8 个 Skill 可参考现有 broadcast-hour 模板<br>3. TEXT2SQL 类型的 7 个 Skill 结构简单，可批量生成 |
| **流式输出兼容性**    | 前端需要适配新的输出格式                            | DeepAgent 流式输出格式与当前 Expert 模式一致，前端无需改动                                                                                            |
| **工具调用一致性**    | intent_match_tool 的路由名与 Skill 目录名映射需同步 | 编写校验脚本，确保 ROUTE_TO_SKILL 映射与 Skills 目录一致                                                                                              |

### 9.3 低风险

| 风险             | 影响                                    | 缓解措施                                                     |
| ---------------- | --------------------------------------- | ------------------------------------------------------------ |
| **模型成本变化** | 所有查询使用 DeepSeek-V3                | 当前 Expert 模式已使用此模型，成本可控                       |
| **知识检索质量** | 移除 grade_documents 后检索精度可能下降 | DeepAgent 可自行判断文档相关性；如质量下降可恢复文档评分逻辑 |

---

## 十、实施步骤

### Step 1: 基础工具改造

**目标**：将 intent_config.py、parse_region_expansion、fetch_time_context、knowledge_agent 改造为 Tool

- [ ] 将 `match_intent()` 封装为 `intent_match_tool`，增加 `ROUTE_TO_SKILL` 映射
- [ ] 将 `parse_region_expansion` 核心逻辑提取为 `parse_region_tool`
- [ ] 将 `fetch_time_context` 核心逻辑提取为 `get_latest_time_tool`
- [ ] 将 `knowledge_agent` + `knowledge_retrieve` + `grade_documents` 封装为 `knowledge_retriever_tool`
- [ ] 编写工具单元测试
- [ ] 验证四个 Tool 可独立调用

**验证标准**：四个 Tool 可独立调用并返回正确结果

### Step 2: Skills 补全

**目标**：为两套 Skills 目录编写完整的 SKILL.md，整合远程模板内容

- [ ] 创建 `skills_fast/` 和 `skills_expert/` 两套目录结构
- [ ] 编写 Fast 模式 AGENTS.md（`skills_fast/AGENTS.md`）
- [ ] 编写 Expert 模式 AGENTS.md（`skills_expert/AGENTS.md`）
- [ ] 迁移并完善现有 3 个 Skill 到两套目录（broadcast-hour、integrated-index-ratio、air-quality-analysis）
- [ ] **整合远程模板**：将 7.2 节中的远程模板内容分别整合到 `skills_fast/` 和 `skills_expert/` 对应的 SKILL.md 中（Fast 版本精简，Expert 版本完整）
- [ ] 获取缺失的 2 个远程模板（`oneIndexManyCityLine`、`primaryPollutantProportionAnaly`），整合到对应 Skill
- [ ] 编写 8 个 MCP 类型 Skill（两套目录各一份）
- [ ] 编写 7 个 TEXT2SQL 类型 Skill（两套目录各一份）
- [ ] 编写 1 个 Knowledge 类型 Skill（两套目录各一份）
- [ ] 编写 Skills 校验脚本（检查两套目录的必需字段、工具名一致性、目录对称性）

**验证标准**：校验脚本通过，所有 Skill 字段完整，远程模板内容已全部同步

### Step 3: DeepAgent 统一节点

**目标**：实现 `deepagent_executor` 节点，替代所有业务节点

- [ ] 改造 `DeepAgentWrapper`：注册所有工具（含四个新工具 + text2sql_tool）
- [ ] 实现双 DeepAgent 实例管理（Fast: 硅基流动 + skills_fast/, Expert: 火山引擎 + skills_expert/）
- [ ] 实现 `deepagent_executor` 节点函数（根据 chat_mode 选择实例）
- [ ] 精简 `AgentState`（移除废弃字段，保留 chat_mode）
- [ ] 重构 `graph.py`（新图结构，保留 chat_mode_init 节点）
- [ ] 调整 `generate` 节点（仅处理权限拒绝等兜底场景）
- [ ] 简化 `route_after_chat_mode_init`（统一指向 deepagent_executor）

**验证标准**：新图可编译运行，DeepAgent 可流式输出

### Step 4: 集成测试与回归

**目标**：确保所有功能正确，性能可接受

- [ ] 15 个模板问题的功能测试
- [ ] 未匹配意图的自主决策测试
- [ ] 追问场景测试
- [ ] 权限校验流程测试
- [ ] 性能对比测试（记录响应时间对比）
- [ ] chart_data 推送测试
- [ ] 边界测试（数据缺失、工具调用失败等）

**验证标准**：

- 功能测试 100% 通过
- 简单查询响应时间 < 5s（目标 < 4s）
- chart_data 正确推送

### Step 5: 清理与优化

**目标**：移除废弃代码，优化性能

- [x] 移除 `prompts.py` 中废弃的提示词函数
- [x] 移除 `nodes.py` 中废弃的节点函数
- [ ] 移除 `intent_config.py` 中的 `IntentConfig` 类（保留 Route 定义和匹配逻辑）
- [ ] 移除远程模板依赖（如果 Skill 已完全覆盖）
- [x] 清理 `AgentState` 废弃字段
- [ ] 性能优化（如有必要）

**验证标准**：无废弃代码残留，测试全部通过

---

## 十、待办事项

### 10.1 模型配置统一

**问题**：`tools.py` 和 `nodes.py` 中存在重复的模型定义，需要统一管理。

**待办**：

- [ ] 将模型定义统一到单一位置（建议 `nodes.py` 或新建 `models.py`）
- [ ] `tools.py` 从统一位置导入模型，避免重复定义
- [ ] 确保 Fast/Expert 模式的模型配置一致

**当前状态**：

- `nodes.py`: 定义 `MODEL_CHAT_DEEPSEEK_V3`, `MODEL_CHAT_DOUBAO`, `MODEL_CHAT_QWEN3_30B` 等
- `tools.py`: 定义 `MODEL_CHAT_DEEPSEEK_V3`, `MODEL_CHAT_QWEN2_14B`（用于工具内部调用）

### 10.2 Expert 模式配置验证

**问题**：`deepagent_integration.py` 中 Expert 模式的配置需要与 `nodes.py` 保持一致。

**待办**：

- [ ] 验证 `config.ARK_API_KEY` 和 `config.ARK_BASE_URL` 配置正确
- [ ] 确认 Expert 模式模型为 `deepseek-v3-2-251201`
- [ ] 确认 temperature 为 `0.2`，timeout 为 `300`

**当前配置**：

```python
# Expert 模式（火山方舟）
model = ChatOpenAI(
    api_key=SecretStr(config.ARK_API_KEY),
    base_url=config.ARK_BASE_URL,
    model="deepseek-v3-2-251201",
    temperature=0.2,
    timeout=300,
    max_retries=1,
)
```

### 10.3 MCP 工具导入路径

**问题**：`ipp_mcp_tools` 和 `datacenter_mcp_tools` 需要异步初始化，目前定义在 `nodes.py`。

**待办**：

- [ ] 考虑将 MCP 工具初始化逻辑抽取到独立模块
- [ ] 或保持现状，在 `deepagent_integration.py` 中从 `agent.nodes` 导入

**当前方案**：

```python
# deepagent_integration.py
from agent.nodes import datacenter_mcp_tools, ipp_mcp_tools
```

### 10.4 tools.py 结构

**目标结构**：

```
tools.py
├── WebSearchTool              # 基础工具类（被 nodes.py 使用）
├── intent_match_tool          # DeepAgent 工具：语义路由匹配
├── parse_region_tool          # DeepAgent 工具：行政区划解析
├── get_latest_time_tool       # DeepAgent 工具：获取最新时间
├── knowledge_retriever_tool   # DeepAgent 工具：知识检索
└── ROUTE_TO_SKILL             # 路由名 → Skill 目录名映射表
```

**已删除的废弃类**：

- `UnifiedTools`
- `MCPTool`
- `Text2SQLTool`
- `KnowledgeTool`

### 10.5 文件精简（已完成）

**问题**：`deepagent_tools.py` 和 `tools.py` 同时存在，功能重叠，需要合并。

**待办**：

- [x] 将 `deepagent_tools.py` 中的四个工具合并到 `tools.py`
- [x] 删除 `deepagent_tools.py` 文件
- [x] 更新 `deepagent_integration.py` 的导入路径

**最终结果**：

- `src/agent/deepagent_tools.py` 已删除
- 所有 DeepAgent 工具统一放在 `tools.py` 中

### 10.6 最终代码清理（待完成）

**目标**：所有重构工作完成后，全面检查并删除无用代码。

**待办**：

- [ ] 检查 `nodes.py` 中是否有未使用的函数和变量
- [ ] 检查 `tools.py` 中是否有未使用的导入和定义
- [ ] 检查 `prompts.py` 中是否有未使用的提示词函数
- [ ] 检查 `config.py` 中是否有未使用的配置项
- [ ] 删除所有 `# TODO`、`# FIXME`、`# XXX` 标记的已解决问题
- [ ] 运行 `ruff check` 和类型检查，确保无警告

**原则**：

- 不保留无用代码
- 每个函数、类、变量都应有明确用途
- 注释应解释"为什么"而非"是什么"

---

## 十一、回滚方案

如果重构后出现严重问题，回滚策略：

1. **Step 1-2 阶段**：新旧代码共存，工具和 Skills 目录不影响现有流程
2. **Step 3 阶段**：保留旧图结构的代码（注释或 feature flag），可快速切换回旧流程
3. **Step 4 阶段**：如果性能不达标，可回退到"语义路由前置 + DeepAgent 仅执行"的混合模式

---

## 十二、未来展望

### 12.1 Skills 后台管理

当 Skills 完全接管提示词和工具配置后，可以构建后台管理系统：

- 可视化编辑 SKILL.md（YAML frontmatter + Markdown 编辑器）
- 工具注册与绑定管理
- Skills 版本管理与发布
- A/B 测试（同一 Skill 不同版本的对比）
- 热加载（无需重启服务即可更新 Skill）

### 12.2 模型路由优化

- 简单查询自动切换轻量模型（Qwen3-30B）
- 复杂分析使用强模型（DeepSeek-V3）
- 基于历史数据训练路由分类器

### 12.3 Skills 质量监控

- 意图匹配命中率统计
- 各 Skill 调用成功率
- 输出质量评估（基于用户反馈）
