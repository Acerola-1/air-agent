# Basic-QA 并行 Tool Calls 设计

## 背景

basic-qa 模块的 Skill 执行流程中存在多个互不依赖的数据查询步骤，当前全部串行执行，效率低。例如 `air-quality-basic-query` Skill 中，实时数据、气象数据、预报数据三个查询互不依赖，但当前依次执行，每次 MCP 工具调用 RTT 约 1-3 秒，串行导致不必要的延迟累积。

### 根因分析

1. **模型未被指导并行调用工具**：DeepAgents 的 Harness Profile 中 `<use_parallel_tool_calls>` 指令仅注入给 Anthropic 模型。`mimo_v2_5_pro` 通过 `ChatOpenAI` 接口访问，解析 profile 时匹配不到任何内置 profile，拿到空 `HarnessProfile()`，无并行指令。
2. **Skill 步骤描述为串行编号**：所有 Skill 的 `fast.md` / `expert.md` 用 Step 1、Step 2、Step 3 顺序编号，模型自然按顺序执行。

### 框架能力确认

DeepAgents + LangGraph **天然支持并行 tool calls**：
- LangGraph ToolNode 异步路径使用 `asyncio.gather` 并发执行多个 tool calls
- 同步路径使用 `executor.map` 线程池并行
- LangGraph Send API 将多个 tool calls 分发到并行节点实例
- Middleware 的 `wrap_tool_call` 逐个拦截，不阻断并行
- `mimo_v2_5_pro`（OpenAI 兼容接口）支持在一次响应中返回多个 function calls

**结论**：框架层已就绪，只需在 Prompt 层和 Skill 层激活并行行为。

## 设计方案

### 方案选择

| 方案 | 描述 | 可靠性 | 复杂度 | 选择 |
|------|------|--------|--------|------|
| A: Prompt 层引导 | system prompt 加并行指令 + Skill 步骤改并行组 | 概率性 | 低 | **✅** |
| B: 中间件强制 | 中间件层识别并行组并强制调度 | 确定性 | 高 | |
| C: 两者结合 | Prompt 基础 + 中间件兜底 | 确定性 | 最高 | |

选择方案 A：Prompt 层引导并行。理由：
- 改动最小，不涉及中间件代码修改
- 框架已天然支持并行 tool calls，只需"告诉模型可以并行"
- 接受概率性并行——偶尔串行执行不会导致错误，只是速度稍慢
- 后续如果需要更高可靠性，可叠加方案 B

### 第一层：System Prompt 注入并行指令

在 `src/basic_qa/graph.py` 的 `SYSTEM_PROMPT` 末尾追加：

```xml
<use_parallel_tool_calls>
如果需要调用多个工具且这些工具调用之间没有数据依赖，请在同一次响应中并行发出所有独立的工具调用。优先并行调用工具以提高速度和效率。例如，当需要同时查询空气质量实时数据和气象预报数据时，应并行发出两个工具调用。但如果某些工具调用的参数依赖于前一次调用的结果，则必须串行调用，不要猜测或使用占位参数。
</use_parallel_tool_calls>
```

与 Anthropic Harness Profile 中的 `<use_parallel_tool_calls>` 指令等价，适配中文语境。

### 第二层：Skill 步骤描述改为并行组模式

**改前**（纯串行编号）：

```markdown
## 执行步骤
1. 调用 helper_get_latest_time 获取最新时间
2. 调用 mcp_city_common_get_air_quality_realtime_stat 获取实时数据
3. 调用 mcp_weather_forecast_daily 获取气象数据
4. 调用 mcp_city_common_get_air_quality_forecast 获取预报数据
5. 调用 chart_city_concentration_line_chart 生成图表
```

**改后**（显式标注并行组）：

```markdown
## 执行步骤

### Step 1 [串行] — 获取时间基准
调用 helper_get_latest_time 获取最新时间

### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 mcp_city_common_get_air_quality_realtime_stat 获取实时数据
- 调用 mcp_weather_forecast_daily 获取气象数据
- 调用 mcp_city_common_get_air_quality_forecast 获取预报数据

### Step 3 [串行] — 生成图表（依赖 Step 2 的实时数据）
调用 chart_city_concentration_line_chart 生成图表
```

关键变化：
- `[并行组]` 标签显式指导模型并行发出工具调用
- `[串行]` 标签标注有依赖的步骤
- 并行组内用 `-` 列表而非编号，避免顺序暗示
- 并行组标题注明"互不依赖，必须并行发出"

### 各 Skill 的并行组划分

#### air-quality-basic-query

| 步骤 | 标签 | 工具 | 依赖 |
|------|------|------|------|
| Step 1 | 串行 | `helper_get_latest_time` | 无 |
| Step 2 | **并行组** | `mcp_city_common_get_air_quality_realtime_stat` / `mcp_weather_forecast_*` / `mcp_city_common_get_air_quality_forecast` | Step 1 |
| Step 3 | 串行 | `chart_city_concentration_line_chart` | Step 2 主数据 |

#### compliance-feasibility

| 步骤 | 标签 | 工具 | 依赖 |
|------|------|------|------|
| Step 1 | 串行 | `helper_get_latest_time` | 无 |
| Step 2 | **并行组** | `mcp_city_analysis_compliance_prediction` / `mcp_city_common_get_air_quality_realtime_stat` / `mcp_weather_forecast_*` / `mcp_city_common_get_air_quality_forecast` | Step 1 |
| Step 3 | 串行 | 校验 + 后续 | Step 2 主数据 |

#### trend-analysis

| 步骤 | 标签 | 工具 | 依赖 |
|------|------|------|------|
| Step 1 | 串行 | `helper_get_latest_time` | 无 |
| Step 2 | **并行组** | `oneIndexManyCityLine` / `mcp_city_common_get_air_quality_realtime_stat` / `mcp_weather_forecast_*` / `mcp_city_common_get_air_quality_forecast` | Step 1 |
| Step 3 | 串行 | `chart_city_concentration_line_chart` | Step 2 主数据 |

#### comparison-composition

| 步骤 | 标签 | 工具 | 依赖 |
|------|------|------|------|
| Step 1 | 串行 | `helper_get_latest_time` | 无 |
| Step 2 | **并行组** | `text2sql_city_indexes_month_and_year` + `mcp_city_common_get_air_quality_realtime_stat` / `integratedIndexRatio` / `mcp_weather_forecast_*` | Step 1 |
| Step 3 | 串行 | `chart_*` | Step 2 数据 |

#### station-extreme

| 步骤 | 标签 | 工具 | 依赖 |
|------|------|------|------|
| Step 1 | 串行 | 解析问题 | 无 |
| Step 2 | **并行组** | `mcp_city_common_get_air_quality_realtime_stat` / `mcp_station_common_air_data` / `mcp_station_common_air_quality_forecast` | Step 1 |
| Step 3 | 串行 | 校验 + 后续 | Step 2 |

#### 不改动的 Skill

- **ranking-assessment**：步骤严格串行，无并行空间
- **regional-benchmark**：步骤严格串行，无并行空间

## 需要改动的文件

| 文件 | 改动内容 |
|------|---------|
| `src/basic_qa/graph.py` | SYSTEM_PROMPT 追加 `<use_parallel_tool_calls>` 指令 |
| `src/basic_qa/skills/air-quality-basic-query/references/fast.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/air-quality-basic-query/references/expert.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/compliance-feasibility/references/fast.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/compliance-feasibility/references/expert.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/trend-analysis/references/fast.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/trend-analysis/references/expert.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/comparison-composition/references/fast.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/comparison-composition/references/expert.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/station-extreme/references/fast.md` | 步骤改为并行组模式 |
| `src/basic_qa/skills/station-extreme/references/expert.md` | 步骤改为并行组模式 |

共 11 个文件。不涉及中间件代码、框架代码或测试代码的修改。

## 预期效果

| Skill | 并行路数 | 省掉的 RTT 次数 | 预估延迟减少 |
|-------|---------|----------------|-------------|
| air-quality-basic-query | 3 路 | 2 次 | 2-6 秒 |
| compliance-feasibility | 4 路 | 3 次 | 3-9 秒 |
| trend-analysis | 4 路 | 3 次 | 3-9 秒 |
| comparison-composition | 3 路 | 2 次 | 2-6 秒 |
| station-extreme | 3 路 | 2 次 | 2-6 秒 |

有并行空间的 Skill 整体执行时间预估减少 **30-50%**。

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 模型偶尔不并行，仍串行执行 | 中 | 低（无错误，只是速度没提升） | 接受概率性并行；可在并行组标题中使用更强措辞"必须并行发出" |
| 模型并行发出有依赖的工具调用 | 低 | 中（参数可能错误） | Skill 中明确标注每个步骤的依赖关系；`<use_parallel_tool_calls>` 指令已包含"有依赖则串行"的约束 |
| 并行工具调用中某个失败 | 低 | 低（MCPResilienceMiddleware 已处理工具失败降级） | 现有中间件已覆盖 |

## 后续演进

如果方案 A 的概率性并行不够可靠，可叠加方案 B（中间件层强制并行）：
- 新增 `ParallelToolCallMiddleware`，在 `wrap_model_call` 中解析 Skill 步骤的并行组标注
- 当模型应该并行但未并行时，由中间件拆分 tool_calls 为并行执行
- 或引入 graph 内部 SubAgent 做并行数据获取子任务

当前不实施，留作观察后的下一步。
