# PermissionClassifyMiddleware Spec

## Purpose

在主模型推理前完成完整权限审查：从 configurable 获取用户身份，按 `permission_need` 决定是否调用预编译权限审查 agent，将 `PermissionResult` 写入 AgentState 并注入系统提示词。

## Architecture

```
PermissionClassifyMiddleware.__init__
  └─ 初始化 agent 缓存字段，不阻塞图构建

PermissionClassifyMiddleware.abefore_agent
  ├─ 从 configurable 提取 user_id → 写入 state.user_id
  ├─ classify_permission_need(消息) → 写入 state.permission_need
  ├─ permission_need == no_check → 快速返回
  └─ permission_need != no_check →
       ├─ 中间件预取 get_beijing_time / get_user_profile / resolve_region_scope
       ├─ 中间件调用 build_permission_region_context 构建行政区权限上下文
       ├─ 将预取结果序列化到 <prefetched_context>
       ├─ 按 MCP 工具签名懒创建或复用权限审查 agent
       ├─ 构造 HumanMessage(content=任务描述含 user_id、用户问题和 <prefetched_context>)
       ├─ await self._permission_agent.ainvoke(messages, config)
       ├─ 从 result["structured_response"] 提取 PermissionResult
       ├─ 序列化为 dict → 写入 state.permission_result
       └─ fix_strategy 可执行时 → 写入 state.permission_query_overrides

PermissionClassifyMiddleware.awrap_model_call
  └─ 根据 permission_need 注入 <permission_context> 到系统提示词，硬拒绝时短路
```

## 预编译 Agent 配置

| 参数 | 值 |
|------|-----|
| model | `ModelRegistry.mimo_v2_5_pro` |
| tools | 动态注入 MCP 工具；`get_beijing_time` 和 `permission_region_context_tool` 已下沉到中间件，不放入 agent 工具列表 |
| system_prompt | 权限审查提示词（含执行流程、规则表、文案模板、刚性约束） |
| response_format | `PermissionResult`（Pydantic BaseModel） |
| middleware | `[CodeInterpreterMiddleware(ptc=[])]` |

### MCP 工具动态注入

中间件在 `abefore_agent` 中确保 MCP 工具已加载，并按当前 MCP 工具名签名懒创建或重建权限审查 agent。`get_user_profile` 和 `resolve_region_scope` 优先由中间件预取；agent 看到 MCP 动态工具，但任务描述明确要求已提供结果时不得重复调用。

## abefore_agent 行为

### 1. 提取 user_id

从 `config["configurable"]` 中读取 `user_id`，写入 `state["user_id"]`。

- `user_id` 缺失时：`permission_need = "no_check"`，快速返回

### 2. 规则预分类

调用 `classify_permission_need(question)` 判断是否涉及数据查询：

- `no_check`：知识问答类 → 快速返回，不调用预编译 agent
- `need_check`：明确的数据查询 → 调用预编译 agent
- `uncertain`：不确定 → 调用预编译 agent（宽松策略）

### 3. 调用预编译 Agent

构造任务描述（`HumanMessage`），包含：
- 用户原始问题
- `userId`
- `<prefetched_context>`，包含北京时间、用户权限画像、行政区解析结果和行政区权限上下文

调用 `await self._permission_agent.ainvoke({"messages": [HumanMessage(content=任务描述)]}, config)`。

预编译 agent 内部执行流程（由提示词引导）：
1. 读取 `<prefetched_context>` 中的 `beijing_time` 作为日期计算基准
2. 读取 `<prefetched_context>` 中的 `user_profile` 作为用户权限画像
3. 读取 `<prefetched_context>` 中的 `requested_region` 作为行政区解析结果
4. 读取 `<prefetched_context>` 中的 `permission_region_context` 作为行政区权限上下文
5. 推断时间粒度和时间范围，用 CodeInterpreter 计算豁免窗口
6. 判断适用范围排除项
7. 判断行政区权限和修正策略
8. 生成 correction_text
9. 返回 PermissionResult（`response_format` 保证结构化）

### 4. 提取 PermissionResult

从 agent 返回结果中提取 `structured_response`（即 PermissionResult 实例），序列化为 dict 写入 `state["permission_result"]`。当 `fix_strategy=replace_region` 时，将 `allowed_region` 规范化为 `state["permission_query_overrides"]["region"]`；当 `fix_strategy=truncate_time` 时，将 `legal_time_span` 规范化为 `state["permission_query_overrides"]["time_span"]`。

### 5. 异常处理

- `ainvoke` 超时或抛异常：降级为 `permission_need = "no_check"`
- `structured_response` 为空或格式错误：降级为 `permission_need = "no_check"`
- 记录异常日志

## awrap_model_call 行为

根据 `state["permission_need"]` 注入权限上下文到系统提示词：

- `permission_need = "no_check"`：不注入任何权限上下文
- `permission_need = "need_check"/"uncertain"` 且有 `permission_result`：注入 `<permission_context>` 包含 PermissionResult 摘要
- 硬拒绝：`permitted=false` 且 `fix_strategy=reject` 或无可用修正策略时，直接返回拒绝答复，不继续主模型调用

注入内容引导主模型：
- `permission_query_overrides` 存在时：后续 skill 查找、参数抽取和工具调用必须以该对象为准
- `fix_strategy = "truncate_time"`：使用 `permission_query_overrides.time_span` 作为后续查询时间范围
- `fix_strategy = "replace_region"`：使用 `permission_query_overrides.region` 作为后续查询行政区
- `fix_strategy = ""`：不修正，按原问题继续
- `correction_text` 非空时，最终回复必须包含该修正说明

## 适用范围排除项

以下场景不享受时间豁免，必须回退基础行政区权限：

- 历史数据补查：查询时间范围超过当前日期前 1 年
- 批量数据导出：单次导出超过 1000 条记录
- 报表定时推送、离线数据同步等非交互式场景
- 单次查询涉及超过 365 个时间点

## 时间粒度豁免窗口

| 粒度 | 豁免窗口 | 精度 |
|------|---------|------|
| hourly / daily_count | 最近 7 天 | 精确到小时 |
| daily / week / other | 最近 7 天 | 精确到天 |
| month / month_count | 近两月 | 当前月和上一个自然月 |
| year / year_count | 近两年 | 当前年和上一自然年 |
| 未知粒度 | 最近 7 天 | 兜底按日级时间段 |

## 行政区权限规则

| 用户层级 | 可访问层级 |
|---------|-----------|
| province | province, city |
| city | province, city, district |
| district | city, district |

## 站点/网格权限规则

- station/grid 继承所属行政区权限
- 站点关联地市 → 汇总数据
- 站点关联区县 → 明细数据
- 网格数据 → 区县明细数据
- 豁免窗口内不受行政区限制
- 站点/网格缺少行政区代码 → permitted=false，提示"该数据暂不可用"

## 修正策略

优先级：时间截断 > 行政区修正

| 用户层级 | 越权场景 | 修正策略 |
|---------|---------|---------|
| province | 本省区县 | 上卷至所属地市 |
| province | 外省区县 | 上卷至所属省份 |
| province | 外省地市 | 上卷至所属省份 |
| city | 本省其他市区县 | 替换为本市常访问区县 |
| city | 外省任何层级 | 替换为用户关联地市 |
| district | 省级汇总 | 替换为用户关联区县 |
| district | 外市任何层级 | 替换为用户关联区县 |

## State 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | `str` | 从 configurable 获取的用户 ID |
| `permission_need` | `str` | 规则预分类结果：no_check/need_check/uncertain |
| `permission_result` | `dict` | 完整权限审查结果，PermissionResult 序列化 |
| `permission_query_overrides` | `dict` | 可修正越权场景的强制查询覆盖参数 |

## File Location

中间件实现：`src/common/middleware/permission_classify_middleware.py`
权限审查提示词：`src/common/middleware/permission_check_prompt.py`（从 subagents 移至 common）
确定性计算函数：`src/common/permission_region.py`
权限结果模型：`src/common/permission_result.py`
规则预分类：`src/common/permission_rules.py`
