## Context

当前权限链路已经形成三层边界：

```text
PermissionExtractedSlots
  -> RegionRequestPlan
  -> PermissionFacts
  -> check_permission()
  -> PermissionResult
  -> permission_query_overrides
```

LLM 只负责抽取 `PermissionExtractedSlots`；MCP 工具提供可信事实；`check_permission()` 是唯一权限判定者。现有实现已覆盖时间豁免、时间截断、行政区自动补全、行政区替换、多区域拆分、集合/下钻区域展开和最终上下文注入。

站点查询当前主要由 `station-extreme` Skill 执行业务查询。权限方案要求站点数据继承站点所属行政区权限，但现有权限事实只包含用户问题中的目标行政区，不包含站点归属行政区。因此站点查询需要在规则引擎前补充“站点归属事实”，再复用现有行政区权限规则。

站点类型包括标准站、国控、省控、市控站、乡镇站、微站、TVOC站、粉尘站、高密度站。它们是查询筛选条件，不是权限类型。

## Goals / Non-Goals

**Goals:**

- 让权限槽位抽取识别站点查询、站点名称列表和站点类型列表；行政区继续由现有 `regions`、`region_mode`、`PermissionRegionRequest` 表达。
- 调用站点归属工具获取标准化站点信息和自然语言行政区归属信息。
- 支持单城市站点列表查询、具体站点查询、多个城市站点查询和父级下辖子区域站点查询。
- 复用当前时间豁免和行政区规则，避免另建站点权限规则。
- 对多站点结果进行可访问站点裁剪，并通过 `permission_query_overrides` 传递给后续查询。
- 对站点归属缺失或无效的结果 fail closed，提示该数据暂不可用。

**Non-Goals:**

- 不实现站点类型权限。
- 不要求权限层使用或暴露行政区 code。
- 不改造站点业务查询工具本身的数据查询逻辑；只定义权限前置解析和后续覆盖参数。
- 不在本次实现网格权限；网格可沿用同样模式另起变更。

## Decisions

### 1. 站点类型作为筛选条件，不进入权限矩阵

站点权限主体是站点归属行政区。`station_types` 仅用于站点归属工具过滤候选站点，也用于后续业务查询保留用户意图。

替代方案是为国控、省控等类型定义额外权限规则。该方案没有文档依据，会扩大权限模型复杂度，且容易和行政区继承规则冲突。

### 2. 扩展 LLM 槽位，而不是新建独立站点解析 Agent

在 `PermissionExtractedSlots` 中新增：

- `data_scope`: `region | station`
- `station_names`: `list[str]`
- `station_types`: `list[str]`

不重新定义 `regions`。当前 `PermissionExtractedSlots.regions` 已是 `list[PermissionRegionRequest]`，是权限链路的权威行政区输入；`province/city/district/target_level` 仅保留为兼容字段。站点查询必须复用 `build_region_request_plan()` 生成的 `RegionRequestPlan`，避免站点逻辑绕过多区域、集合展开和最小化消歧规则。

替代方案是让主业务 Skill 先解析站点，再回填权限层。但权限审查发生在主模型调用前，放到 Skill 后会让越权站点有机会进入后续查询计划，不符合 fail closed。

### 3. 站点归属工具合约使用自然语言字段，但区域来源必须来自区域计划

站点归属工具由业务侧实现，权限层通过 MCP 封装调用。入参建议：

```json
{
  "region": "郑州市",
  "station_names": ["水利监测站"],
  "station_types": ["国控站"]
}
```

`region` 是本次工具调用的限定区域，来源必须是 `RegionRequestPlan` 中已解析或自动补全后的查询区域名称，不能直接从原始 `province/city/district` 字段拼接。`station_names` 为空且 `station_types` 非空时表示区域下站点列表查询。

出参建议：

```json
{
  "found": true,
  "stations": [
    {
      "station_name": "水利监测站",
      "station_type": "国控站",
      "region": {
        "name": "金水区",
        "level": "district",
        "city": "郑州市",
        "province": "河南省"
      }
    }
  ]
}
```

权限层不依赖 code，但要求工具返回标准化官方名称。若 `region` 为空或缺少 `name/level/city/province`，该站点视为归属无效。

### 4. 站点归属解析复用 RegionRequestPlan

对“郑州市和洛阳市下国控站 AQI 排名”：

```text
slots.regions = [
  PermissionRegionRequest(text="郑州市", level_hint="city"),
  PermissionRegionRequest(text="洛阳市", level_hint="city"),
]
slots.station_types = ["国控站"]

plan = build_region_request_plan(question, slots)
for item in plan.items:
    resolved_region = resolve_region_scope(item.query, ...)
    resolve_station_scope(region=resolved_region.region.name, station_names=[], station_types=["国控站"])
```

每次调用只传一个区域，避免站点工具内部处理复杂组合。中间件合并所有返回站点，并记录每个站点的来源区域、来源 `RegionRequestItem` 和归属行政区。

对 `collection` 模式，必须先按现有逻辑展开父级下辖子区域，再对展开后的子区域逐项调用站点归属工具；不得在无法展开子区域时退回父级汇总站点查询。

对 `auto_fill` 模式，必须先使用用户绑定行政区构造可信默认区域，再以该自动补全区域调用站点归属工具，并在权限结果中保留 `auto_filled` 语义。

站点归属工具调用不得重新引入用户画像作为行政区消歧默认值。区县、城市和省级的消歧上下文应沿用当前行政区解析规则：区县可带 city/province，城市可带 province，省级不带 city。

### 5. 规则引擎复用行政区上下文判断每个站点

时间判定仍先执行：

- `full_window`：直接放行，不裁剪站点。
- `partial`：时间截断，保留原站点集合。
- `none`：对每个站点的归属行政区调用当前行政区上下文判断。

站点归属行政区需要转换为现有 `requested_region` 兼容结构：

```json
{
  "found": true,
  "ambiguous": false,
  "region": {"name": "金水区", "level": "district"},
  "city": {"name": "郑州市"},
  "province": {"name": "河南省"}
}
```

这样可以直接使用 `build_permission_region_context()` 判断是否可访问。

站点分支只使用站点工具返回的归属行政区做权限判断。若工具返回的站点归属缺失，不能用用户问题中的查询区域、自动补全区域或来源 `RegionRequestItem` 兜底。

### 6. PermissionResult 增加可执行站点覆盖

当前结果已有 `accessible_station_count`，但没有可执行站点列表。需要新增字段表达后续查询必须使用的站点集合，例如：

```json
{
  "station_overrides": [
    {"station_name": "水利监测站", "station_type": "国控站"}
  ],
  "accessible_station_count": 1,
  "total_station_count": 3
}
```

`permission_query_overrides` 应把 `station_overrides` 透传给主流程。最终 Skill 后续查询必须使用该集合，不得继续按原始区域扩大站点范围。

## Risks / Trade-offs

- 站点工具返回归属不标准 → 权限层将该站点视为不可用，并记录 `station_region_missing` 或 `station_region_invalid`。
- 多城市站点列表导致工具调用次数增加 → 中间件按区域串行或有界并发调用，单次问题的区域数量可设置上限，超限时 fail closed 或要求用户缩小范围。
- LLM 漏抽站点类型或站点名称 → 单区域有行政区时仍可做区域级权限；但站点列表裁剪依赖站点工具返回，缺少必要过滤可能导致站点集合过大。提示词和单测需要覆盖常见站点类型。
- 站点权限裁剪后排序语义改变 → 最终回答必须告知仅展示可访问站点，避免用户误认为是全量排名。
- 后续业务 Skill 未遵守 `station_overrides` → 权限上下文必须明确强制规则，并新增中间件测试确保覆盖参数被注入。
