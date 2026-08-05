## Context

当前权限抽取模型经历了从单行政区到多行政区的增量演进：旧字段 `province/city/district/target_level` 仍表达一个区域，而新增 `region_entities` 表达多个区域。这会造成两个问题：

- 多区域问题里旧字段只能放一个值，容易被调用方误读为最终查询区域。
- 聚合后的 `allowed_regions` 能表达最终可执行区域列表，但缺少每个请求区域从原文、解析、权限校验到最终查询区域的逐项链路。

权限规则引擎本身仍适合保持“一次校验一个标准区域”的职责。需要调整的是 slots、权限中间件编排、结果 schema 和主流程消费契约。

## Goals / Non-Goals

**Goals:**

- 将 slots 行政区主语义统一为 `regions` 列表，单区域也使用一项列表表达。
- 删除或废弃 `province/city/district/target_level` 作为主输入语义，避免多区域时单值字段误导。
- 每个区域请求项独立携带 `text`、`level_hint`、`province_hint`、`city_hint`、`source` 和可选 `role`。
- 中间件按区域项循环调用 `resolve_region_scope`，再循环构造 `PermissionFacts` 并调用 `check_permission()`。
- 权限结果保留 `region_results`，让主流程可以分别处理每个城市或区县的允许、替换、拒绝和时间截断。
- `permission_query_overrides.regions` 继续作为最终可执行查询区域列表。

**Non-Goals:**

- 不改变确定性权限规则矩阵和时间豁免规则。
- 不改业务数据查询 SQL 或统计逻辑。
- 不在本变更中重写所有 skill 的业务查询实现；只更新权限上下文契约和主流程提示要求。

## Decisions

### 1. 使用 `regions[]` 作为唯一权威区域输入

`PermissionExtractedSlots` SHALL 以列表字段表达行政区请求：

```json
{
  "need_check": true,
  "region_mode": "multi_explicit",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "province_hint": "河南省",
      "city_hint": null,
      "source": "explicit",
      "role": "query"
    },
    {
      "text": "平顶山市",
      "level_hint": "city",
      "province_hint": "河南省",
      "city_hint": null,
      "source": "explicit",
      "role": "query"
    }
  ]
}
```

原因：

- 单区域、多区域和混合层级区域可以使用同一种结构。
- 每项都有自己的层级提示和消歧上下文，避免一个全局 `target_level` 无法表达“郑州市和金水区”的问题。
- `source` 支持解释和审计：区分用户显式输入、系统自动补全、集合展开和权限替换。

旧字段可在短期保留为兼容派生输出，但中间件 SHALL NOT 将其作为权威输入。

### 2. 集合查询显式表达父级与目标子级

集合/下钻查询不应把父级城市直接塞进 `regions` 当作最终查询项。建议结构：

```json
{
  "region_mode": "collection",
  "regions": [
    {
      "text": "郑州市",
      "level_hint": "city",
      "source": "explicit",
      "role": "collection_parent",
      "child_level": "district"
    }
  ]
}
```

中间件解析父级后展开 children，并把展开出的区县作为实际权限校验项。展开失败 SHALL 生成可解释的拒绝或部分拒绝，不得用父级汇总替代。

### 3. 规则引擎保持单区域输入，中间件负责循环编排

不把多个区域塞进一个 `PermissionFacts`。中间件 SHALL 对每个标准区域执行：

1. 根据区域项的 `text` 和 hints 调 `resolve_region_scope`。
2. 构造单个 `PermissionFacts`。
3. 调用 `check_permission()`。
4. 将结果转换为一条 `region_results[]`。

原因：

- 保持规则引擎职责清晰和现有规则复用。
- 支持每个区域产生不同权限结果，例如允许、替换、拒绝或时间截断。
- 便于测试和审计。

### 4. 新增逐项 `region_results`

`PermissionResult` SHALL 增加逐项结果：

```json
{
  "region_results": [
    {
      "requested": {"text": "郑州市", "source": "explicit", "level_hint": "city"},
      "resolved": {"name": "郑州市", "level": "city", "data_type": "summary"},
      "status": "allowed",
      "query_region": {"name": "郑州市", "level": "city", "data_type": "summary"},
      "permission_result": {"fix_strategy": "", "permitted": true},
      "correction_text": ""
    }
  ],
  "allowed_regions": [],
  "rejected_regions": []
}
```

聚合字段仍保留，用于快速执行查询；逐项字段用于解释、审计和复杂主流程处理。

### 5. 主流程消费分层

主流程 SHALL：

- 使用 `permission_query_overrides.regions` 决定实际查询区域列表。
- 使用 `permission_result.region_results` 生成面向用户的逐项说明。
- 在存在 `regions` 时不得读取旧单值 `region` 或旧 slots 字段来覆盖区域列表。

## Risks / Trade-offs

- [Risk] 旧调用方仍依赖 `province/city/district/target_level` → Mitigation: 提供短期派生兼容字段并在提示词中声明不得作为权威输入，测试覆盖旧单区域路径。
- [Risk] LLM 输出的 `regions` 列表遗漏区域或层级错误 → Mitigation: 中间件仍使用规则提取和后缀推断兜底，逐项调用 `resolve_region_scope` 做可信标准化。
- [Risk] `region_results` 结构过大影响上下文长度 → Mitigation: 仅保留必要字段，原始 MCP 大对象放入 bounded debug context 或日志。
- [Risk] 混合层级区域导致业务工具无法一次查询 → Mitigation: 主流程按 `query_region.level` 分组或逐项调用工具，不能静默丢弃任何允许项。

## Migration Plan

1. 新增 `PermissionRegionRequest`、`RegionPermissionResult` 等模型，更新 slots prompt 输出 `regions[]`。
2. 中间件先读新 `regions[]`，旧字段只作为短期 fallback。
3. 将当前多区域 planner 改为基于区域项列表，不再从单值 slots 推导主语义。
4. 更新 `PermissionResult`、`permission_query_overrides` 和 `<permission_context>`。
5. 更新主流程提示词和受影响测试。
6. 在所有旧字段依赖迁移完成后，删除旧字段或将其标记为 deprecated-only。

## Open Questions

- 是否需要在本变更中完全删除旧字段，还是先保留一版 deprecated 兼容输出？
- 集合展开是否继续使用本地 helper，还是同时要求 MCP 提供正式 `list_region_children`？
- 业务工具是否已经支持混合层级 `regions` 一次传入；若不支持，主流程是否需要按区域逐项调用？
