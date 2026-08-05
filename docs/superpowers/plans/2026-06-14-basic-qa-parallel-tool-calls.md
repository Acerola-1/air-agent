# Basic-QA 并行 Tool Calls 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 System Prompt 注入并行指令 + Skill 步骤改写为并行组模式，激活 basic-qa 图的并行 tool calls 能力，减少 30-50% 的 Skill 执行延迟。

**Architecture:** 双层改动——(1) 在 `graph.py` 的 SYSTEM_PROMPT 末尾追加 `<use_parallel_tool_calls>` XML 指令；(2) 将 5 个有并行空间的 Skill 的 `fast.md` 和 `expert.md` 中的"数据获取流程"从串行编号改为 `[并行组]` / `[串行]` 标注模式。框架层（LangGraph ToolNode）已天然支持并行，无需改动。

**Tech Stack:** Prompt engineering, Markdown Skill 文件, Python (graph.py)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/basic_qa/graph.py` | 修改 | SYSTEM_PROMPT 追加 `<use_parallel_tool_calls>` 指令 |
| `src/basic_qa/skills/air-quality-basic-query/references/fast.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/air-quality-basic-query/references/expert.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/compliance-feasibility/references/fast.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/compliance-feasibility/references/expert.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/trend-analysis/references/fast.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/trend-analysis/references/expert.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/comparison-composition/references/fast.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/comparison-composition/references/expert.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/station-extreme/references/fast.md` | 修改 | 数据获取流程改为并行组模式 |
| `src/basic_qa/skills/station-extreme/references/expert.md` | 修改 | 数据获取流程改为并行组模式 |

---

### Task 1: System Prompt 追加并行指令

**Files:**
- Modify: `src/basic_qa/graph.py:57` (SYSTEM_PROMPT 结尾处)

- [ ] **Step 1: 在 SYSTEM_PROMPT 末尾追加 `<use_parallel_tool_calls>` 指令**

在 `src/basic_qa/graph.py` 中，将 SYSTEM_PROMPT 的最后一行（第 57 行的 `"""` 闭合引号之前）追加并行工具调用指令。

将：

```python
    如果 find_skill 未命中，则回到 DeepAgents 原生能力：根据系统已披露的 Skill 列表、工具说明和问题语义自行判断，
    可按需读取可能相关的 Skill 并选择工具，但数据必须来自真实工具的有效数据，不得捏造。

"""
```

改为：

```python
    如果 find_skill 未命中，则回到 DeepAgents 原生能力：根据系统已披露的 Skill 列表、工具说明和问题语义自行判断，
    可按需读取可能相关的 Skill 并选择工具，但数据必须来自真实工具的有效数据，不得捏造。

    <use_parallel_tool_calls>
    如果需要调用多个工具且这些工具调用之间没有数据依赖，请在同一次响应中并行发出所有独立的工具调用。优先并行调用工具以提高速度和效率。例如，当需要同时查询空气质量实时数据和气象预报数据时，应并行发出两个工具调用。但如果某些工具调用的参数依赖于前一次调用的结果，则必须串行调用，不要猜测或使用占位参数。
    </use_parallel_tool_calls>

"""
```

- [ ] **Step 2: 验证 Python 语法正确**

Run: `python3 -m py_compile src/basic_qa/graph.py`
Expected: 无输出（编译通过）

---

### Task 2: air-quality-basic-query — fast.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/air-quality-basic-query/references/fast.md:18-29`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 fast.md 中的"数据获取流程"部分：

```markdown
### 数据获取流程

1. 基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将"今天、昨天、当前、最近"等相对时间解析为绝对日期、截止小时和查询场景。
2. 调用`mcp_city_common_get_air_quality_realtime_stat`获取主数据；主数据工具调用失败时，自动重试 1 次，间隔 2 秒。
3. 校验主数据是否包含目标城市、统计时段、AQI、等级、首要污染物和六因子；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。
4. 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败时，不重试，在答案末尾标注："缺失气象数据，未分析气象对空气质量的影响"。
5. 空气质量数值预报（独立步骤）：
   - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 补充当日剩余小时或未来短临趋势研判；历史日期和当日主数据已覆盖全天时不得主动调用。
   - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测事实（小时值、日累计、峰值、均值、AQI、等级、首要污染物、排名、占比）。
   - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来短临趋势补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
   - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。
6. 主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果是问当天是数据,则需要输出该天的日累计时间段图表;如果是问历史某天的数据,则需要输出该天的小时时间段图表,如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

改为：

```markdown
### 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将"今天、昨天、当前、最近"等相对时间解析为绝对日期、截止小时和查询场景。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取主数据；主数据工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败时，不重试，在答案末尾标注："缺失气象数据，未分析气象对空气质量的影响"。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 补充当日剩余小时或未来短临趋势研判；历史日期和当日主数据已覆盖全天时不得主动调用。
  - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测事实（小时值、日累计、峰值、均值、AQI、等级、首要污染物、排名、占比）。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来短临趋势补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
  - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 主数据结果）
校验主数据是否包含目标城市、统计时段、AQI、等级、首要污染物和六因子；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 主数据结果）
主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果是问当天是数据,则需要输出该天的日累计时间段图表;如果是问历史某天的数据,则需要输出该天的小时时间段图表,如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 3: air-quality-basic-query — expert.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/air-quality-basic-query/references/expert.md:14-24`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 expert.md 中的"数据获取流程"部分：

```markdown
### 数据获取流程

1. 基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将相对时间解析为绝对日期、截止小时、单城/多城场景和是否需要实时口径。
2. 调用`mcp_city_common_get_air_quality_realtime_stat`获取主数据；主数据工具调用失败时，自动重试 1 次，间隔 2 秒。
3. 校验主数据是否包含目标城市、统计时段、AQI、等级、首要污染物和六因子；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。
4. 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败时，在注意事项中标注："缺失气象数据，未分析气象对空气质量的影响"。
5. 空气质量数值预报（独立步骤）：
   - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 补充当日剩余小时或未来短临趋势研判；历史日期和当日主数据已覆盖全天时不得主动调用。
   - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测事实（小时值、日累计、峰值、均值、AQI、等级、首要污染物、排名、占比）。
   - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来短临趋势补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
   - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。
6. 主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果是问当天是数据,则需要输出该天的日累计时间段图表;如果是问历史某天的数据,则需要输出该天的小时时间段图表,如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

改为：

```markdown
### 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将相对时间解析为绝对日期、截止小时、单城/多城场景和是否需要实时口径。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取主数据；主数据工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败时，在注意事项中标注："缺失气象数据，未分析气象对空气质量的影响"。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 补充当日剩余小时或未来短临趋势研判；历史日期和当日主数据已覆盖全天时不得主动调用。
  - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测事实（小时值、日累计、峰值、均值、AQI、等级、首要污染物、排名、占比）。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来短临趋势补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
  - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 主数据结果）
校验主数据是否包含目标城市、统计时段、AQI、等级、首要污染物和六因子；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 主数据结果）
主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果是问当天是数据,则需要输出该天的日累计时间段图表;如果是问历史某天的数据,则需要输出该天的小时时间段图表,如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 4: compliance-feasibility — fast.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/compliance-feasibility/references/fast.md:22-33`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 fast.md 中的"数据获取流程"部分（第 1-6 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并解析目标城市、日期、当前小时和"保良/规避重污染"研判目标。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 主数据: 调用 `mcp_city_analysis_compliance_prediction` 获取指定城市的当前小时浓度、累计浓度、等级、剩余控制值和可行性字段；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 数据补充: 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 快速模式只做可行性判定和阈值差距说明；气象数据仅在工具可快速获取时补充事实说明：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 查询未来空气质量数值预报数据；历史日期和当日主数据已覆盖全天时不得主动调用。
  - 用途：仅用于未来达标可行性或风险补充研判；不得替代达标预测主工具（`mcp_city_analysis_compliance_prediction`）的累计浓度、控制阈值和可行性判定，不得覆盖已监测累计事实。预报数据仅用于未发生时段的风险补充。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来达标风险补充研判）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，研判未考虑未来空气质量数值预报"。
  - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 主数据结果）
按 `SKILL.md` 的"通用数据判读规则"校验主数据是否包含累计浓度、当前值、剩余控制值或"无法完成"等明确研判字段；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。
```

---

### Task 5: compliance-feasibility — expert.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/compliance-feasibility/references/expert.md:19-30`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 expert.md 中的"数据获取流程"部分（第 1-6 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并解析目标城市、日期、当前小时、目标污染物和"保良/规避重污染"研判目标。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 主数据: 调用 `mcp_city_analysis_compliance_prediction` 获取指定城市的当前小时浓度、累计浓度、等级、剩余控制值和可行性字段；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 数据补充: 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 专家模式必须调用气象工具补充气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在查询日期为当日且主监测数据未覆盖未来时段时，调用 `mcp_city_common_get_air_quality_forecast` 查询未来空气质量数值预报数据；历史日期和当日主数据已覆盖全天时不得主动调用。
  - 用途：仅用于未来达标可行性或风险补充研判；不得替代达标预测主工具（`mcp_city_analysis_compliance_prediction`）的累计浓度、控制阈值和可行性判定，不得覆盖已监测累计事实。预报数据仅用于未发生时段的风险补充。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来达标风险补充研判）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，研判未考虑未来空气质量数值预报"。
  - 降级规则：失败自动重试 1 次，间隔 2 秒；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 主数据结果）
按 `SKILL.md` 的"通用数据判读规则"校验主数据是否包含累计浓度、控制阈值、实时小时浓度和明确可行性字段；主数据为空或缺少累计浓度/控制阈值时，直接进入数据缺失处理，不得继续生成模板答案。
```

---

### Task 6: trend-analysis — fast.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/trend-analysis/references/fast.md:22-33`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 fast.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并解析目标城市、指标、开始日期、结束日期和时间粒度。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `oneIndexManyCityLine` 获取目标指标的逐日序列、峰值、低值、均值和变化方向；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级趋势调用 `mcp_weather_forecast_daily_history`，历史日期的小时级趋势调用 `mcp_weather_forecast_hourly_history`，当日或未来日级趋势调用 `mcp_weather_forecast_daily`，当日或未来小时级趋势调用 `mcp_weather_forecast_hourly`。气象数据获取失败或为空时，在答案末尾标注："缺失气象数据，未分析气象对趋势的影响"。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在用户请求包含当日/未来趋势或趋势区间延伸至未来时，调用 `mcp_city_common_get_air_quality_forecast` 补充未来趋势研判；纯历史趋势分析不得主动调用。
  - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测历史序列统计（峰值、均值、低值、变化方向）。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来趋势走向补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
  - 降级规则：失败自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 oneIndexManyCityLine 结果）
校验主数据是否包含时间序列、目标指标值和统计时段；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 主数据结果）
主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 7: trend-analysis — expert.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/trend-analysis/references/expert.md:19-30`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 expert.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并解析目标城市、指标、开始日期、结束日期、统计粒度和是否需要气象解释。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `oneIndexManyCityLine` 获取目标指标逐日序列、峰值、低值、均值和变化方向；主数据工具调用失败时，自动重试 1 次，间隔 2 秒。
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级趋势调用 `mcp_weather_forecast_daily_history`，历史日期的小时级趋势调用 `mcp_weather_forecast_hourly_history`，当日或未来日级趋势调用 `mcp_weather_forecast_daily`，当日或未来小时级趋势调用 `mcp_weather_forecast_hourly`。气象数据获取失败或为空时，在注意事项中标注："缺失气象数据，未分析气象对趋势的影响"。
- 空气质量数值预报（独立步骤）：
  - 调用条件：仅在用户请求包含当日/未来趋势或趋势区间延伸至未来时，调用 `mcp_city_common_get_air_quality_forecast` 补充未来趋势研判；纯历史趋势分析不得主动调用。
  - 用途：预报数据仅用于未发生时段的风险补充，不得覆盖、替代或重算已监测历史序列统计（峰值、均值、低值、变化方向）。
  - 双向规则：若有预报数据，必须结合预报数据进行分析（如未来趋势走向补充）；若无数值预报数据（工具不可用、调用失败、数据为空），必须在注意事项中标注局限："暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"。
  - 降级规则：失败自动重试 1 次，间隔 2 秒；重试仍失败时，基于已有监测数据和可用气象数据降级回答，不得提及工具、接口或错误细节。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 oneIndexManyCityLine 结果）
校验主数据是否包含时间序列、目标指标值、统计时段、峰值和低值；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 主数据结果）
主数据获取成功后，必须调用 `chart_city_concentration_line_chart` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 8: comparison-composition — fast.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/comparison-composition/references/fast.md:22-29`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 fast.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将"本月、今年、去年同期、同比"等相对周期解析为明确的月度或年度统计周期。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `text2sql_city_indexes_month_and_year`、`mcp_city_common_get_air_quality_realtime_stat` 获取城市月数据以及同比数据。
- 需要查询六因子占比时调用 `integratedIndexRatio` 获取综合指数、优良天数、六因子浓度、分指数、占比和同比变化；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败或为空时，在答案末尾标注："气象数据暂缺"。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 数据结果）
校验主数据是否包含目标城市、统计周期、综合指数以及同比或占比核心字段；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 数据结果）
- 同比对比主数据获取成功后，必须调用 `chart_city_indexes_month_year_line_chart` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
- 六因子占比主数据获取成功后，必须调用 `chart_city_composite_index_six_factor_ratio` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 9: comparison-composition — expert.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/comparison-composition/references/expert.md:19-26`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 expert.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 获取时间基准
基于已注入的真实北京时间，使用 `helper_get_latest_time` 获取仓库数据的最新时间，并将"本月、今年、去年同期、同比"等相对周期解析为明确的月度或年度统计周期。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `text2sql_city_indexes_month_and_year`、`mcp_city_common_get_air_quality_realtime_stat` 获取城市月数据以及同比数据。
- 需要查询六因子占比时调用 `integratedIndexRatio` 获取综合指数、优良天数、六因子浓度、分指数、占比和同比变化；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 必须调用气象工具获取气象数据：历史日期的日级数据调用 `mcp_weather_forecast_daily_history`，历史日期的小时级数据调用 `mcp_weather_forecast_hourly_history`，当日或未来日级数据调用 `mcp_weather_forecast_daily`，当日或未来小时级数据调用 `mcp_weather_forecast_hourly`。气象数据获取失败或为空时，在注意事项中标注："气象数据暂缺"。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 数据结果）
校验主数据是否包含核心同比或占比字段、统计周期和目标城市；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 生成图表（依赖 Step 2 数据结果）
- 同比对比主数据获取成功后，必须调用 `chart_city_indexes_month_year_line_chart` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
- 六因子占比主数据获取成功后，必须调用 `chart_city_composite_index_six_factor_ratio` 获取图表数据；如果图表数据失败，等待 2 秒后自动重试 1 次；重试仍失败则不影响组织结论。
```

---

### Task 10: station-extreme — fast.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/station-extreme/references/fast.md:21-27`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 fast.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 解析问题
解析用户问题中的城市、日期、站点场景和目标污染等级；当前技能未配置专用数据工具时，不得假设已能查询站点数据。
若运行上下文或上游结果已提供站点主数据，先使用该数据；若未提供主数据，直接进入数据缺失处理，不得编造工具结果或站点名称。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 主数据: 调用 `mcp_station_common_air_data` 获取站点历史污染数据，用于数据补充和异常判断；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 补充数据: 调用 `mcp_station_common_air_quality_forecast` 获取站点未来污染预测数据，用于数据补充和异常判断；工具调用失败时，自动重试 1 次，间隔 2 秒。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 数据结果）
校验站点主数据是否包含站点名称、AQI、等级、首要污染物和统计时段；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 组织输出
快速模式只输出站点事实、站点明细和可用的城市整体背景，不做污染来源判断或排查建议。
```

---

### Task 11: station-extreme — expert.md 改写并行组

**Files:**
- Modify: `src/basic_qa/skills/station-extreme/references/expert.md:19-26`

- [ ] **Step 1: 将"数据获取流程"从串行编号改为并行组标注**

将 expert.md 中的"数据获取流程"部分（第 1-7 步）改为：

```markdown
# 数据获取流程

#### Step 1 [串行] — 解析问题
解析用户问题中的城市、日期、站点场景、目标污染等级和是否需要排查方向；当前技能未配置专用数据工具时，不得假设已能查询站点数据。
若运行上下文或上游结果已提供站点主数据，先使用该数据；若未提供主数据，直接进入数据缺失处理，不得编造工具结果或站点名称。

#### Step 2 [并行组] — 数据获取（以下调用互不依赖，必须并行发出）
- 调用 `mcp_city_common_get_air_quality_realtime_stat` 获取指定城市的实时空气质量数据作为数据补充；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 主数据: 调用 `mcp_station_common_air_data` 获取站点历史污染数据，用于数据补充和异常判断；工具调用失败时，自动重试 1 次，间隔 2 秒。
- 补充数据: 调用 `mcp_station_common_air_quality_forecast` 获取站点未来污染预测数据，用于数据补充和异常判断；工具调用失败时，自动重试 1 次，间隔 2 秒。

#### Step 3 [串行] — 校验主数据（依赖 Step 2 数据结果）
校验站点主数据是否包含站点名称、AQI、等级、首要污染物、统计时段和可用六因子；主数据为空时，直接进入数据缺失处理，不得继续生成模板答案。

#### Step 4 [串行] — 组织输出
专家模式仅在主数据包含城市整体数据、站点类型或站点地址时做异常程度和排查方向补强；没有现场核查、源解析或地址数据时，不得断言污染来源。
```

---

### Task 12: 最终验证

**Files:**
- All modified files

- [ ] **Step 1: 检查 graph.py Python 语法**

Run: `python3 -m py_compile src/basic_qa/graph.py`
Expected: 无输出（编译通过）

- [ ] **Step 2: Ruff 检查 graph.py**

Run: `ruff check src/basic_qa/graph.py`
Expected: 无输出（无 lint 错误）

- [ ] **Step 3: 检查所有 Skill 文件可读性**

Run: `find src/basic_qa/skills -name "fast.md" -o -name "expert.md" | head -10 | xargs -I{} sh -c 'echo "=== {} ===" && head -5 {}'`
Expected: 所有文件头部正常显示，无乱码

- [ ] **Step 4: 查看完整 diff 供用户 review**

Run: `git diff --stat`
Expected: 11 个文件被修改

**注意：不 commit。用户要求最后自己 review change 后再决定是否 commit。**
