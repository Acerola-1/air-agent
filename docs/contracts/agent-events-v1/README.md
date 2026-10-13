# Agent 事件台账契约 v1（冻结候选）

状态：**freeze_candidate** — 由 change `add-agent-event-ledger`（实施者：匿名02）产出，
**待协调者独立验收**后才冻结。冻结前第 3/5 项不得据此改共享文件；冻结后由协调者在
`ownership.md` 填写实际冻结提交与公共文件所有者。

## 文件

| 文件 | 用途 |
|---|---|
| `event-envelope.schema.json` | 信封结构（JSON Schema 2020-12）。语义约束不在此文件，见下 |
| `event-registry.json` | **单一事实源**：来源信任级别、事件类型登记、启用开关、载荷字段白名单、限制 |
| `states.json` | 运行主状态、证据来源秩、取消来源/阶段、客户端状态标志 |
| `metrics.json` | 指标口径（分子/分母/窗口）、质量标签、worker 默认值 |
| `samples/samples.json` | 合法/非法样本集 + 期望原因码（TS/Python 同一组） |
| `ownership.md` | 第 3/5 项文件/接口/迁移责任表 |

## 校验语义（实现方必须逐条实现）

按顺序判定，命中即返回该 `reason`：

1. `schema_version` 主版本必须为 `1` → 否则 `schema_version_unsupported`
2. 信封必填字段齐全（`event-registry.json.envelope.required`）→ 否则 `missing_field`
3. `event_id`/`tenant_id`/`user_id`/`membership_id` 为合法 UUID → 否则 `bad_uuid`
4. `occurred_at` 为 `...Z` 的 UTC ISO-8601 → 否则 `bad_timestamp`
5. `event_type` 存在于 `event_types` → 否则 `unknown_type`
6. 类型 `enabled` 为 true → 否则 `type_not_enabled`
7. `source` 在类型的 `sources` 内 → 否则 `source_not_allowed`
8. 载荷键（任意层级）不得命中 `forbidden_payload_key_patterns` → 否则 `forbidden_payload_key`
   （硬拒绝，优先于白名单：即使危险键被登记进白名单也必须拒绝）
9. `payload` 字段 ⊆ `payload_fields` → 否则 `payload_field_not_allowed`
10. 载荷必填字段齐全（`required_payload_fields`）→ 否则 `missing_required_payload_field`
11. 枚举字段合法：`state` ∈ `states.json.states`；`origin` ∈ `cancel_origins`；
    `phase` ∈ `cancel_phases`；`evidence_source` ∈ `evidence_sources`；`operation_kind`
    ∈ `{initial, retry, regenerate, resume}` → 否则 `invalid_enum_value`
12. 单条字节数 ≤ 类型对应上限 → 否则 `payload_too_large`

服务端另有两项不在本样本集、由实现层保证的规则：
- **身份覆盖**：`source`、`tenant_id`、`user_id`、`membership_id`、`received_at` 由服务端
  从可信上下文写入，客户端提交值一律忽略。
- **批量上限**：浏览器批次 ≤ 50 条且 ≤ 256 KiB，超限逐项返回失败，不宣称整批成功。

## 规范载荷摘要

去重与冲突判定用**规范 JSON 摘要**：
`sha256(JSON(payload, sort_keys=true, separators=(',',':'), ensure_ascii=false))`。
同 `(tenant_id, event_id)` 同摘要 → 复用已有结果；同 ID 不同摘要 → 冲突（不覆盖原事实）。

## 兼容与扩展规则

- 后向兼容扩展（新增可选载荷字段、新增启用类型）必须有样本 + 双语言契约测试，由集成者合并。
- 主版本变更（破坏性）必须先更新本 change/样本并重新冻结；消费方不得自行扩充自由字典。
- 第 3/5 项各自新增类型时：先在 `event-registry.json` 登记（`reserved_for` 标注），
  样本放各自包内，公共登记由集成者落地。

## 已登记的预留（v1 不启用）

`browser.feedback`、`browser.copy`、`browser.timing`（第 3 项）；
`runtime.model_usage`、`runtime.ttft`、`runtime.cost`、`runtime.span`、`runtime.tool_call`（第 5 项）。
提交这些类型返回 `type_not_enabled`，**不得**视为"已采集"。
