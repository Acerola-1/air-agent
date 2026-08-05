# Deep Agent 架构设计开发文档

> 注意：本文档记录的是旧版 Main Agent + 业务 SubAgent 设计，已不再作为当前目标架构。
> 当前 6 个业务模块已经迁移为 6 个顶层 LangGraph graph，调用方直接选择 `base`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 或 `intelligent-tracing`。
> 现行调用约定见 `docs/top-level-business-graphs.md`。

## 文档信息

- **项目名称**：空气质量查询系统 Agent 架构
- **版本**：v2.0
- **创建日期**：2025-01-09
- **最后更新**：2025-01-11
- **技术框架**：Deep Agents (基于 LangGraph)
- **状态**：设计中

---

## 一、文档概述

### 1.1 目的

本文档定义空气质量查询系统的完整 Agent 架构设计，包括：

- SubAgent 模块化划分与模式路由机制
- Skill 三层文件体系（总纲 + 快速 + 专家）
- 7 类基础问题 Skill 的详细定义
- Middleware 横切关注点
- 扩展性设计

### 1.2 架构决策背景

| 决策              | 选择                        | 原因                                                                                           |
| ----------------- | --------------------------- | ---------------------------------------------------------------------------------------------- |
| SubAgent 划分维度 | **按模块**                  | 模块是业务隔离的自然边界，4 个 SubAgent 数量可控                                               |
| 模式差异化方式    | **Skill 内部分流**          | fast/expert 区别在 prompt 深度，不在 model；用 SKILL_FAST.md / SKILL_EXPERT.md 在 Skill 层解决 |
| 统一 Model        | **gpt-4o**                  | 中档 model 覆盖两种模式需求，避免 SubAgent 数量膨胀                                            |
| 模式信号传递      | **task() description 注入** | `[mode:XXX]` 标记是唯一运行时信息通道                                                          |

### 1.3 核心设计原则

| 原则           | 说明                                                            | 实现方式                                        |
| -------------- | --------------------------------------------------------------- | ----------------------------------------------- |
| **模块隔离**   | 每个 SubAgent 只处理自己模块的业务域                            | 4 个 SubAgent，各自绑定模块专属 skills + tools  |
| **模式内聚**   | fast/expert 差异在 Skill 层解决，不拆 SubAgent                  | SKILL.md 总纲 + SKILL_FAST.md / SKILL_EXPERT.md |
| **统一模型**   | 所有 SubAgent 使用同一 model                                    | deepseek，成本与能力的平衡点                    |
| **运行时路由** | 模式信号通过 description 注入，由 SubAgent 自行选择 prompt 变体 | `[mode:fast]` / `[mode:expert]` 标记            |
| **渐进式增强** | 快速模式为基础，专家模式在此基础上补强                          | 共享数据获取层，差异在输出深度                  |
| **监控友好**   | 每个 SubAgent 独立追踪性能和成本                                | 统一日志格式 + 监控埋点                         |

---

## 二、系统架构总览

### 2.1 架构分层

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend Layer                            │
│  模块选择：基础问题 | 数据分析 | 智能交互 | 知识问答             │
│  模式选择：快速 | 专家                                           │
│  参数传递：{module, mode, user_id, query}                       │
└─────────────────────────────────────────────────────────────────┘
                            ↓  API层格式化: [module:XXX][mode:XXX] query
┌─────────────────────────────────────────────────────────────────┐
│              Main Agent (Intelligent Router)                     │
│  - 解析 [module:XXX] 标记                                       │
│  - 路由到对应模块的 SubAgent                                     │
│  - 将 [mode:XXX] 标记透传到 description                        │
│  - State: {user_id, module, mode}                               │
└─────────────────────────────────────────────────────────────────┘
                            ↓
         ┌──────────────────┼──────────────────┐──────────────────┐
         ↓                  ↓                  ↓                  ↓
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ basic-agent  │   │analysis-agent│   │interactive-  │   │ knowledge-  │
│              │   │              │   │    agent      │   │    agent     │
│ Skills: 7种  │   │ Skills: N种  │   │ Skills: M种  │   │ Skills: K种  │
│ Model: gpt-4o│   │ Model: gpt-4o│   │ Model: gpt-4o│   │ Model: gpt-4o│
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
         ↓                  ↓                  ↓                  ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Middleware Stack (共享)                        │
│  1. ContentFilterMiddleware (内容审核)                          │
│  2. LoggingMiddleware (日志记录)                                │
│  3. SkillsMiddleware (技能加载)                                 │
│  4. FilesystemMiddleware (文件操作)                             │
│  5. SubAgentMiddleware (子 agent 调度)                          │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 模块化架构（按模块划分）

```
┌─────────────────────────────────────────────────────────────────┐
│                     模块化 SubAgent 架构                         │
│                                                                 │
│  SubAgent 数量：4（按模块）                                      │
│  统一 Model：gpt-4o                                             │
│  模式区分：Skill 层内部（SKILL_FAST.md / SKILL_EXPERT.md）      │
│                                                                 │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┐     │
│  │ basic-agent │analysis-agent│interactive- │knowledge-  │     │
│  │             │             │   agent     │   agent     │     │
│  │ Skills:7种  │ Skills:N种  │ Skills:M种  │ Skills:K种  │     │
│  │ Tools:查询  │ Tools:分析  │ Tools:搜索  │ Tools:知识库│     │
│  │ sources:    │ sources:    │ sources:    │ sources:    │     │
│  │ ./skills/   │ ./skills/   │ ./skills/   │ ./skills/   │     │
│  │  basic/     │  analysis/  │  interactive│  knowledge/ │     │
│  └─────────────┴─────────────┴─────────────┴─────────────┘     │
│                                                                 │
│  加新模块 → 只加 1 个 SubAgent + 1 个 skills 目录              │
│  加新模式 → 只改 Skill 内容，不改 SubAgent                      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 信息流全链路

```
1. Frontend → {module: "basic", mode: "fast", user_id: "xxx", query: "北京空气质量怎么样"}
2. API层格式化 → "[module:basic][mode:fast] 北京空气质量怎么样"
3. Main Agent (LLM) → 解析标记 → 选 basic-agent
4. task(subagent_type="basic-agent", description="[mode:fast] 北京空气质量怎么样")
5. basic-agent 启动 → SkillsMiddleware 加载 ./skills/basic/ 下所有 SKILL.md
6. system prompt 注入 skills 元数据列表（name + description + path）
7. agent 识别查询匹配 air-quality-basic-query skill → 读取 SKILL.md
8. SKILL.md 总纲 → 看到模式指引 → 读取 SKILL_FAST.md
9. SKILL_FAST.md → 执行精简查询流程 → 调用工具 → 返回结果
10. Main Agent → 转述结果给用户
```

---

## 三、核心组件设计

### 3.1 组件职责矩阵

| 组件           | 职责                  | 数量            | 配置方式    |
| -------------- | --------------------- | --------------- | ----------- |
| **Main Agent** | 路由、协调            | 1               | 代码实现    |
| **SubAgent**   | 业务处理              | 4（按模块）     | Python 配置 |
| **Middleware** | 横切关注点            | 5-10            | 代码实现    |
| **Skill**      | 工作流定义 + 模式分流 | 每模块 N 种     | 文件系统    |
| **Tool**       | 能力封装              | 共享 + 模块专属 | 代码实现    |

### 3.2 Main Agent 设计

#### 3.2.1 核心职责

```python
class MainAgent:
    """主 Agent：负责路由和协调"""

    职责:
        1. 接收 API层格式化的用户消息（含 [module:XXX][mode:XXX] 标记）
        2. 解析 module 标记，路由到对应模块的 SubAgent
        3. 将 mode 标记透传到 task() 的 description 参数
        4. 协调多个 SubAgent 协作（如果需要）
        5. 转述 SubAgent 结果给用户
        6. 记录日志和监控数据
```

#### 3.2.2 系统提示模板

```python
MAIN_AGENT_PROMPT = """你是空气质量查询助手的主调度器。

## 用户输入格式
用户消息格式固定为：[module:XXX][mode:XXX] 实际问题

## 强制路由规则（必须严格遵守）

从用户消息中提取 module 标记，选择对应的 subagent_type：

| module 标记 | subagent_type |
|-------------|---------------|
| basic       | basic-agent   |
| analysis    | analysis-agent |
| interactive | interactive-agent |
| knowledge   | knowledge-agent |

将 [mode:XXX] 标记和实际问题一起传入 task 工具的 description 参数。

## 注意事项
1. 严格按照 module 标记选择 SubAgent，不要自行判断
2. 不要忽略标记自行回答用户问题
3. 不要修改用户选择的 mode
4. 如果用户未指定 module，根据问题内容智能判断
5. 如果用户未指定 mode，默认使用 [mode:fast]
6. 你必须调用 task 工具，不允许直接回答
"""
```

#### 3.2.3 状态管理

```python
class AgentState(TypedDict):
    """主 Agent 状态"""

    # 用户信息
    user_id: str
    permissions: list[str]

    # 路由参数
    module: Literal["basic", "analysis", "interactive", "knowledge"]
    mode: Literal["fast", "expert"]

    # 对话历史
    messages: list[BaseMessage]

    # 执行结果
    current_subagent: str | None
    subagent_results: dict[str, Any]

    # 监控数据
    trace_id: str
    start_time: float
    model_calls: int
    total_tokens: int
```

### 3.3 SubAgent 架构设计

#### 3.3.1 SubAgent 定义规范

```python
from typing import TypedDict, NotRequired, Literal
from langchain_core.language_models import BaseChatModel
from langchain.tools import BaseTool
from deepagents.middleware.permissions import FilesystemPermission
from deepagents.middleware import AgentMiddleware

class SubAgentSpec(TypedDict):
    """SubAgent 配置规范"""

    # 必填字段
    name: str          # 格式: {module}-agent
    description: str   # 中文描述，供 Main Agent 跃路决策
    system_prompt: str  # 系统提示（含模式路由指令）
    model: str | BaseChatModel  # 统一使用 gpt-4o
    tools: list[BaseTool]       # 本模块专属 + 共享工具
    skills: list[str]           # 本模块 skills 目录路径

    # 可选字段
    middleware: list[AgentMiddleware]
    permissions: list[FilesystemPermission]
```

#### 3.3.2 SubAgent 命名规范

```
格式: {module}-agent

当前 SubAgent 列表:
- basic-agent       (基础问题模块)
- analysis-agent    (数据分析模块)
- interactive-agent (智能交互模块)
- knowledge-agent   (知识问答模块)
```

#### 3.3.3 SubAgent 配置

```python
# config/subagents.py

# 统一模型配置
UNIFIED_MODEL = "openai:gpt-4o"  # 中档模型，覆盖 fast/expert 两种模式

# ─── 基础问题模块 ───
BASIC_AGENT = {
    "name": "basic-agent",
    "description": "空气质量基础问题查询助手，支持实时查询、排名评估、达标可行性、对比构成、站点极值、区域对标、趋势分析",
    "system_prompt": BASIC_AGENT_PROMPT,  # 见下方
    "model": UNIFIED_MODEL,
    "skills": ["./skills/basic/"],
    "tools": [
        # 共享工具
        get_current_time,
        standardize_city_name,
        check_query_permission,
        # 数据获取工具
        query_city_daily_air_data,
        query_city_realtime_air_data,
        query_multiple_cities_air_data,
        query_weather_data,
        # 排名工具
        query_province_ranking,
        query_assessment_ranking,
        # 达标可行性工具
        query_compliance_control,
        query_good_day_feasibility,
        # 对比/构成工具
        query_index_composition,
        query_year_comparison,
        # 站点工具
        query_worst_station,
        query_polluted_stations,
        # 区域对标工具
        query_regional_comparison,
        query_regional_statistics,
        # 趋势工具
        query_trend_analysis,
    ],
}

# ─── 数据分析模块 ───
ANALYSIS_AGENT = {
    "name": "analysis-agent",
    "description": "空气质量数据分析助手，支持统计分析、相关性分析等深度数据洞察",
    "system_prompt": ANALYSIS_AGENT_PROMPT,
    "model": UNIFIED_MODEL,
    "skills": ["./skills/analysis/"],
    "tools": [
        get_current_time,
        standardize_city_name,
        check_query_permission,
        query_air_quality_data,
        statistical_analysis,
        correlation_analysis,
    ],
}

# ─── 智能交互模块 ───
INTERACTIVE_AGENT = {
    "name": "interactive-agent",
    "description": "空气质量智能交互助手，支持多轮问答、上下文感知的深度对话",
    "system_prompt": INTERACTIVE_AGENT_PROMPT,
    "model": UNIFIED_MODEL,
    "skills": ["./skills/interactive/"],
    "tools": [
        get_current_time,
        standardize_city_name,
        check_query_permission,
        query_air_quality_data,
        web_search,
    ],
}

# ─── 知识问答模块 ───
KNOWLEDGE_AGENT = {
    "name": "knowledge-agent",
    "description": "空气质量知识问答助手，支持政策问答、技术标准解读",
    "system_prompt": KNOWLEDGE_AGENT_PROMPT,
    "model": UNIFIED_MODEL,
    "skills": ["./skills/knowledge/"],
    "tools": [
        get_current_time,
        standardize_city_name,
        check_query_permission,
        knowledge_base_search,
    ],
}

# SubAgent 列表
ALL_SUBAGENTS = [BASIC_AGENT, ANALYSIS_AGENT, INTERACTIVE_AGENT, KNOWLEDGE_AGENT]
```

#### 3.3.4 SubAgent System Prompt 设计（模式路由指令）

每个模块 SubAgent 的 system_prompt 都包含相同的模式路由框架：

```python
# 模式路由指令（所有 SubAgent 共享的部分）
MODE_ROUTING_INSTRUCTION = """
## 模式路由规则

任务描述中会包含 [mode:XXX] 标记，你必须根据标记选择对应的执行方式：

- **[mode:fast]** → 选择对应 Skill 的 SKILL_FAST.md，执行精简快速流程
- **[mode:expert]** → 选择对应 Skill 的 SKILL_EXPERT.md，执行深度专家流程

## 执行步骤

1. 从任务描述中识别 [mode:XXX] 标记
2. 从 system prompt 的 Skills 列表中识别匹配当前查询的 Skill
3. 读取该 Skill 的 SKILL.md（总纲）
4. 根据 mode 标记，读取 SKILL_FAST.md 或 SKILL_EXPERT.md
5. 按所选模式的具体指引执行
6. 返回结果

## 权限校验（所有模式通用）

执行任何数据查询前，必须先完成权限校验：
1. 调用 get_current_time() 获取当前时间
2. 调用 standardize_city_name() 规范化城市名
3. 调用 check_query_permission(user_id, city, station, time) 校验权限
4. 权限通过后才可调用数据查询工具
"""

# 基础问题模块的完整 system prompt
BASIC_AGENT_PROMPT = f"""你是空气质量基础问题查询助手。

{MODE_ROUTING_INSTRUCTION}

## 模块特有指引

你负责处理以下 7 类基础问题：
1. 基础空气质量数据查询（air-quality-basic-query）
2. 排名/考核查询（ranking-assessment）
3. 达标可行性研判（compliance-feasibility）
4. 同比/占比分析（comparison-composition）
5. 站点极值/污染查询（station-extreme）
6. 区域对标/统计（regional-benchmark）
7. 趋势分析（trend-analysis）

根据用户查询内容，选择最匹配的 Skill 执行。
"""
```

---

## 四、Skill 系统设计

### 4.1 Skill 三层文件体系

每个 Skill 由三个文件组成，职责如下：

| 文件                | 职责                                    | 加载方式                            | 内容                                                                   |
| ------------------- | --------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------- |
| **SKILL.md**        | 总纲：元数据 + 通用流程 + 模式路由指引  | SkillsMiddleware **自动发现和注入** | YAML frontmatter（name, description, allowed-tools）+ 模式选择指引段落 |
| **SKILL_FAST.md**   | 快速模式：精简查询流程 + 标准化输出模板 | SubAgent **运行时主动读取**         | 角色定位 + 输入数据范围 + 核心总结要求 + 回复模板                      |
| **SKILL_EXPERT.md** | 专家模式：深度分析流程 + 专业输出模板   | SubAgent **运行时主动读取**         | 角色定位 + 深度补强模块（气象影响、成因分析、管控建议）+ 术语规范      |

**关键机制**：

- SkillsMiddleware 只扫描 `SKILL.md`，不扫描 `SKILL_FAST.md` 和 `SKILL_EXPERT.md`
- `SKILL.md` 的 name + description 注入到 system prompt 的 Skills 列表中
- SubAgent 根据 `[mode:XXX]` 标记，用文件读取工具主动读取对应的 SKILL_FAST.md 或 SKILL_EXPERT.md
- 模式分流在 Skill 层解决，不需要拆 SubAgent

### 4.2 SKILL.md 标准格式

#### 4.2.1 YAML Frontmatter 必填字段

```yaml
---
name: skill-name # Skill 名称（小写字母+连字符，1-64字符）
description: | # Skill 描述（触发关键词+典型场景，≤1024字符）
    简要描述 Skill 功能

    触发关键词：
    - "关键词1"、"关键词2"

    典型场景：
    - "问题示例1"
    - "问题示例2"

version: 1.0.0 # Skill 版本号
allowed-tools: # 工具列表（从 tools 目录声明）
    - tool_name_1
    - tool_name_2

metadata: # 元数据（分类信息）
    category: "category_name" # 分类名称
    question_ids: ["1", "2", "4"] # 覆盖问题编号
    requires_weather: true/false # 是否需要气象数据
    prompt_fast: "./SKILL_FAST.md" # 快速模式提示词路径
    prompt_expert: "./SKILL_EXPERT.md" # 专家模式提示词路径
---
```

#### 4.2.2 SKILL.md Markdown 正文结构

SKILL.md 的正文（YAML frontmatter 之后）包含两个固定段落：

```markdown
# Skill 名称

## 模式选择指引

本技能包含两种执行模式，请根据任务中的 `[mode:XXX]` 标记选择：

- **快速模式** (`[mode:fast]`)：请阅读 `SKILL_FAST.md`，执行精简查询流程
- **专家模式** (`[mode:expert]`)：请阅读 `SKILL_EXPERT.md`，执行深度分析流程

## 通用说明

所有模式下，你都必须先执行权限校验（见 system prompt 的权限校验规则）。

数据获取遵循刚性流程：

1. 优先从 MCP 接口获取数据
2. 若失败，自动重试 1 次（间隔 2 秒）
3. 若仍失败，降级到 text2sql
4. 最终仍无数据,真诚回复用户,无数据可回复

请先读取对应的 SKILL_FAST.md 或 SKILL_EXPERT.md 获取完整指引。
```

### 4.3 目录结构

```
skills/
├── basic/                              # 基础问题模块
│   ├── air-quality-basic-query/           # 1. 实时查询
│   │   ├── SKILL.md                    # 总纲（自动注入）
│   │   ├── SKILL_FAST.md              # 快速模式（运行时读取）
│   │   ├── SKILL_EXPERT.md            # 专家模式（运行时读取）
│   │   └── scripts/                   # 可选辅助脚本
│   │       └── query.py
│   ├── ranking-assessment/             # 2. 排名评估
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   └── SKILL_EXPERT.md
│   ├── compliance-feasibility/         # 3. 达标可行性
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   └── SKILL_EXPERT.md
│   ├── comparison-composition/         # 4. 对比构成
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   └── SKILL_EXPERT.md
│   ├── station-extreme/                # 5. 站点极值
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   └── SKILL_EXPERT.md
│   ├── regional-benchmark/             # 6. 区域对标
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   └── SKILL_EXPERT.md
│   └── trend-analysis/                 # 7. 趋势分析
│       ├── SKILL.md
│       ├── SKILL_FAST.md
│       ├── SKILL_EXPERT.md
│
├── analysis/                           # 数据分析模块（待设计）
│   ├── statistical-analysis/
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   ├── SKILL_EXPERT.md
│   └── correlation-analysis/
│       ├── SKILL.md
│       ├── SKILL_FAST.md
│       ├── SKILL_EXPERT.md
│
├── interactive/                        # 智能交互模块（待设计）
│   ├── multi-turn-qa/
│   │   ├── SKILL.md
│   │   ├── SKILL_FAST.md
│   │   ├── SKILL_EXPERT.md
│   └── context-aware/
│       ├── SKILL.md
│       ├── SKILL_FAST.md
│       ├── SKILL_EXPERT.md
│
└── knowledge/                          # 知识问答模块（待设计）
    ├── policy-qa/
    │   ├── SKILL.md
    │   ├── SKILL_FAST.md
    │   ├── SKILL_EXPERT.md
    └── technical-qa/
        ├── SKILL.md
        ├── SKILL_FAST.md
        ├── SKILL_EXPERT.md
```

### 4.4 Skill 加载机制

```python
# SkillsMiddleware 加载流程（编译期，只处理 SKILL.md）

# 1. SubAgent 配置中指定 skills 目录
BASIC_AGENT = {
    "skills": ["./skills/basic/"],  # 只绑定本模块目录
}

# 2. SkillsMiddleware 启动时：
#    a. ls("./skills/basic/") → 发现 7 个子目录
#    b. 对每个子目录，检查 SKILL.md 是否存在
#    c. 解析 SKILL.md 的 YAML frontmatter → 提取 name, description, allowed-tools
#    d. 注入到 system prompt：
#       "Available Skills:
#        - air-quality-basic-query: 查询城市空气质量实况数据...
#        - ranking-assessment: 排名/考核查询...
#        ...
#        Read `./skills/basic/air-quality-basic-query/SKILL.md` for full instructions"

# 3. SubAgent 运行时（收到 [mode:fast] 标记后）：
#    a. 从 skills 列表识别匹配的 skill
#    b. 读取 SKILL.md → 看到模式指引段落
#    c. 根据 mode 标记，读取 SKILL_FAST.md 或 SKILL_EXPERT.md
#    d. 按所选模式的具体指引执行
```

---

## 五、基础问题模块 Skill 详细设计（7 类）

### 5.1 Skill 分类总览

| Skill 名称                 | 覆盖问题 | 核心特征                     | 典型问题示例                               | 推荐工具                                                                                                                  | 气象需求 |
| -------------------------- | -------- | ---------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | -------- |
| **air-quality-basic-query**   | 1,2,4    | 单日/实时、单城/多城基础数据对比 | "昨日洛阳市空气质量怎么样？"               | `query_city_daily_air_data`<br>`query_city_realtime_air_data`<br>`query_multiple_cities_air_data`<br>`query_weather_data` | 必须     |
| **ranking-assessment**     | 6,8      | 单因子区域排名、县域考核     | "今日洛阳市PM2.5在河南省排名第几？"        | `query_province_ranking`<br>`query_assessment_ranking`                                                                    | 不必须   |
| **compliance-feasibility** | 10,11    | 保良/规避重污染天研判        | "今天洛阳市能保良吗？"                     | `query_compliance_control`<br>`query_good_day_feasibility`<br>`query_weather_data`                                        | 必须     |
| **comparison-composition** | 3,7      | 月度/年度同比、六因子占比    | "本月洛阳市空气质量如何，较去年有改善没？" | `query_index_composition`<br>`query_year_comparison`                                                                      | 可选     |
| **station-extreme**        | 12,13    | 城市最差站点、污染站点查询   | "昨日洛阳市空气质量最差的站点是哪个？"     | `query_worst_station`<br>`query_polluted_stations`                                                                        | 可选     |
| **regional-benchmark**     | 5,9      | 多城月度对标、区域达标统计   | "本月洛阳市和平顶山市哪个城市空气质量好？" | `query_regional_comparison`<br>`query_regional_statistics`                                                                | 可选     |
| **trend-analysis**         | 14       | 多日污染物/综指变化趋势      | "过去一周洛阳市PM2.5浓度变化趋势如何？"    | `query_trend_analysis`<br>`query_weather_data`                                                                            | 必须     |

### 5.2 问题编号说明

| 编号 | 问题                                                       |
| ---- | ---------------------------------------------------------- |
| 1    | 昨日洛阳市空气质量怎么样？（单一城市日回答）               |
| 2    | 今天洛阳市空气质量怎么样？（单一城市日累计回答）           |
| 3    | 本月洛阳市空气质量如何，较去年有改善没？（单一城市月同比） |
| 4    | 今日洛阳市和平顶山市空气质量如何？（多城市日回答）         |
| 5    | 本月洛阳市和平顶山市哪个城市空气质量好？（多城市月对标）   |
| 6    | 今日洛阳市PM2.5在河南省排名第几？（单因子省内排名）        |
| 7    | 本月洛阳市综合指数六因子的占比如何？（综指占比分析）       |
| 8    | 这个月虞城县考核咋样，第几名？（县域考核排名）             |
| 9    | 今日河南省有多少个城市臭氧保良？（区域达标统计）           |
| 10   | 今天洛阳市能保良吗？（保良可行性研判）                     |
| 11   | 今天洛阳市能避免重污染天吗？（重污染规避研判）             |
| 12   | 昨日洛阳市空气质量最差的站点的是哪个站点？（站点极值查询） |
| 13   | 昨日洛阳市哪个站点轻度污染了？（站点污染查询）             |
| 14   | 过去一周洛阳市PM2.5浓度变化趋势如何？（趋势分析）          |

### 5.3 SKILL.md 完整示例：air-quality-basic-query

```yaml
---
name: air-quality-basic-query
description: |
  查询城市基础空气质量数据（昨日/今日/指定日）。

  触发关键词：
  - "空气质量怎么样"、"空气质量如何"
  - "昨天"、"今日"、"指定日" + 城市 + 空气质量
  - "实时查询"、"当前AQI"

  单城市场景：
  - "昨日洛阳市空气质量怎么样？"（问题1）
  - "今天洛阳市空气质量怎么样？"（问题2）

  多城市场景：
  - "今日洛阳市和平顶山市空气质量如何？"（问题4）

version: 1.0.0
allowed-tools:
  - query_city_daily_air_data
  - query_city_realtime_air_data
  - query_multiple_cities_air_data
  - query_weather_data
  - get_current_time

metadata:
  category: "basic-query"
  question_ids: ["1", "2", "4"]
  time_types: ["昨日", "今天", "指定日", "实时"]
  city_modes: ["single", "multiple"]
  requires_weather: true
  prompt_fast: "./SKILL_FAST.md"
  prompt_expert: "./SKILL_EXPERT.md"
---

# 基础空气质量数据查询

## 模式选择指引

本技能包含两种执行模式，请根据任务中的 `[mode:XXX]` 标记选择：

- **快速模式** (`[mode:fast]`)：请阅读 `SKILL_FAST.md`，执行精简查询流程
- **专家模式** (`[mode:expert]`)：请阅读 `SKILL_EXPERT.md`，执行深度分析流程

## 通用说明

所有模式下，你都必须先执行权限校验（见 system prompt 的权限校验规则）。

数据获取遵循刚性流程：
1. 优先从 MCP 接口获取数据
2. 若失败，自动重试 1 次（间隔 2 秒）
3. 若仍失败，降级到 text2sql
4. 若所有数据源均无结果，标记 source="none"

边缘场景处理优先级：场景3 > 场景1 > 场景2 > 场景4（详见边缘异常处理章节）。

请先读取对应的 SKILL_FAST.md 或 SKILL_EXPERT.md 获取完整指引。
```

### 5.4 其他 Skill 的 SKILL.md YAML Frontmatter

#### ranking-assessment

```yaml
---
name: ranking-assessment
description: |
    查询空气质量排名和考核数据。

    触发关键词：
    - "排名第几"、"排名"、"考核"
    - "PM2.5排名"、"臭氧排名"
    - "县域考核"

    典型场景：
    - "今日洛阳市PM2.5在河南省排名第几？"（问题6）
    - "这个月虞城县考核咋样，第几名？"（问题8）

version: 1.0.0
allowed-tools:
    - query_province_ranking
    - query_assessment_ranking
    - get_current_time

metadata:
    category: "ranking"
    question_ids: ["6", "8"]
    requires_weather: false
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

#### compliance-feasibility

```yaml
---
name: compliance-feasibility
description: |
    空气质量达标可行性研判（保良/规避重污染天）。

    触发关键词：
    - "能保良吗"、"保良"
    - "能避免重污染吗"、"规避重污染"
    - "达标可行性"、"研判"

    典型场景：
    - "今天洛阳市能保良吗？"（问题10）
    - "今天洛阳市能避免重污染天吗？"（问题11）

version: 1.0.0
allowed-tools:
    - query_compliance_control
    - query_good_day_feasibility
    - query_weather_data
    - get_current_time

metadata:
    category: "compliance"
    question_ids: ["10", "11"]
    requires_weather: true
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

#### comparison-composition

```yaml
---
name: comparison-composition
description: |
    空气质量同比对比和六因子占比分析。

    触发关键词：
    - "同比"、"较去年"、"改善没"
    - "占比"、"六因子"、"综合指数占比"
    - "月度对比"、"年度对比"

    典型场景：
    - "本月洛阳市空气质量如何，较去年有改善没？"（问题3）
    - "本月洛阳市综合指数六因子的占比如何？"（问题7）

version: 1.0.0
allowed-tools:
    - query_index_composition
    - query_year_comparison
    - get_current_time

metadata:
    category: "comparison"
    question_ids: ["3", "7"]
    requires_weather: false
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

#### station-extreme

```yaml
---
name: station-extreme
description: |
    查询城市最差站点和污染站点数据。

    触发关键词：
    - "最差站点"、"最差的站点"
    - "哪个站点污染"、"轻度污染站点"
    - "站点极值"

    典型场景：
    - "昨日洛阳市空气质量最差的站点的是哪个站点？"（问题12）
    - "昨日洛阳市哪个站点轻度污染了？"（问题13）

version: 1.0.0
allowed-tools:
    - query_worst_station
    - query_polluted_stations
    - get_current_time

metadata:
    category: "station"
    question_ids: ["12", "13"]
    requires_weather: false
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

#### regional-benchmark

```yaml
---
name: regional-benchmark
description: |
    多城市对标比较和区域达标统计。

    触发关键词：
    - "哪个城市好"、"对标"、"对比"
    - "有多少个城市保良"、"达标统计"
    - "区域统计"、"省达标"

    典型场景：
    - "本月洛阳市和平顶山市哪个城市空气质量好？"（问题5）
    - "今日河南省有多少个城市臭氧保良？"（问题9）

version: 1.0.0
allowed-tools:
    - query_regional_comparison
    - query_regional_statistics
    - get_current_time

metadata:
    category: "regional"
    question_ids: ["5", "9"]
    requires_weather: false
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

#### trend-analysis

```yaml
---
name: trend-analysis
description: |
    多日污染物浓度或综合指数变化趋势分析。

    触发关键词：
    - "趋势"、"变化趋势"
    - "过去一周"、"最近几天"
    - "PM2.5趋势"、"臭氧趋势"

    典型场景：
    - "过去一周洛阳市PM2.5浓度变化趋势如何？"（问题14）

version: 1.0.0
allowed-tools:
    - query_trend_analysis
    - query_weather_data
    - get_current_time

metadata:
    category: "trend"
    question_ids: ["14"]
    requires_weather: true
    prompt_fast: "./SKILL_FAST.md"
    prompt_expert: "./SKILL_EXPERT.md"
---
```

---

## 六、提示词设计规范

### 6.1 SKILL_FAST.md 设计规范

**设计目标**：

- 输出标准化数据呈现
- 核心结论前置（≤30字）
- 无深度分析、无管控建议
- 模板严格遵守

**核心内容结构**：

```markdown
# 角色定位

生态环境部门基础空气质量数据查询值守人员

# 合规依据

《环境空气质量标准》（GB 3095-2016）
《环境空气质量评价技术规范》（HJ 663-2026）

# 输入数据范围

Skill 层已获取的 fetched_data 对象：

- source: MCP / text2sql / none
- data: 空气质量监测数据
- weather: 气象数据（可选）
- metadata: 元数据

# 核心总结要求

1. 核心结论前置（≤30字）
2. 数据完整呈现（六因子+单位）
3. 气象影响简述（若有气象数据）
4. 无额外延伸分析

# 输出规范

固定输出结构：
【核心结论】→【核心指标明细】→【气象影响简述】

# 回复模板（严格遵守）

模板A：单城市日数据
{date}，{city}空气质量等级为{level}，AQI为{AQI}，首要污染物为{pollutant}...

模板B：今日实时数据
{date}截止{hour}时，{city}空气质量等级为{level}...

模板C：多城市对比
{date}截止{hour}时，{city1}空气质量...{city2}空气质量...
{better_city}空气质量整体优于{worse_city}...
```

### 6.2 SKILL_EXPERT.md 设计规范

**设计目标**：

- 专业深度分析
- 成因溯源 + 实操建议
- 衔接快速模式基础数据，不重复
- 气象影响强制分析（若有气象数据）

**核心内容结构**：

```markdown
# 角色定位

大气环保领域资深专家

# 衔接前置条件

快速模式已输出：核心数据结论（本模式在此基础上补强，不重复基础数据）

# 系统指令前置

接收 fetched_data 对象：

- source 为 none → "暂无相关数据，无法完成分析"
- weather 存在 → 强制嵌入气象影响分析
- weather 缺失 → 标注"本次分析未考虑气象条件"

# 核心补强模块（按需触发）

### 气象影响分析（强制模块）

气象条件整体[利于/不利于]污染物[扩散/生成]
其中[具体气象因子]是[污染物变化]的主要驱动因素

### 关键原因分析（≤5点）

围绕大气领域核心维度：

1. 污染源解析
2. 气象条件影响
3. 化学转化过程
4. 管控措施落实情况

### 应急管控措施（≤5点）

明确责任主体或技术路径

### 注意事项/局限性

明确数据、区域、技术等相关局限性

# 术语规范

使用大气环保领域国家标准术语，杜绝口语化表述
```

### 6.3 双模式执行流程

#### 6.3.1 快速模式执行流程

```
1. SubAgent 收到 [mode:fast] 标记
    ↓
2. 识别匹配 Skill → 读取 SKILL.md
    ↓
3. 根据指引读取 SKILL_FAST.md
    ↓
4. 权限校验（调用 check_query_permission 等工具）
    ↓
5. 数据获取（MCP > text2sql > none）
    ↓
6. 构建 fetched_data 对象
    ↓
7. 执行快速模式提示词流程
    ↓
8. 输出标准化数据呈现（核心结论前置）
    ↓
9. 返回结果给 Main Agent
```

#### 6.3.2 专家模式执行流程

```
1. SubAgent 收到 [mode:expert] 标记
    ↓
2. 识别匹配 Skill → 读取 SKILL.md
    ↓
3. 根据指引读取 SKILL_EXPERT.md
    ↓
4. 权限校验
    ↓
5. 数据获取（MCP > text2sql > none）
    ↓
6. 构建 fetched_data 对象
    ↓
7. 执行专家模式提示词流程
    ↓
8. 输出专业分析 + 实操建议（在快速模式基础上补强）
    ↓
9. 返回结果给 Main Agent
```

---

## 七、边缘异常处理

### 7.1 边缘场景分类（4类）

| 场景编号  | 场景名称       | 触发条件                                   | 输出内容示例                                                                                                                                            |
| --------- | -------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **场景1** | 数据缺失       | 数据缺失                                   | "暂无【洛阳市】【2026-04-09】【PM2.5】监测数据，无法完成查询。建议确认：① 城市名称是否准确；② 统计时段是否在监测覆盖范围；③ 目标因子是否为常规监测项。" |
| **场景2** | 跨区域无排名   | 查询"XX城市在XX大区排名"但无该区域排名数据 | "暂无【华东地区】【今日】【PM2.5】排名数据，可提供替代查询：① 该城市在本省的排名；② 华东地区核心城市PM2.5浓度对比。"                                    |
| **场景3** | 非规范城市名称 | 城市名称不在标准列表中（如"洛市"）         | "推测您查询的是'洛阳市'，今日截至16时洛阳市空气质量等级为良，AQI为74；若为其他城市（如洛宁县），请补充完整名称后重新查询。"                             |
| **场景4** | 氏象数据不完整 | 只有温度，无湿度、风速等                   | "气象数据不完整（缺失湿度、风速），分析可能不全面，建议补充完整气象数据后重新研判。"                                                                    |

### 7.2 处理优先级

```
优先级顺序：场景3 > 场景1 > 场景2 > 场景4

说明：
- 场景3（非规范城市名称）会导致后续所有查询失败，优先级最高
- 场景1（数据缺失）是核心查询失败，优先级次之
- 场景2（跨区域无排名）和场景4（气象不完整）影响范围较小

若同时触发多个场景，仅输出优先级最高的一个。
```

### 7.3 边缘处理实现

边缘场景由 SubAgent 在执行 Skill 流程时处理，不单独设 Middleware：

```python
# 边缘场景逻辑写在 SKILL_FAST.md / SKILL_EXPERT.md 的流程指引中
# SubAgent 按指引判断并输出对应处理结果

# 伪代码（供 Skill prompt 设计参考）
async def handle_edge_scenarios(query: str, fetched_data: dict) -> dict | None:
    """边缘场景处理"""

    # 场景 3: 非标准城市名（最高优先级）
    if not is_standard_city(query.city):
        matched_city = fuzzy_match_city(query.city)
        return {
            "type": "scenario_3",
            "message": f"推测您查询的是'{matched_city}'...",
            "suggestion": "请补充完整城市名称后重新查询"
        }

    # 场景 1: 数据缺失
    if fetched_data.get("source") == "none":
        return {
            "type": "scenario_1",
            "message": "暂无该城市空气质量数据",
            "suggestion": "请稍后重试或查询其他城市"
        }

    # 场景 2: 跨区域无排名数据
    if is_cross_region_query(query) and not has_ranking_data(query):
        return {
            "type": "scenario_2",
            "message": "跨区域查询暂无排名数据",
            "suggestion": "可查询各城市独立数据"
        }

    # 场景 4: 气象数据不完整
    if fetched_data.get("weather") and is_incomplete_weather(fetched_data["weather"]):
        return {
            "type": "scenario_4",
            "message": "气象数据不完整，部分分析可能受限",
            "warning": "湿度、风速等数据缺失"
        }

    return None  # 无边缘场景
```

##

---

###

```python

```

#####

```python

```

---

## 十一、项目目录结构

```
air-quality-agent/
├── config/                        # 配置文件
│   ├── subagents.py              # 4 个 SubAgent 配置
│   ├── models.py                 # 统一模型配置
│   ├── tools.py                  # 工具注册
│   └── settings.py               # 系统设置
│
├── agent/                         # Agent 核心
│   ├── main_agent.py             # 主 Agent 实现
│   └── state.py                  # 状态定义
│
├── middleware/                    # 中间件
│   ├── __init__.py
│   ├── content_filter.py         # 内容审核
│   ├── logging.py                # 日志记录
│   └── monitoring.py             # 监控埋点
│
├── skills/                        # 技能系统（三层文件体系）
│   ├── basic/                    # 基础问题模块（7 个 Skill）
│   │   ├── air-quality-basic-query/
│   │   │   ├── SKILL.md          # 总纲
│   │   │   ├── SKILL_FAST.md     # 快速模式
│   │   │   ├── SKILL_EXPERT.md   # 专家模式
│   │   │   └── scripts/
│   │   │       └── query.py
│   │   ├── ranking-assessment/
│   │   ├── compliance-feasibility/
│   │   ├── comparison-composition/
│   │   ├── station-extreme/
│   │   ├── regional-benchmark/
│   │   └── trend-analysis/
│   ├── analysis/                 # 数据分析模块（待设计）
│   ├── interactive/              # 智能交互模块（待设计）
│   └── knowledge/                # 知识问答模块（待设计）
│
├── tools/                         # 工具库
│   ├── __init__.py
│   ├── permission_tools.py       # 权限校验工具（get_current_time, standardize_city_name, check_query_permission）
│   ├── air_quality.py            # 空气质量数据工具
│   ├── weather.py                # 气象数据工具
│   ├── ranking.py                # 排名考核工具
│   ├── compliance.py             # 达标可行性工具
│   ├── comparison.py             # 对比构成工具
│   ├── station.py                # 站点极值工具
│   ├── regional.py               # 区域对标工具
│   ├── trend.py                  # 趋势分析工具
│   ├── text2sql.py               # SQL 生成工具
│   └── web_search.py             # 网络搜索工具
│
├── services/                      # 业务服务
│   ├── permission_service.py     # 权限服务
│   ├── content_filter_service.py # 内容审核服务
│   ├── data_service.py           # 数据服务
│   └── cache_service.py          # 缓存服务
│
├── api/                           # API 接口
│   ├── __init__.py
│   ├── query.py                  # 查询接口（格式化 [module:XXX][mode:XXX] 标记）
│   └── monitor.py                # 监控接口
│
├── tests/                         # 测试
│   ├── test_agents/              # Agent 测试
│   ├── test_middleware/          # 中间件测试
│   ├── test_skills/              # Skill 测试
│   └── test_tools/               # Tool 测试
│
├── docs/                          # 文档
│   ├── 架构设计开发文档.md       # 本文档（唯一架构文档）
│   └── API 文档.md               # API 文档
│
├── pyproject.toml                 # 项目配置
├── .env.example                   # 环境变量示例
└── README.md                      # 项目说明
```

---

## 十二、扩展指南

### 12.1 新增模块

新增模块只需 3 步，不影响现有 SubAgent：

```python
# 步骤1: 创建 skills 目录
# skills/new_module/
# ├── skill-1/
# │   ├── SKILL.md          # 总纲
# │   ├── SKILL_FAST.md     # 快速模式
# │   ├── SKILL_EXPERT.md   # 专家模式
# └── skill-2/

# 步骤2: 配置 1 个新 SubAgent
# config/subagents.py
NEW_MODULE_AGENT = {
    "name": "new-module-agent",
    "description": "新模块助手，...",
    "system_prompt": f"你是新模块助手。\n{MODE_ROUTING_INSTRUCTION}\n...",
    "model": UNIFIED_MODEL,        # 统一模型，无需改
    "skills": ["./skills/new_module/"],
    "tools": [new_tool_1, new_tool_2],
}

# 步骤3: 更新 Main Agent 路由表
# 在 MAIN_AGENT_PROMPT 的路由表中添加：
# | new_module | new-module-agent |
```

### 12.2 新增模式

新增模式**不改 SubAgent**，只改 Skill 内容：

```
步骤1: 在每个 Skill 目录下新增对应的 .md 文件
  skills/basic/air-quality-basic-query/
  ├── SKILL.md           # 总纲（更新模式选择指引段落）
  ├── SKILL_FAST.md      # 快速模式（已有）
  ├── SKILL_EXPERT.md    # 专家模式（已有）
  ├── SKILL_CREATIVE.md  # 新增：创意模式

步骤2: 更新每个 SKILL.md 的模式选择指引段落
  添加：
  - **创意模式** (`[mode:creative]`)：请阅读 `SKILL_CREATIVE.md`

步骤3: 更新 SubAgent system_prompt 的模式路由指令
  添加 [mode:creative] 的说明

步骤4: 更新前端模式选择 UI
  添加"创意模式"选项
```

### 12.3 切换统一模型

如果需要更换所有 SubAgent 的 model，只需改一处：

```python
# config/models.py
UNIFIED_MODEL = "openai:gpt-4o"  # 改为其他模型，如 "anthropic:claude-sonnet-4-5-20250929"
```

所有 4 个 SubAgent 自动生效，无需逐个修改。

### 12.4 新增 Middleware

```python
# middleware/custom_middleware.py
class CustomMiddleware(AgentMiddleware):
    """自定义中间件"""

    async def awrap_model_call(self, request, handler):
        # 前置处理
        response = await handler(request)
        # 后置处理
        return response

# 注册到 Main Agent
agent = create_deep_agent(
    model=settings.MAIN_MODEL,
    system_prompt=MAIN_AGENT_PROMPT,
    subagents=ALL_SUBAGENTS,
    middleware=[
        ContentFilterMiddleware(),
        CustomMiddleware(),
        LoggingMiddleware(),
    ],
)
```

---

## 十三、总结

### 13.1 核心架构特点

1. **模块化 SubAgent**：4 个 SubAgent 按业务模块划分，各自绑定专属 skills + tools，职责清晰
2. **Skill 三层文件体系**：SKILL.md 总纲自动注入 + SKILL_FAST.md / SKILL_EXPERT.md 运行时按需读取，模式分流在 Skill 层解决
3. **统一模型**：所有 SubAgent 使用 gpt-4o，成本与能力平衡，避免 SubAgent 膨胀
4. **运行时模式路由**：`[mode:XXX]` 标记通过 task() description 注入，SubAgent 自行选择 prompt 变体
5. **Tool-based 权限校验**：利用 LangGraph 的 tool calling 机制，LLM 自动提取参数和调用
6. **低扩张成本**：加模块只加 1 个 SubAgent，加模式只改 Skill 内容

### 13.2 关键约束

- **SubAgent model 是静态的**：编译期绑定，运行时不可切换——统一 model 避免了这个约束的影响
- **SkillsMiddleware 只扫描 SKILL.md**：SKILL_FAST.md / SKILL_EXPERT.md 由 SubAgent 运行时主动读取
- **task() description 是唯一运行时通道**：模式信号必须通过 `[mode:XXX]` 标记注入
- **Prompt 是关键**：SubAgent 的 system prompt + SKILL.md / SKILL_FAST.md / SKILL_EXPERT.md 的设计质量决定了系统行为

---

## 附录

### A. Skill 清单速查

| Skill 名称             | 覆盖问题 | 允许工具                                                                                                                  | 气象需求 | 提示词文件                       |
| ---------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------- |
| air-quality-basic-query   | 1,2,4    | `query_city_daily_air_data`<br>`query_city_realtime_air_data`<br>`query_multiple_cities_air_data`<br>`query_weather_data` | 必须     | SKILL_FAST.md<br>SKILL_EXPERT.md |
| ranking-assessment     | 6,8      | `query_province_ranking`<br>`query_assessment_ranking`                                                                    | 不必须   | SKILL_FAST.md<br>SKILL_EXPERT.md |
| compliance-feasibility | 10,11    | `query_compliance_control`<br>`query_good_day_feasibility`<br>`query_weather_data`                                        | 必须     | SKILL_FAST.md<br>SKILL_EXPERT.md |
| comparison-composition | 3,7      | `query_index_composition`<br>`query_year_comparison`                                                                      | 可选     | SKILL_FAST.md<br>SKILL_EXPERT.md |
| station-extreme        | 12,13    | `query_worst_station`<br>`query_polluted_stations`                                                                        | 可选     | SKILL_FAST.md<br>SKILL_EXPERT.md |
| regional-benchmark     | 5,9      | `query_regional_comparison`<br>`query_regional_statistics`                                                                | 可选     | SKILL_FAST.md<br>SKILL_EXPERT.md |
| trend-analysis         | 14       | `query_trend_analysis`<br>`query_weather_data`                                                                            | 必须     | SKILL_FAST.md<br>SKILL_EXPERT.md |

### B. 参考资料

- Deep Agents 官方文档：https://github.com/langchain-ai/deepagents
- LangGraph 文档：https://langchain-ai.github.io/langgraph/
- Agent Skills 规范：https://agentskills.io/
- 《环境空气质量标准》（GB 3095-2016）
- 《环境空气质量评价技术规范》（HJ 663-2026）
- 《城市环境空气质量排名技术规定》（环办大气〔2018〕19号）

# Deep Agents 架构设计总结

### 一、SubAgent 设计：按业务模块划分

|                 | 单实例 Deep Agents                 | 本方案                               |
| --------------- | ---------------------------------- | ------------------------------------ |
| **架构**        | 1 个通用 Agent 处理所有业务        | 4 个模块化 SubAgent，各管各的领域    |
| **Skills 绑定** | 全量加载，Agent 看到所有模块的技能 | 每个 SubAgent 只加载本模块的技能目录 |
| **Tools 绑定**  | 全量绑定，Agent 看到所有工具       | 每个 SubAgent 只绑定本模块的工具     |
| **职责边界**    | 无边界，所有问题混在一起           | 模块隔离，基础查询不会触发分析工具   |

**4 个 SubAgent**：

| SubAgent          | 负责模块    | Skills 数量 | 绑定工具范围                             |
| ----------------- | ----------- | ----------- | ---------------------------------------- |
| basic-agent       | 基础问题    | 7 种        | 查询、排名、达标、对比、站点、区域、趋势 |
| analysis-agent    | 数据分析    | 待定        | 待定                                     |
| interactive-agent | 智能分析    | 待定        | 待定                                     |
| knowledge-agent   | 研判报告... | 待定        | 待定                                     |

**核心改进**：

- **认知聚焦**：每个 SubAgent 只"看到"自己领域的技能和工具，不会被其他模块干扰，决策更准确
- **Token 节省**：System Prompt 只注入本模块的 Skill 元数据，不加载无关内容
- **独立演进**：各模块可独立增删 Skill、调整工具，互不影响
- **可观测性**：每个 SubAgent 独立追踪性能和成本，问题定位精准

---

### 二、Skill 设计：三层文件体系 + 模式内分流

|              | 单实例 Deep Agents              | 本方案                                                        |
| ------------ | ------------------------------- | ------------------------------------------------------------- |
| **文件结构** | 每个技能 1 个 SKILL.md          | 每个技能 3 个文件：SKILL.md + SKILL_FAST.md + SKILL_EXPERT.md |
| **模式处理** | 无模式区分，或拆成多个 SubAgent | 模式分流在 Skill 层解决，不增加 SubAgent                      |
| **加载方式** | 全量加载                        | SKILL.md 自动注入元数据；SKILL_FAST/EXPERT.md 运行时按需读取  |

**三层文件的职责**：

| 文件                | 职责                                   | 何时加载         | 谁加载            |
| ------------------- | -------------------------------------- | ---------------- | ----------------- |
| **SKILL.md**        | 总纲：元数据 + 通用流程 + 模式路由指引 | Agent 启动时自动 | SkillsMiddleware  |
| **SKILL_FAST.md**   | 快速模式：精简流程 + 标准化输出模板    | Agent 执行时按需 | SubAgent 自行读取 |
| **SKILL_EXPERT.md** | 专家模式：深度分析流程 + 专业输出模板  | Agent 执行时按需 | SubAgent 自行读取 |
