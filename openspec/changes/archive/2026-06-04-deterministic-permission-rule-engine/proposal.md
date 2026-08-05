## 背景

当前权限审查链路已经完成了 `PermissionClassifyMiddleware`、`PermissionResult`、行政区确定性辅助逻辑、两个权限 MCP 工具和单元测试等基础能力。当前实现的核心路径是：中间件先规则预分类，再预取北京时间、用户画像和行政区解析结果，最后懒创建权限审查 Agent，由 LLM Agent 完成时间粒度推断、时间范围解析、豁免窗口计算、行政区权限判定、修正策略和文案生成。

该方案在工程集成上已经跑通，但存在严重稳定性问题：LLM 需要同时承担信息抽取和规则判定职责，容易出现时间粒度推断错误、日期计算错误、权限矩阵误判、修正策略优先级错误等问题；同时每次权限审查需要额外 LLM 调用，延迟通常在 2-5 秒，且决策过程不可完全解释。

因此本变更将权限审查从“LLM 做决策”改为“LLM 只抽取信息，代码规则引擎做判定”。两个 MCP 工具 `get_user_profile` 和 `resolve_region_scope` 继续保留，但其职责从“供 LLM Agent 自主调用”调整为“由中间件主动调用并作为规则引擎可信事实输入”。

## 为什么需要变更

### 当前问题

1. **误判风险高**：LLM 需要理解规则、计算日期、比较行政区层级并生成修正策略，任一环节出错都会导致权限结果错误。
2. **延迟较高**：完整权限 Agent 调用一次需要模型推理和可能的工具调用，影响数据问答首包时间。
3. **不可解释**：虽然提示词写了规则，但最终判定来自 LLM 推理，难以保证每次命中同一条确定性规则。
4. **测试困难**：自然语言端到端测试会受到 LLM 输出波动影响，无法稳定覆盖权限矩阵。
5. **安全边界不清**：权限判断属于安全/合规逻辑，不应由概率模型直接决定。

### 目标收益

1. 权限判定毫秒级完成。
2. 同一输入事实必然得到同一 `PermissionResult`。
3. 每个结果能明确说明命中的规则、豁免窗口、修正原因和规则版本。
4. 可用纯单元测试覆盖权限矩阵和 85 条自然语言用例中的核心事实组合。
5. MCP 工具继续作为可信数据源，LLM 不再直接参与权限决策。

## 变更内容

- 新增 `PermissionFacts` 事实模型，作为规则引擎唯一输入。
- 新增 `PermissionExtractedSlots` 模型，约束 LLM 只输出时间粒度、时间范围、行政区文本、目标层级和数据类型，不允许输出权限判定结果。
- 新增 `permission_engine.py`，实现确定性 `check_permission(facts) -> PermissionResult`。
- 将 `permission_region.py` 中的时间窗口、交集、截断、排除项逻辑拆分或复用到规则引擎，形成明确的规则执行顺序。
- 改造 `PermissionClassifyMiddleware`：
  - 保留 `classify_permission_need()` 预分类。
  - 保留中间件主动调用 `get_user_profile`。
  - 使用 LLM 或规则抽取器只抽取 slots。
  - 用 slots 中的 `region_text` 调用 `resolve_region_scope`。
  - 构造 `PermissionFacts` 并调用 `check_permission()`。
  - 将 `PermissionResult` 写入 state，并生成 `permission_query_overrides`。
- 保留两个 MCP 工具：
  - `get_user_profile`：提供可信用户权限画像。
  - `resolve_region_scope`：提供标准行政区对象和父级关系。
- 降级策略由“LLM 审查失败降级 no_check”调整为可配置策略，默认 P0 仍保持宽松降级，但规则引擎输入完整时必须执行确定性判定。
- 新增规则引擎单元测试、slots 抽取单元测试、中间件改造测试和回归测试矩阵。

## 能力变化

### 新增能力

- `permission-facts-schema`：权限事实输入模型，聚合用户画像、请求行政区、时间范围、粒度、数据类型和原始问题。
- `permission-rule-engine`：确定性权限规则引擎，负责时间豁免、基础行政区权限、修正策略和拒绝策略。
- `permission-slot-extraction`：LLM 限定为结构化信息抽取，不允许做权限判定。
- `permission-mcp-context`：两个 MCP 工具作为可信事实源，由中间件主动调用。

### 修改能力

- `permission-classify-middleware`：从“预取上下文 + 调用权限 Agent 返回 PermissionResult”改为“预取上下文 + 抽取 slots + 调用规则引擎返回 PermissionResult”。
- `permission-result-schema`：保留现有结构，必要时增加 `matched_rules`、`debug_context` 等可选字段用于日志和测试，不影响主流程。

## 影响范围

### 代码影响

- 新增：`src/common/permission_facts.py`
- 新增：`src/common/permission_engine.py`
- 可选新增：`src/common/permission_slots.py`
- 可选新增：`src/common/permission_time.py`
- 修改：`src/common/middleware/permission_classify_middleware.py`
- 修改：`src/common/permission_region.py`（如拆分时间函数）
- 修改：`src/common/permission_result.py`（如新增可选调试字段）
- 修改：`tests/unit_tests/test_permission_classify_middleware.py`
- 新增：`tests/unit_tests/test_permission_engine.py`
- 新增：`tests/unit_tests/test_permission_facts.py`
- 新增：`tests/unit_tests/test_permission_slot_extraction.py`

### MCP 影响

- `get_user_profile` 保留，仍由中间件主动调用。
- `resolve_region_scope` 保留，但调用入参应优先使用 slots 中的 `region_text`，避免直接传完整用户问题。
- 不要求后端修改 MCP 协议，但建议文档统一工具名：当前代码使用 `get_user_profile`，不再使用文档中的 `get_permission_user_profile` 名称。

### 行为影响

- 权限结果从概率模型输出变为确定性代码输出。
- LLM 输出不再能直接放行、拒绝或修正权限。
- 部分时间豁免场景必须优先 `truncate_time`，再考虑行政区修正。
- 规则引擎在 facts 不完整时按明确降级策略处理，而不是让 LLM 猜测。

## 非目标

- 不引入外部通用规则引擎库。
- 不改造后端 Java MCP 工具核心实现。
- 不在 P0 支持站点/网格完整权限闭环，仅保留字段和后续扩展接口。
- 不把权限规则做成后台动态配置系统。
- 不要求一次性把所有自然语言时间解析代码化，P0 仍允许 LLM 抽取时间 slots。

## 与现有变更的关系

本变更是 `permission-check-subagent` 的后续修正和替代方案。`permission-check-subagent` 已完成的模型、MCP 工具预取、行政区确定性辅助和中间件接入可以复用；但其中“权限审查 Agent 返回 PermissionResult”的实现路径需要被本变更替换。
