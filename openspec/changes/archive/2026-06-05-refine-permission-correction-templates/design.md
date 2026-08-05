## Context

当前权限链路已经由确定性规则引擎生成 `PermissionResult.correction_text`，中间件再将该文案注入 `<permission_context>` 并在硬拒绝场景直接返回给用户。现有实现中的时间截断、行政区替换和硬拒绝文案主要由少量通用函数拼接，缺少《AI智能问数权限方案设计-260521.md》第 5.2 和 5.3 节要求的场景化表达。

文档中的关键差异包括：行政区替换需要按用户层级和越权类型区分，月/年粒度越权需要专门说明，时间截断需要说明“部分超出豁免窗口”并保留原请求语义。站点部分越权和站点归属缺失文案已经基本符合文档，可以保持稳定。

## Goals / Non-Goals

**Goals:**

- 将行政区替换文案从通用模板升级为矩阵化模板。
- 根据用户层级、请求区域层级、同省/同市关系、替换目标和时间粒度选择文案。
- 为月/年粒度越权替换使用专门文案。
- 优化时间截断文案，说明截断原因、合法时间范围，并尽量包含原请求区域和指标。
- 确保中间件注入和硬拒绝路径原样使用规则引擎生成的细分文案。
- 为每类核心文案增加单元测试。

**Non-Goals:**

- 不改变权限判定矩阵和行政区替换目标。
- 不实现前端可点击备选提示或完整 `change_log` 机制。
- 不改变站点权限裁剪规则。
- 不引入 LLM 生成修正文案；文案必须由确定性代码生成。

## Decisions

### 1. 新增确定性 correction template helper

在权限模块内新增或重构一个确定性文案 builder，例如：

```text
build_region_correction_text(user_profile, requested_region, replacement, time_granularity)
build_time_correction_text(original_span, legal_span, requested_region, metric_names)
```

该 helper 只接收规则引擎已有事实和确定性上下文，不调用 MCP，不依赖主模型。这样可以让文案单测覆盖完整矩阵，并避免中间件二次推断。

替代方案是在中间件聚合阶段拼文案。该方案会让单区域和多区域路径产生重复逻辑，也更难访问 `build_permission_region_context()` 中的同省/同市关系，因此不采用。

### 2. 行政区文案以修正类型推断为核心

文案类型由以下信息确定：

- `user_region.level`
- `requested_region.level`
- `replacement_region.level`
- `same_province`
- `same_city`
- `time_granularity`

优先级：

1. 若 `time_granularity` 为 `month/month_count/year/year_count` 且发生行政区替换，使用月/年粒度越权替换模板。
2. 否则按行政区矩阵匹配省级、地市、区县用户的越权场景。
3. 若信息不足，回退到当前通用文案，但在 `matched_rules` 或 debug context 中保留模板回退标记。

### 3. 时间截断文案保持查询继续执行语义

时间截断仍是修正策略，不是拒绝。文案应表达：

- 原始时间范围部分超出豁免窗口。
- 已截断到合法交集。
- 查询区域保持原请求区域。
- 如果 slots 中有指标，尽量在文案中包含指标；没有指标时省略。

本次不实现“截断比例低于阈值时追加备选按钮”，但可以在设计上保留扩展点。

### 4. 多区域聚合不丢失逐项文案

多区域场景中，每个 `region_results[].correction_text` 应保留单项细分文案。聚合 `PermissionResult.correction_text` 可以拼接自动拆分/展开说明和逐项修正文案，但不得将逐项细分文案降级为“部分区域未查询”一类泛化文本。

### 5. 站点文案保持现状

站点部分越权继续使用：

```text
根据权限，仅展示您可访问的 {count} 个站点数据。如需查看其他区域站点，可查询最近7天数据。
```

站点归属缺失继续使用：

```text
该数据暂不可用。
```

## Risks / Trade-offs

- 矩阵匹配依赖上下文字段完整 → 使用现有 `build_permission_region_context()` 输出作为主输入，缺字段时回退通用文案并记录 debug。
- 文案变长影响最终回复风格 → 中间件继续要求 correction_text 放在回复开头，但文案本身保持一句到两句。
- 多区域聚合文案可能重复 → 聚合时按文本去重，保留逐项 `region_results` 作为详细来源。
- 文档模板中包含 `{value}`，但权限层不知道查询结果 → 权限层文案只生成“修正说明前缀”，最终数据值仍由业务回答补充。
