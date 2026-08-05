# Permission Check SubAgent Spec

## Purpose

执行完整的语义分析权限推理：根据用户问题、时间粒度、行政区信息，计算豁免窗口、判定权限、生成修正策略和修正说明文案，返回结构化 PermissionResult。

## Trigger

主模型通过 `task` 工具触发，触发条件：
- AgentState 中 `permission_need` 为 `"need_check"` 或 `"uncertain"`
- 主模型系统提示词引导："数据查询类问题，请先调用 task(permission_check, ...) 进行权限检查"

## Input

SubAgent 接收的描述信息（`task` 的 `description` 参数）应包含：
- 用户原始问题
- 用户身份信息（region_level、region_code、region_name）
- 当前北京时间

## Execution Flow

```
1. 调 get_beijing_time → 获取当前时间基准
2. 调 parse_region_tool → 解析行政区信息（层级、代码）
3. LLM 推断时间粒度 + 解析时间范围
4. 判断豁免窗口（按提示词中的规则表）
5. 计算时间交集（LLM 推理，日期计算必须用 CodeInterpreter/quickjs）
6. 判定行政区权限（LLM 按规则表推理）
7. 确定修正策略（截断时间 / 替换行政区 / 无修正）
8. 生成修正说明文案（按提示词中的文案模板）
9. 返回 PermissionResult
```

## Time Granularity Rules

### Classification

| 分类 | 时间类型 | 豁免窗口 |
|------|---------|---------|
| 单一时间点 | hourly, daily_count, daily, month, month_count, year, year_count | 按粒度对应窗口 |
| 日级时间段 | week, other | 最近7天 |

> `week` 按日级时间段处理，转换为该自然周的起止日期后与 `other` 共享逻辑。

### Exemption Windows

| 粒度 | 豁免窗口 | 示例（当前 2026-05-20） |
|------|---------|----------------------|
| hourly / daily_count | 最近7天（精确到小时） | 2026-05-14 00:00:00 ~ 2026-05-20 23:59:59 |
| daily / week / other | 最近7天（精确到天） | 2026-05-14 ~ 2026-05-20 |
| month / month_count | 近两月 | 2026-04-01 ~ 2026-05-31 |
| year / year_count | 近两年 | 2025-01-01 ~ 2026-12-31 |

### Exemption Logic

**单一时间点**：
- 时间点在豁免窗口内 → 完全豁免（无行政区限制）
- 时间点不在窗口内 → 回退基础行政区权限

**日级时间段（week/other）**：
- 完全在窗口内 → 完全豁免
- 部分交集 → 时间截断（保留交集部分，行政区不变）
- 无交集 → 回退基础行政区权限

## Region Permission Rules

### Level Whitelist

| 用户层级 | 可访问层级 |
|---------|-----------|
| province | province, city |
| city | province, city, district |
| district | city, district |

> 省级用户不能查区县明细；区县用户不能查省级汇总。

### Allowed Regions

| 用户层级 | 查省级 | 查地市 | 查区县 |
|---------|-------|-------|-------|
| province | 全国各省（汇总） | 本省地市（汇总） | 禁止 |
| city | 本省（汇总） | 本省地市（汇总） | 本市区县（明细） |
| district | 禁止 | 本市（汇总） | 本市区县（明细） |

## Correction Strategy

优先级：时间截断 > 行政区修正

### Region Correction Matrix

| 用户层级 | 越权场景 | 修正策略 |
|---------|---------|---------|
| 省级 | 本省区县 | 上卷至所属地市 |
| 省级 | 外省区县 | 上卷至所属省份 |
| 省级 | 外省地市 | 上卷至所属省份 |
| 地市 | 本省其他市区县 | 替换为本市常访问区县 |
| 地市 | 外省任何层级 | 替换为用户关联地市 |
| 区县 | 省级汇总 | 替换为用户关联区县 |
| 区县 | 外市任何层级 | 替换为用户关联区县 |

## Correction Text Templates

- 省级用户区县上卷："因数据权限限制，省级用户无法查看区县明细数据，已为您展示{区县}所属{地市}的汇总数据：{value}。"
- 省级用户外省上卷："因数据权限限制，省级用户无法查看外省地市/区县数据，已为您展示{地市/区县}所属{省份}的省级汇总数据：{value}。如需查看明细，可查询最近7天的数据。"
- 地市用户本省其他区县替换："因数据权限限制，地市用户无法查看其他市区县数据，已为您展示您所在{本市}的{常访问区县}数据：{value}。"
- 地市用户外省替换："因数据权限限制，您无法查看外省数据，已为您展示您所在城市{关联地市}的数据：{value}。"
- 区县用户省级替换："因数据权限限制，区县用户无法查看省级汇总数据，已为您展示您所在{区县}的明细数据：{value}。如需查看省级汇总，可查询最近7天的数据。"
- 区县用户外市替换："因数据权限限制，您无法查看外市数据，已为您展示您所在{区县}的数据：{value}。"
- 月/年粒度越权："月/年统计数据不支持跨行政区查看，已为您切换至有权限的{区域}数据：{value}。"

## Rigid Constraints for SubAgent

```
【刚性约束】
1. 所有日期计算（豁免窗口生成、时间交集计算、日期截断）必须使用 CodeInterpreter 编写 JavaScript 执行，严禁心算或凭记忆推算日期。
2. 行政区层级白名单必须严格遵循上述规则表，不得自行扩展。
3. 修正策略严格按优先级：时间截断 > 行政区修正。单一时间点不涉及时间截断。
4. 必须返回 PermissionResult 结构，不得返回自由文本。
5. 完全豁免时，fix_strategy 为空字符串，不做任何修正。
```

## SubAgent Configuration

- model: DeepSeek V3（`ModelRegistry.deepseek_v3`）
- tools: `[get_beijing_time, parse_region_tool]`
- middleware: `[CodeInterpreterMiddleware(ptc=[])]`
- response_format: `PermissionResult`（Pydantic BaseModel）
- system_prompt: 包含上述执行流程、规则表、文案模板、刚性约束

## File Location

SubAgent 定义：`src/basic_qa/subagents/permission_check.py`
SubAgent 提示词：`src/basic_qa/subagents/permission_check_prompt.py`

## Registration

在 `src/basic_qa/graph.py` 中通过 `create_deep_agent` 的 `subagents` 参数注册。
