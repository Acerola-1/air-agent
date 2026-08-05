## Context

当前权限链路以“单个请求行政区”为核心建模：

- `PermissionExtractedSlots` 只有 `province`、`city`、`district` 三个单值字段。
- `PermissionFacts.requested_region` 表达一个行政区。
- `PermissionResult.allowed_region` 和 `permission_query_overrides.region` 也表达一个行政区。
- 确定性规则引擎一次只校验一个 `requested_region`。

这会导致两类真实问题无法被正确表达：

- 显式多行政区：`2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？`
- 集合/下钻行政区：`2026年3月郑州市下的区县的PM2.5月均值分别是多少？`

这两类问题都应由系统自动拆解并继续执行，而不是提示用户手动拆分。权限链路需要把“用户请求的区域集合”和“最终允许查询的区域集合”完整交给主流程。

## Goals / Non-Goals

**Goals:**

- 从原始问题和 slots 中识别显式多行政区与集合/下钻行政区语义。
- 将显式多行政区拆分为多个区域请求项，并逐项调用行政区解析。
- 将集合/下钻行政区展开为下级区域请求项，例如城市下辖区县。
- 对每个区域请求项独立构造 `PermissionFacts` 并调用确定性规则引擎。
- 聚合逐项权限结果，生成完整权限上下文和查询覆盖参数。
- 主流程继续查询，不要求用户拆分问题；回复中说明自动拆分、展开、替换或时间截断情况。
- 保留单区域和无行政区自动补全的既有兼容行为。

**Non-Goals:**

- 本 change 不修改业务数据查询工具的统计 SQL 逻辑；它只保证主流程拿到正确的区域覆盖参数。
- 本 change 不改变基础权限矩阵和时间豁免规则。
- 本 change 不实现跨区域结果排序、图表展示样式等查询结果层能力。

## Decisions

### 1. 引入区域请求计划，而不是继续依赖单值 slots

中间件新增区域请求规划步骤，输出逻辑结构：

```json
{
  "mode": "multi_explicit | collection | single | auto_fill",
  "source_text": "2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？",
  "items": [
    {"query": "郑州市", "source": "explicit", "requested_level": "city"},
    {"query": "平顶山市", "source": "explicit", "requested_level": "city"}
  ]
}
```

集合/下钻问题先得到父级请求：

```json
{
  "mode": "collection",
  "parent": {"query": "郑州市", "requested_level": "city"},
  "child_level": "district"
}
```

原因：

- slots 的单值字段仍可用于单区域兼容，但不能表达多个独立区域。
- 区域请求计划可以保留用户原始意图，并驱动后续逐项解析、展开和校验。
- 主流程可以根据 `mode` 决定是否在回复中说明“已自动拆分”或“已展开下辖区县”。

### 2. 显式多行政区通过多次调用现有 resolve_region_scope 实现

对于 `郑州市和平顶山市` 这种用户明确列出的区域，中间件 SHALL 提取多个区域文本，并对每一项分别调用现有 `resolve_region_scope`。

消歧上下文沿用现有原则：

- 省级区域不传用户城市上下文。
- 地市区域可传省份上下文。
- 区县区域可传省份和城市上下文；如果用户问题中给出城市上下文，优先使用问题中的城市，而不是用户绑定城市。

这样不需要改 `resolve_region_scope` 工具即可支持显式多区域。

### 3. 集合/下钻行政区必须具备下级展开能力

对于 `郑州市下的区县`、`郑州市各区县`、`平顶山市下辖区县` 等问题，单纯解析父级城市不够，必须展开下级区域列表。

实现优先级：

1. 如果现有本地行政区树在运行环境可用且数据可信，先封装 `list_region_children` helper，输入父级标准区域和 `child_level`，返回下级标准区域列表。
2. 如果生产权限链路要求所有行政区事实来自 MCP，则新增 MCP 工具 `list_region_children`。

建议 MCP 签名：

```json
{
  "parent_query": "郑州市",
  "parent_level": "city",
  "child_level": "district",
  "province": "河南省"
}
```

建议返回：

```json
{
  "found": true,
  "parent": {"name": "郑州市", "level": "city"},
  "children": [
    {"name": "中原区", "level": "district"},
    {"name": "二七区", "level": "district"}
  ]
}
```

验收要求是：集合查询不得按父级城市本级汇总继续执行，必须展开成下级区域列表再逐项校验。

### 4. 逐项权限校验复用现有确定性规则引擎

每个解析后的区域项 SHALL 独立构造 `PermissionFacts` 并调用 `check_permission()`。这样可以复用现有时间豁免、基础行政区权限、行政区替换和时间截断逻辑。

聚合器只负责合并结果，不重新发明权限规则：

- 任一项 `permitted=true` 且无替换，加入 `allowed_regions`。
- 任一项 `fix_strategy="replace_region"`，加入 `corrected_regions`，并把替换后的区域加入实际查询区域。
- 任一项 `fix_strategy="truncate_time"`，记录合法时间范围；如果多个区域返回不同合法时间范围，取交集或按逐项上下文保留，不能静默扩大时间范围。
- 任一项无法解析或硬拒绝，加入 `rejected_regions`；若所有项均拒绝，整体硬拒绝。

### 5. 扩展 PermissionResult 与查询覆盖参数

保持现有单区域字段兼容，同时新增批量上下文。建议字段：

```json
{
  "region_mode": "multi_explicit | collection | single | auto_fill",
  "requested_regions": [],
  "allowed_regions": [],
  "corrected_regions": [],
  "rejected_regions": [],
  "region_corrections": []
}
```

`permission_query_overrides` 新增：

```json
{
  "regions": [
    {"name": "郑州市", "level": "city"},
    {"name": "平顶山市", "level": "city"}
  ],
  "region_mode": "multi_explicit"
}
```

保留 `permission_query_overrides.region` 用于单区域兼容。多区域时主流程 SHALL 使用 `regions`，不得只取第一个区域。

### 6. 用户可见修正文案由聚合器生成

聚合器 SHALL 生成一句或多句可供主流程引用的修正文案，例如：

- `已自动拆分查询区域为：郑州市、平顶山市。`
- `已自动展开郑州市下辖区县作为查询区域。`
- `因权限限制，已将金水区调整为新郑市。`
- `因权限限制，部分区域无权访问，已仅查询有权限的区域：...`

主流程最终回复应说明做过的自动拆分、展开和权限订正，而不是让用户重问。

## Risks / Trade-offs

- 多区域提取漏识别 → 以规则识别常见连接词和行政区后缀为底线，同时扩展 slots prompt 输出多个区域实体。
- 同名区县消歧错误 → 优先使用问题中的省市上下文；缺失时使用用户省份上下文，但不得把用户城市强行传给省级或其他市级查询。
- 集合展开依赖工具或本地行政区树 → 实施时先确认生产可信数据源；没有下级展开能力时不能声称支持集合查询。
- 多区域时间截断结果不一致 → 聚合器必须显式记录逐项合法时间，不得把某一区域的合法时间套用到所有区域。
- 主流程只读取单个 `region` → 系统提示词和查询覆盖参数必须明确要求多区域使用 `regions`。
