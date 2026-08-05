## Context

当前权限链路已经采用“中间件预分类 + slots 抽取 + MCP 可信事实 + 确定性规则引擎”的方向，但无行政区查询仍存在语义偏差：当 slots 中 `province`、`city`、`district` 均为空时，中间件把 `requested_region=None` 传入规则引擎，规则引擎再用 `default_allowed_region(user_profile)` 构造 `replace_region` 结果。这会把“用户没有指定区域”误解释为“发生行政区越权并替换区域”。

业务期望不同：无行政区是参数缺省，应自动使用 `get_user_profile.bound_region` 补全查询区域，并将该补全作为主流程后续 skill 查找、参数抽取和工具调用的真实查询上下文。补全动作需要透明告知用户，但不应污染越权修正语义。

## Goals / Non-Goals

**Goals:**

- 用户未指定行政区时，自动使用可信用户画像中的 `bound_region` 作为默认查询区域。
- 主流程必须收到结构化的补全区域，后续查询必须使用补全后的区域。
- 自动补全必须生成明确修正文案，并在最终回复中告知用户“已自动补全查询区域”。
- `PermissionResult` 语义清晰区分“自动补全默认区域”和“越权替换行政区”。
- 保留时间豁免、时间截断和真实行政区越权替换的既有优先级。

**Non-Goals:**

- 不修改 MCP 服务端工具合约。
- 不引入新的权限规则引擎库。
- 不扩大用户可访问的数据范围；自动补全只能使用用户自己的 `bound_region` 或现有 `default_allowed_region`。
- 不一次性重写历史权限 OpenSpec 中所有旧 agent 表述，仅更新本次行为相关的规范。

## Decisions

### Decision 1: 在中间件层构造自动补全区域

当 slots 清洗后不存在 `province`、`city`、`district` 时，中间件直接从 `user_profile.bound_region` 构造标准区域对象，作为后续 `PermissionFacts.requested_region` 或等价补全上下文。

选择理由：

- `get_user_profile` 是可信事实来源，`bound_region` 不需要再调用 `resolve_region_scope` 解析。
- 补全发生在编排层，主流程和规则引擎都能看到“补全后的请求区域”。
- 避免规则引擎只能根据 `requested_region=None` 进行兜底替换，造成 `replace_region` 误语义。

替代方案：

- 继续传 `requested_region=None`，由规则引擎兜底。该方案会保留现有误语义，主流程难以区分缺省补全和越权替换。
- 调用 `resolve_region_scope(bound_region.name)` 再补全。该方案增加无意义 MCP 调用和失败点，且 `bound_region` 已是可信标准名称。

### Decision 2: 自动补全结果不使用 `fix_strategy=replace_region`

无行政区自动补全 SHALL 设置 `auto_filled=true`，并携带补全区域和 `correction_text`；但只要补全后的默认区域本身通过权限校验，就 SHALL 保持 `fix_strategy=""`。`replace_region` 仅表示用户指定了越权行政区，系统替换为可访问区域。

选择理由：

- 让主流程能稳定区分“参数缺省补全”和“权限越界修正”。
- 避免把正常默认区域查询短路为 `permitted=false` 的修正查询。
- 测试和日志能更准确地解释权限结果。

### Decision 3: `permission_query_overrides.region` 是后续查询的强制参数

自动补全、行政区替换两类场景都应写入 `permission_query_overrides.region`，但通过字段组合区分语义：

- 自动补全：`auto_filled=true`、`fix_strategy=""`、`region=<bound_region>`。
- 越权替换：`auto_filled=false`、`fix_strategy="replace_region"`、`region=<replacement_region>`。

主流程 prompt 必须明确：只要 `permission_query_overrides.region` 存在，后续 skill 查找、参数抽取和工具调用必须以该区域为准。

### Decision 4: 统一 slots 行政区清洗函数

中间件和 facts 构造逻辑应复用一致的清洗语义，将 `None`、`null`、`NULL`、空字符串等 LLM 常见空值统一视为未指定行政区。

选择理由：

- 避免 `"None"` 字符串被误判为用户指定行政区。
- 降低中间件和 facts 构造之间的分支不一致。

### Decision 5: 完整权限上下文进入状态与 prompt

`permission_context` 至少包含：

- `permission_need`
- `permission_result`
- `permission_query_overrides`
- `auto_filled`
- `correction_text`
- `matched_rules`
- `debug_context`
- `raw_slots` 或可排障的 slots 摘要
- `user_bound_region`

该上下文用于主流程执行和回复告知，不要求最终用户看到 debug 字段。

## Risks / Trade-offs

- **[Risk] 用户画像缺失或 `bound_region` 不完整** → 使用 fail-closed 或现有用户画像缺失拒绝策略；不得凭空补全行政区。
- **[Risk] 自动补全文案与其他修正文案叠加导致回复重复** → 文案生成集中在权限结果构造处，主流程只按 `correction_text` 一次性告知。
- **[Risk] prompt 上下文变长** → 只放结构化关键字段，debug 上下文控制为小字典，不塞 MCP 原始大对象。
- **[Risk] 历史测试依赖 `replace_region` 旧语义** → 同步更新测试断言，明确新语义是兼容性修正而不是行为回退。

## Migration Plan

1. 新增自动补全区域构造和 slots 清洗单元测试。
2. 调整规则引擎无行政区默认区域结果语义，或确保中间件不再把自动补全场景以 `requested_region=None` 传入规则引擎。
3. 更新 `permission_query_overrides` 构造逻辑，覆盖 `auto_filled=true` 且 `fix_strategy=""` 的场景。
4. 更新 prompt 注入规则和硬拒绝判断，避免自动补全被误短路。
5. 运行权限相关目标测试，确认时间截断和真实行政区替换不受影响。

回滚方式：保留旧 `replace_region` 分支测试可作为对照；若上线后发现主流程未消费补全区域，可临时通过 prompt 侧强制读取 `permission_query_overrides.region` 缓解，但不建议回退到旧语义。
