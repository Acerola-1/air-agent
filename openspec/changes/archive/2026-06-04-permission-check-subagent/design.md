## Context

当前项目使用 DeepAgents 框架，业务图通过 `create_deep_agent` 构建，主模型循环中通过 Skill 渐进披露机制控制工具可见性。原有的 `PermissionMiddleware` 调用服务端 RBAC 接口（`permission_vaild` MCP 工具），已弃用且未挂载到任何业务图。

设计文档（`docs/设计文档/AI智能问数权限方案设计-260521.md` V2.3）描述了一套基于时间粒度动态豁免的权限控制方案，但假设了 6 个独立 Agent 节点串联的架构（Intent → SlotFilling → Permission → Corrector → DataQuery → Response），与实际 DeepAgents + 中间件 + Skill 架构不匹配。

现有可复用的基础设施：
- `permission_rules.py`：规则预分类（`classify_permission_need` → no_check/need_check/uncertain）
- `parse_region_tool`：行政区解析（LLM structured output + region_tree.json）
- `get_beijing_time`：北京时间获取
- `permission_region.py`：确定性行政区辅助（层级白名单、父级推断、修正建议、默认安全行政区）
- `mcp_client.py`：MCP 工具注册（`get_user_profile`、`resolve_region_scope` 已上线）
- `SkillDiscoveryState`：AgentState 扩展模式

## Goals / Non-Goals

**Goals:**
- 在主模型推理前通过中间件完成完整权限审查，将 `PermissionResult` 写入 AgentState
- 中间件从 configurable 获取 `user_id`，调用 `get_user_profile` MCP 工具获取用户可信权限画像
- 中间件调用 `resolve_region_scope` MCP 工具解析用户问题中的行政区表达
- 中间件预取北京时间、用户权限画像、行政区解析结果和行政区权限上下文，降低权限审查 agent 的工具调用轮次
- 中间件将 `PermissionResult` 注入 system prompt，主模型按修正参数调整后续查询
- 行政区父子关系、同省/同市判断、默认安全行政区和修正目标建议由确定性 helper 提供
- 历史补查、批量导出、非交互式和超过 365 时间点场景不得享受时间豁免，回退基础行政区权限
- `week` 类型按日级时间段处理，与 `other` 共享 7 天窗口和截断逻辑
- 不影响主进程推理循环，权限逻辑以"插入"而非"拦截"方式工作

**Non-Goals:**
- 不实现独立的 Permission Agent / Corrector Agent 节点（设计文档的多 Agent 架构）
- 不使用 SubAgent 做权限推理（已废弃 SubAgent 方案，改为全中间件）
- 不让 LLM 自行臆测日期和行政区关系；日期计算仍通过 CodeInterpreter 执行，行政区关系优先使用中间件预取的确定性上下文
- 不替换或修改旧 `PermissionMiddleware`（保留代码，不挂载）
- 不实现 `permission_config.yaml` 的配置化（V1 硬编码规则）
- 不实现截断效果评估与备选提示（5.2.3 节，后续迭代）
- 不实现常访问区县用户画像服务对接（4.5.4 节，V1 用关联区县兜底，后续迭代）

## Decisions

### D1: 全部权限逻辑在中间件中完成，取消 SubAgent

**选择**：`PermissionClassifyMiddleware` 在 `abefore_agent` 中完成规则预分类 + 完整权限审查，将 `PermissionResult` 写入 state 并注入 system prompt

**替代方案**：
- A. 规则预分类用中间件 + 权限推理用 SubAgent（原方案）：主模型需通过 task 工具触发 SubAgent，触发时机不可靠；SubAgent 无法直接从 configurable 获取 user_id，依赖主模型在 task description 中透传用户身份，存在数据流断裂风险
- B. 全部在 Skill 工具中完成：主模型可能忘记先调权限工具，可靠性不足
- C. 全部用 SubAgent（含预分类）：每次都调 SubAgent，no_check 场景浪费延迟

**理由**：
1. 前端仅传 `user_id`（从 configurable 获取），不传 region_level/region_code/region_name。SubAgent 无法直接访问 configurable，必须依赖主模型在 task description 中透传，而主模型可能捏造或遗漏身份信息
2. 中间件的 `abefore_agent(state, runtime, config: RunnableConfig)` 可直接访问 configurable 获取 user_id
3. 中间件可直接调用 MCP 工具（`get_user_profile`、`resolve_region_scope`），无需经过主模型中转
4. 确定性计算（日期、行政区）用 Python 函数比 LLM + CodeInterpreter 更精确
5. 职责清晰：中间件负责权限审查，主模型只消费 PermissionResult

### D2: 中间件自动执行权限审查，无需主模型触发

**选择**：中间件在 `abefore_agent` 中根据 `permission_need` 自动决定是否执行完整审查；主模型无需调用任何权限相关工具

**替代方案**：
- A. 主模型通过 task 工具触发 SubAgent（原方案）：触发时机不可靠，数据流断裂
- B. Skill references 中引导：权限检查应在 find_skill 之前执行，Skill 还未选中

**理由**：权限审查是前置条件检查，应在主模型推理前完成。中间件自动执行消除了主模型遗漏触发的风险，也消除了 SubAgent 数据传递的复杂性。

### D3: 权限审查使用 `create_agent` 懒创建 + 中间件预取确定性上下文

**选择**：中间件在 `abefore_agent` 中先完成确定性上下文预取，再按 MCP 工具签名懒创建或复用一个轻量权限审查 agent；`permission_need` 为 `need_check` 或 `uncertain` 时才 `ainvoke` 该 agent。

**替代方案**：
- A. 中间件直接调用 LLM（`model.with_structured_output().ainvoke()`）：无法方便接入 CodeInterpreter，日期计算与结构化推理质量不稳定
- B. 中间件纯确定性 Python 函数：无法从自然语言中提取时间粒度和行政区语义（"浙江省今年的空气质量" → time_granularity=year, region=浙江省）
- C. 保持 SubAgent + task 工具触发（原方案）：触发时机不可靠，user_id 传递链条复杂

**理由**：
1. 中间件直接调用 `get_beijing_time`、`get_user_profile`、`resolve_region_scope` 和 `build_permission_region_context`，可把固定工具链从 LLM 循环中移出，减少往返轮次。
2. LLM 只负责语义提取、日期计算编排和最终判定；确定性数据通过 `<prefetched_context>` 注入，减少重复工具调用。
3. agent 按 MCP 工具签名懒创建，避免 MCP 工具首次加载前缓存空工具列表；签名变化时自动重建。
4. `response_format=PermissionResult` 保证结构化输出，中间件从 `structured_response` 提取结果。
5. 不走 `task` 工具流程，不受主模型触发可靠性影响。

**实现要点**：
- `_prefetch_deterministic_context()` 中：调用 `get_beijing_time`、`get_user_profile`、`resolve_region_scope` 和 `build_permission_region_context`，序列化为 `<prefetched_context>`。
- `_ensure_permission_agent()` 中：按当前 MCP 工具签名调用 `create_agent(model, tools, system_prompt, response_format=PermissionResult, middleware=[CodeInterpreterMiddleware(ptc=[])])`，其中 tools 不包含 `get_beijing_time` 和 `permission_region_context_tool`。
- `abefore_agent` 中：`result = await self._permission_agent.ainvoke({"messages": [HumanMessage(content=task_desc)]}, config)`，任务描述明确要求 agent 优先读取 `<prefetched_context>`。
- 从 `result["structured_response"]` 提取 PermissionResult，序列化为 dict 写入 state
- agent 调用失败时降级为 `permission_need=no_check`

### D4: week 类型按日级时间段处理

**选择**：`week` 在时间类型分类中直接归入"日级时间段"，与 `other` 共享 7 天豁免窗口和截断逻辑

**替代方案**：设计文档的"单一时间→转时间段"分类，week 单独处理

**理由**：week 转换后就是日期范围（如 2026-05-18 ~ 2026-05-24），与 other 本质相同，无需单独分类。确定性 Python 函数中统一为两类：单一时间点（hourly/daily_count/daily/month/month_count/year/year_count）和日级时间段（week/other）。

### D5: 中间件注册到 basic-qa，其他图暂不接入

**选择**：仅 basic-qa graph 注册权限中间件

**理由**：basic-qa 是面向自然语言问答的主入口，权限需求最直接。中间件注册比 SubAgent 更轻量，其他图可直接复用中间件定义。data_analysis 使用菜单确定性路由，intelligent_analysis 等图后续按需接入。

## Risks / Trade-offs

- **[MCP 工具调用失败]** → 降级为 permission_need=no_check（宽松）或 permitted=False（严格），默认宽松降级；后续可配置降级策略
- **[主模型可能忽略 PermissionResult 修正参数]** → 修正信息同时写入 AgentState 和注入系统提示词，双保险
- **[中间件职责较重]** → 通过拆分确定性计算函数到 permission_region.py 控制复杂度；中间件本身仅编排调用流程
- **[多轮对话中权限状态管理]** → V1 不做跨轮权限状态保持，每轮独立检查；后续迭代可优化

## 设计文档逐章节对照

> 对照源：`docs/设计文档/AI智能问数权限方案设计-260521.md` V2.3
> 符号：✅ V1实现 | 🔄 后续迭代 | ❌ 不实现 | ⚠️ 适配调整

### 第1节：文档变更记录

| 内容 | 状态 | 说明 |
|------|------|------|
| V2.3 规则版本号 | ✅ | PermissionResult.rule_version = "V2.3" |

### 第2节：总体设计原则

| 内容 | 状态 | 说明 |
|------|------|------|
| 安全优先 | ✅ | 中间件严格按规则判定，远期数据不越权 |
| 体验优先 | ✅ | 时间截断优先于行政区修正 |
| 粒度区分（7天/近两月/近两年） | ✅ | 确定性 Python 函数实现完整规则表 |
| 透明告知 | ✅ | correction_text 字段 + 文案模板 |

### 2.1 适用范围

| 内容 | 状态 | 说明 |
|------|------|------|
| 仅AI智能问数实时查询场景 | ✅ | 仅 basic-qa 接入 |
| 历史数据补查不享受豁免 | ✅ | 中间件确定性判断命中后回退基础行政区权限 |
| 批量导出不享受豁免 | ✅ | 同上 |
| 报表定时推送不享受豁免 | ✅ | 同上 |
| 大数据量导出（>365时间点）不享受豁免 | ✅ | 同上 |

### 第3节：核心权限模型

#### 3.1 基础行政区权限

| 内容 | 状态 | 说明 |
|------|------|------|
| 省级用户权限表 | ✅ | 确定性 Python 函数完整规则 |
| 地市级用户权限表 | ✅ | 同上 |
| 区县级用户权限表 | ✅ | 同上 |
| 汇总数据 vs 明细数据区分 | ✅ | 确定性 Python 函数明确 |
| 数据存储粒度说明（每层级一条记录） | ⚠️ | 设计文档的存储层说明，中间件无需感知 |

#### 3.2 时间粒度豁免规则

| 内容 | 状态 | 说明 |
|------|------|------|
| 9种时间类型豁免窗口表 | ✅ | 确定性 Python 函数完整规则表 |
| hourly/daily_count 7天窗口（精确到小时） | ✅ | 同上 |
| daily 7天窗口（精确到天） | ✅ | 同上 |
| week → 日级时间段处理 | ✅ | ⚠️ 设计文档为"单一时间→转时间段"，我们按日级时间段处理（D4决策） |
| month/month_count 近两月窗口 | ✅ | 确定性 Python 函数完整规则表 |
| year/year_count 近两年窗口 | ✅ | 同上 |
| other 7天窗口（时间段） | ✅ | 同上 |
| 累计型数据窗口对齐说明 | ✅ | 确定性 Python 函数包含 |

#### 3.2.1 时间类型分类说明

| 内容 | 状态 | 说明 |
|------|------|------|
| 单一时间 vs 时间段分类 | ✅ | 确定性 Python 函数两类：单一时间点 + 日级时间段 |
| week 特殊处理 | ✅ | ⚠️ 简化为日级时间段，与 other 共享逻辑（D4决策） |
| 3种豁免判定（完全/部分/无） | ✅ | 确定性 Python 函数明确 |

#### 3.3 宽免规则细节

| 内容 | 状态 | 说明 |
|------|------|------|
| 完全豁免 | ✅ | 确定性 Python 函数 |
| 部分豁免（时间截断优先） | ✅ | 同上 |
| 无豁免 | ✅ | 同上 |

#### 3.4 站点与网格数据权限适配

| 内容 | 状态 | 说明 |
|------|------|------|
| 站点数据继承行政区权限 | ✅ | 确定性 Python 函数 + 刚性约束 |
| 网格数据继承区县权限 | ✅ | 同上 |
| 豁免窗口内不受行政区限制 | ✅ | 同上 |
| 站点关联层级与权限匹配表 | ✅ | 确定性 Python 函数匹配规则表 |
| 缺失数据降级策略 | ✅ | 确定性 Python 函数明确"该数据暂不可用" |
| 站点数据部分越权文案 | ✅ | 确定性 Python 函数文案模板 |
| 网格数据空间裁剪文案 | ✅ | 同上 |

### 第4节：权限审查逻辑（伪代码）

#### 4.0 PermissionResult 数据结构

| 字段 | 状态 | 说明 |
|------|------|------|
| permitted | ✅ | PermissionResult.permitted |
| exemption | ✅ | PermissionResult.exemption |
| exemption_window | ✅ | PermissionResult.exemption_window |
| fix_strategy | ✅ | PermissionResult.fix_strategy |
| legal_time_span | ✅ | PermissionResult.legal_time_span |
| allowed_region | ✅ | PermissionResult.allowed_region |
| reason | ✅ | PermissionResult.reason |
| original_time_span | ✅ | PermissionResult.original_time_span |
| rule_version | ✅ | PermissionResult.rule_version |

新增字段（设计文档无，我们补充的）：
- `data_type`: region/station/grid 区分数据类型
- `accessible_station_count`: 可访问站点数量
- `accessible_grid_count`: 可访问网格数量
- `correction_text`: 面向用户的修正说明文案
- `time_granularity`: 推断的时间粒度

#### 4.1 权限审查主函数 `check_permission`

| 内容 | 状态 | 说明 |
|------|------|------|
| 按粒度选择豁免窗口 | ✅ | 确定性 Python 函数 |
| 单一时间判定逻辑 | ✅ | 确定性 Python 函数明确 |
| 时间段交集判定 | ✅ | 同上 |
| 完全包含判定 | ✅ | 同上 |
| 部分交集→时间截断 | ✅ | 同上 |

#### 4.2 按粒度生成豁免窗口 `get_exemption_window_by_granularity`

| 内容 | 状态 | 说明 |
|------|------|------|
| UTC+8 基准时间 | ✅ | 中间件调用 get_beijing_time |
| 各粒度窗口计算 | ✅ | 确定性 Python 函数 + CodeInterpreter 计算 |
| 月末/年末自动适配 | ✅ | 确定性 Python 函数执行日期计算 |
| 未知粒度回退7天窗口 | ✅ | 确定性 Python 函数兜底规则 |

#### 4.3 基础行政区权限检查 `check_base_region_permission`

| 内容 | 状态 | 说明 |
|------|------|------|
| 层级合法性校验 | ✅ | 确定性 helper |
| 具体行政区合法性校验 | ✅ | 确定性 helper + parse_region_tool |
| replace_region 修正策略 | ✅ | 确定性 Python 函数 + 修正策略矩阵 |

#### 4.4 关键辅助函数

| 函数 | 状态 | 说明 |
|------|------|------|
| `is_region_level_allowed` | ⚠️ | 确定性 Python 函数层级白名单表替代，非代码函数 |
| `get_allowed_regions` | ⚠️ | 中间件 + resolve_region_scope + permission_region_context_tool 替代 |
| `calculate_time_intersection` | ⚠️ | 确定性 Python 函数实现 替代 |
| `is_fully_contained` | ⚠️ | 确定性 Python 函数实现 替代 |
| `get_default_allowed_region` | ✅ | V1 提供确定性 helper，中间件优先调用 |
| `get_user_frequent_district` | 🔄 | I2 后续迭代，V1 用关联区县兜底 |
| `get_days_in_week_range` | ⚠️ | 确定性 Python 函数实现 替代 |
| `get_region_name_by_code` | ⚠️ | parse_region_tool 已覆盖 |
| `get_days_in_range` / `get_months_in_range` / `get_years_in_range` / `date_range` | ⚠️ | 确定性 Python 函数实现 替代 |
| `get_default_district_for_city` | ✅ | V1 helper 通过 region_tree 获取城市首个区县兜底 |

### 第5节：问题修正策略

#### 5.1 修正优先级

| 内容 | 状态 | 说明 |
|------|------|------|
| 时间截断优先 | ✅ | 确定性 Python 函数 + 刚性约束 |
| 行政区修正其次 | ✅ | 同上 |

#### 5.2 时间截断策略

| 内容 | 状态 | 说明 |
|------|------|------|
| other/week 截断规则 | ✅ | 确定性 Python 函数 |
| 单一时间不存在截断 | ✅ | 同上 |

#### 5.2.1 分时间类型截断规则表

| 内容 | 状态 | 说明 |
|------|------|------|
| other（含week）截断规则 | ✅ | 确定性 Python 函数 |

#### 5.2.2 修正说明模板

| 内容 | 状态 | 说明 |
|------|------|------|
| other 修正说明模板 | ✅ | 确定性 Python 函数 |
| 模板层级适配（省/地市/区县） | ✅ | 确定性 Python 函数文案模板 |

#### 5.2.3 截断效果评估与备选提示

| 内容 | 状态 | 说明 |
|------|------|------|
| ratio 计算 | 🔄 | I1 后续迭代 |
| 低于阈值触发备选提示 | 🔄 | I1 后续迭代 |
| 备选提示模板 | 🔄 | I1 后续迭代 |
| 伪代码 | 🔄 | I1 后续迭代 |

#### 5.3 行政区修正策略矩阵

##### 5.3.1 行政区修正策略

| 内容 | 状态 | 说明 |
|------|------|------|
| 省级区县上卷至地市 | ✅ | 确定性 Python 函数修正策略矩阵 |
| 省级外省上卷至省份 | ✅ | 同上 |
| 地市本省其他区县替换 | ✅ | 同上 |
| 地市外省替换 | ✅ | 同上 |
| 区县省级替换 | ✅ | 同上 |
| 区县外市替换 | ✅ | 同上 |
| 月/年粒度越权替换 | ✅ | 同上 |
| 替换优先级（常访问>关联） | ⚠️ | V1 无常访问区县，用关联区县兜底 |

##### 5.3.2 行政区修正说明模板

| 模板 | 状态 | 说明 |
|------|------|------|
| 省级用户区县上卷至地市 | ✅ | 确定性 Python 函数 |
| 省级用户外省上卷至省 | ✅ | 同上 |
| 地市用户本省其他区县替换 | ✅ | 同上 |
| 地市用户外省替换 | ✅ | 同上 |
| 区县用户省级汇总替换 | ✅ | 同上 |
| 区县用户外市替换 | ✅ | 同上 |
| 月/年粒度越权替换 | ✅ | 同上 |
| 站点数据部分越权 | ✅ | 同上 |
| 网格数据空间裁剪 | ✅ | 同上 |

### 第6节：多 Agent 架构实现

#### 6.1 总体工作流

| 设计文档节点 | 我们的实际实现 | 状态 |
|------------|--------------|------|
| Intent Agent | 主模型 + Skill 路由 | ✅ 已有 |
| SlotFilling Agent | Skill references + 主模型推理 | ✅ 已有 |
| Permission Agent | PermissionClassifyMiddleware | ✅ V1实现 |
| Corrector Agent | PermissionClassifyMiddleware（合并） | ✅ V1实现，权限判断+修正合并 |
| Data Query Skill | MCP 工具 | ✅ 已有 |
| Response Agent | 主模型输出 + correction_text | ✅ V1实现 |

> 设计文档的 6 个 Agent 节点，我们合并为中间件（Permission+Corrector）+ 主模型（其余4个已由现有架构覆盖）。

#### 6.2 Agent 详细设计

| 设计文档 Agent | 我们的实现 | 状态 | 说明 |
|--------------|-----------|------|------|
| (1) Intent Agent | Skill 路由（已有） | ✅ | 不需要新建 |
| (2) SlotFilling Agent | Skill references + 主模型推理（已有） | ✅ | 不需要新建 |
| (3) Permission Agent | PermissionClassifyMiddleware | ✅ | 中间件完成权限审查 |
| (4) Corrector Agent | PermissionClassifyMiddleware（合并） | ✅ | 合并到中间件 |
| (5) Data Query Skill | MCP 工具（已有） | ✅ | 不需要新建 |
| (6) Response Agent | 主模型 + correction_text | ✅ | 不需要新建 |
| 6.2.1 Corrector Agent 接口约定 | 中间件写入 PermissionResult 到 state | ✅ | 结构化输出 |
| 6.2.2 接口调用规范 | ⚠️ | ⚠️ | 中间件 abefore_agent 中同步调用 MCP 工具

#### 6.3 框架实现建议

| 内容 | 状态 | 说明 |
|------|------|------|
| AgentState 定义 | ✅ | 扩展 SkillDiscoveryState |
| LangGraph StateGraph 编排 | ✅ | create_deep_agent + middleware |
| 监控与日志 | 🔄 | V1 仅记录 PermissionResult，审计日志后续补充 |

### 第7节：验证用例

| 用例 | 场景 | 状态 | 说明 |
|------|------|------|------|
| 用例1 | 省级用户查本省区县年度数据（豁免外） | ✅ | 中间件应正确上卷至地市 |
| 用例2 | 省级用户查外省区县7天内数据（完全豁免） | ✅ | 中间件应返回完全豁免 |
| 用例3 | 地市用户查本省其他市区县年度数据（豁免外） | ✅ | 中间件应替换为常访问区县 |
| 用例4 | 地市用户查上月全国数据（月粒度完全豁免） | ✅ | 中间件应返回完全豁免 |
| 用例5 | 区县用户查省级30天数据（部分交集） | ✅ | 中间件应截断至7天 |
| 用例6 | 地市用户查上月其他地市数据（近两月豁免） | ✅ | 中间件应返回完全豁免 |
| 用例7 | 地市用户查3月其他地市数据（超出两月） | ✅ | 中间件应替换行政区 |
| 用例8 | 区县用户查去年全国数据（近两年豁免） | ✅ | 中间件应返回完全豁免 |
| 用例9 | 区县用户查2024年全国数据（超出两年） | ✅ | 中间件应替换行政区 |

### 第8节：配套机制

#### 8.1 依赖数据源

| 依赖 | 状态 | 说明 |
|------|------|------|
| `get_all_province_codes()` | ⚠️ | permission_region_context_tool 可按编码前缀与 region_tree 做基础推断，完整代码表后续接数据源 |
| `get_province_city_codes(code)` | ⚠️ | 同上 |
| `get_city_district_codes(code)` | ⚠️ | 同上 |
| `get_region_name_by_code(code)` | ⚠️ | 同上 |
| `get_user_frequent_district(user_id)` | 🔄 | I2 后续迭代 |
| 用户关联城市信息 | ⚠️ | V1 从 AgentState user_profile 获取；后续接入 get_permission_user_profile 工具作为可信来源 |
| `resolve_region_scope(name_or_code)` | 🔄 | 后续接入标准行政区服务，替代静态 region_tree fallback |
| `resolve_station_scope(station_name_or_code)` | 🔄 | 后续接入站点信息服务，提供站点所属行政区和关联层级 |
| `resolve_grid_scope(grid_id_or_region_or_bbox)` | 🔄 | 后续接入网格信息服务，提供覆盖区县和网格数量 |

#### 8.2 异常处理细则

| 异常场景 | 状态 | 说明 |
|---------|------|------|
| 豁免窗口计算失败 | ✅ | 确定性 Python 函数，计算精确 |
| 行政区修正无法找到替代区域 | ✅ | 中间件回退至用户关联区县 |
| 数据查询服务超时 | ❌ | MCP 工具层处理，不属于权限模块 |
| 数据查询返回空结果 | ❌ | 同上 |
| 整体流程未知异常 | 🔄 | 后续迭代补充审计日志 |

#### 8.3 规则配置化

| 内容 | 状态 | 说明 |
|------|------|------|
| permission_config.yaml | 🔄 | I3 后续迭代 |
| 热加载机制 | 🔄 | I3 后续迭代 |

#### 8.4 输入数据结构参考

| 内容 | 状态 | 说明 |
|------|------|------|
| user_region 表 DDL | ❌ | 数据库设计，不属于 AI 侧实现 |
| query_slots 表 DDL | ❌ | 同上 |

#### 8.5 枚举与常量定义

| 内容 | 状态 | 说明 |
|------|------|------|
| TimeGranularity 枚举 | ✅ | PermissionResult.time_granularity 字段 |
| RegionLevel 枚举 | ✅ | AllowedRegion.level 字段 |

#### 8.6 计算公式与配置项

| 内容 | 状态 | 说明 |
|------|------|------|
| calculate_ratio | 🔄 | I1 后续迭代（截断效果评估） |
| time_truncation_min_ratio 配置 | 🔄 | I1 后续迭代 |
| alarm_threshold_unknown_granularity 配置 | 🔄 | I3 后续迭代（配置化） |

### 第9节：总结

| 内容 | 状态 | 说明 |
|------|------|------|
| 三层权限体系 | ✅ | 基础行政区 + 时间豁免 + 回退 |
| 智能修正策略 | ✅ | 时间截断优先 + 行政区修正 |
| 多 Agent 工程落地 | ⚠️ | 适配为 DeepAgents 中间件方案 |
| 用户行为学习 | 🔄 | 远期演进 |
| 多维度宽免 | 🔄 | 远期演进 |
| 效果监控 | 🔄 | 后续迭代 |

---

## 状态符号说明

- ✅ V1 实现：当前计划中会完成
- ⚠️ 适配调整：设计文档有，我们用不同方式实现（中间件确定性函数 + MCP 工具替代独立代码函数）
- 🔄 后续迭代：V1 不做，已记录在"V1 不做，留后续迭代"表中
- ❌ 不实现：设计文档声明排除或属于其他模块，无需实现

| 功能 | 设计文档章节 | V1 | 何时做 | 当前替代方案 |
|------|------------|:--:|--------|------------|
| PermissionResult 模型 | 4.0 | ✅ | — | — |
| PermissionClassifyMiddleware | 4.1 | ✅ | — | — |
| PermissionClassifyMiddleware（含完整审查） | 4.1-4.4 | ✅ | — | — |
| 站点/网格数据权限适配 | 3.4 | ✅ | — | — |
| 主模型触发引导 | 6.2 | ✅ | — | — |

### V1 不做，留后续迭代

| 功能 | 设计文档章节 | 推迟原因 | 何时做 | 当前替代方案 |
|------|------------|---------|--------|------------|
| 截断效果评估与备选提示 | 5.2.3 | 体验优化，不影响权限正确性 | 用户反馈截断后体验差时 | 截断后直接返回剩余数据，不提示备选 |
| 常访问区县（用户画像服务对接） | 4.5.4 | 需对接用户画像 API，服务可能未就绪 | 用户画像服务就绪时 | 用用户关联区县兜底，修正文案中用"关联区县"代替"常访问区县" |
| 配置化（permission_config.yaml） | 8.3 | V1 窗口参数不需要频繁调整 | 需要动态调整窗口参数时 | 规则硬编码在 确定性 Python 函数 |
| 默认行政区获取函数 `get_default_allowed_region` | 4.5.3 | ✅ 已纳入V1 | — | permission_region_context_tool 返回默认安全行政区和修正目标建议 |
| 20+ 伪代码工具函数 | 4.5 全部 | 中间件确定性 Python 函数 + MCP 工具替代 | 计算不稳定时改写为 Python @tool | 中间件按确定性函数计算 |
| 多 Agent 架构适配 | 全文档 | 设计文档假设独立 Agent 节点，实际是 DeepAgents+中间件+Skill | 需要多 Agent 编排时 | 用中间件替代 Permission Agent 和 Corrector Agent |
| 其他图接入（data_analysis等） | — | 各图有不同的 Skill 路由和工具披露策略 | 业务需求驱动 | 仅 basic-qa 接入 |

### 后续迭代详细设计预留

#### I1: 截断效果评估与备选提示

设计文档 5.2.3 节描述：
- 截断后数据量占比 `ratio = legal_days / original_days`
- 当 `ratio < 0.5`（配置项 `time_truncation_min_ratio`）时触发备选提示
- 备选提示模板："当前仅能展示 {legal_days} 天数据，是否查看您有权限的 {替代区域} 近 {original_days} 天数据？"
- 前端渲染为可点击按钮，用户选择"否"则仅展示截断结果

实现方式预留：
- 在 确定性 Python 函数增加截断效果评估步骤
- PermissionResult 中新增 `truncation_ratio` 和 `alternative_suggestion` 字段
- 或在 Skill references 中作为后置规则处理

#### I2: 常访问区县（用户画像服务对接）

设计文档 4.5.4 节描述：
- `get_user_frequent_district(user_id)` 从用户画像服务获取常访问区县
- 替代优先级：常访问区县 > 用户关联区县
- 服务超时 500ms，返回 None 时回退至关联区县

实现方式预留：
- 新增 `get_user_frequent_district` 工具（调用 `/api/v1/user/profile` REST API）
- 加入中间件的工具调用列表
- PermissionResult 的修正文案中区分"常访问区县"和"关联区县"

#### I2b: 用户权限画像工具

当前 V1 从 AgentState 的 `user_profile` 读取用户行政区信息。后续应接入独立工具作为可信来源，避免依赖前端或调用方透传字段。

建议工具契约：

```python
get_permission_user_profile(user_id: str) -> {
    "found": True,
    "user_id": "...",
    "region_level": "province|city|district",
    "region_code": "...",
    "region_name": "...",
    "province_code": "...",
    "province_name": "...",
    "city_code": "...",
    "city_name": "...",
    "frequent_regions": [
        {"code": "...", "name": "...", "level": "district"}
    ],
    "permission_flags": {
        "ai_query_enabled": True,
        "allow_time_exemption": True
    }
}
```

接入方式：
- 中间件从 configurable 获取 user_id 后调用该工具，获取用户权限画像
- 中间件使用工具结果构建 PermissionResult，而不是只依赖用户自然语言或前端透传
- 工具不可用时，降级为 no_check 或严格基础权限，具体策略由业务安全要求决定。

#### I3: 配置化

设计文档 8.3 节描述：
- `permission_config.yaml` 定义豁免窗口参数、层级白名单、修正阈值
- `get_exemption_window_by_granularity` 和 `is_region_level_allowed` 从配置加载
- 配置变更后热加载或重启生效

实现方式预留：
- 创建 `permission_config.yaml`
- 编写配置加载模块
- 确定性 Python 函数的规则表改为动态生成（从配置读取）

#### I4: 默认行政区获取函数

设计文档 4.5.3 节描述：
- `get_default_allowed_region(user, requested_admin)` 返回安全默认行政区
- 省级 → 关联省份（汇总），地市 → 关联地市（汇总），区县 → 关联区县（明细）

实现方式预留：
- 新增 `get_default_allowed_region` @tool 函数
- 加入 SubAgent 的 tools 列表
- 或将逻辑写入 确定性 Python 函数（当前方案）

#### I5: 其他图接入

当前仅 basic-qa 接入权限中间件。其他图接入需要：
- `data_analysis`：菜单确定性路由，权限检查可能在前端菜单层面已完成，需评估是否需要 AI 侧权限检查
- `intelligent_analysis`：语义路由 + `unmatched_policy="native"`，可直接复用 basic-qa 的中间件定义
- `intelligent_report` / `intelligent_tracing` / `deep_research`：按业务需求逐个接入

#### I6: 标准行政区、站点和网格工具接入

当前 V1 的 `permission_region_context_tool` 使用 `region_tree.json` 和行政区编码前缀做基础推断，只适合行政区权限的最小闭环。站点/网格权限要做到可审计，需要接入以下工具。

标准行政区工具：

```python
resolve_region_scope(name_or_code: str) -> {
    "found": True,
    "code": "...",
    "name": "...",
    "level": "province|city|district",
    "province_code": "...",
    "province_name": "...",
    "city_code": "...",
    "city_name": "...",
    "children": [{"code": "...", "name": "...", "level": "..."}],
    "data_type": "summary|detail"
}
```

站点归属工具：

```python
resolve_station_scope(station_name_or_code: str) -> {
    "found": True,
    "station_code": "...",
    "station_name": "...",
    "region_code": "...",
    "region_name": "...",
    "region_level": "city|district",
    "association_level": "city|district",
    "available": True
}
```

网格归属工具：

```python
resolve_grid_scope(grid_id_or_region_or_bbox: str) -> {
    "found": True,
    "districts": [
        {"code": "...", "name": "...", "city_code": "...", "city_name": "..."}
    ],
    "grid_count": 123,
    "invalid_grid_count": 0,
    "clip_summary": "..."
}
```

接入后测试要求：
- 区县用户查地市关联站点，豁免窗口外应无权或替换。
- 地市用户查本市区县站点，豁免窗口外应允许。
- 网格按所属区县继承权限，跨市范围需裁剪到可访问区县。
- 豁免窗口内站点/网格跨区域应放行。
