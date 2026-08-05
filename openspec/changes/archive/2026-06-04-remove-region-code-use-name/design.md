## Context

当前权限审查链路中，两个 MCP 工具 `get_user_profile` 和 `resolve_region_scope` 都返回 `code` 字段（如 `41b6dc401`），但这个 `code` 在 Python 侧没有任何实际用途：

1. **`infer_region_level_from_code`** 期望的是标准 6 位行政区划编码（如 `410000`=省, `410100`=市, `410105`=区），但 MCP 返回的是内部 ID（如 `41b6dc401`），导致层级推断错误
2. **`_same_province` / `_same_city`** 比较时优先用 `code`，但 `code` 不可比，实际降级到了 `name` 比较
3. **`context_city_code` 传参错误**：中间件把用户画像中的 `city.code`（商丘市）传给了 `resolve_region_scope`，而不是查询行政区的父级城市 code，导致"商丘市 + 金水区"这种错误组合

同时，`resolve_region_scope` 的消歧参数 `context_city_code` / `context_province_code` 设计不合理，应该用中文名消歧而非内部 code。

## Goals / Non-Goals

**Goals:**
- 删除 `get_user_profile` 返回中的 `code` 字段，仅保留 `name` 和 `level`
- 删除 `resolve_region_scope` 入参中的 `context_city_code` / `context_province_code`，替换为 `city` / `province`（中文名）
- 删除 `resolve_region_scope` 返回中的 `code` 字段，仅保留 `name` 和 `level`
- Python 侧所有行政区比较和消歧逻辑改为基于 `name`
- 更新 `docs/权限P0工具需求.md` 文档

**Non-Goals:**
- 不改标准行政区划编码体系（GB/T 2260），只是不在接口中暴露
- 不改动权限规则引擎的核心判定逻辑（放行/拒绝/替换策略不变）
- 不涉及用户认证、会话管理等其他模块

## Decisions

### D1: 删除 `code` 字段，使用 `name` 作为唯一标识

**选择**：在 MCP 工具接口层面删除 `code` 字段，仅保留 `name` 和 `level`。

**理由**：
- `name` 在同层级下是唯一的（中国没有同名同级的行政区）
- `level` + `name` 组合可以唯一确定一个行政区
- 消歧时传入父级 `city` / `province` 的中文名即可

**替代方案**：保留 `code` 但改为标准 6 位行政区划编码。拒绝原因：服务端改动更大，且标准编码需要维护映射表。

### D2: `resolve_region_scope` 消歧参数改为中文名

**选择**：删除 `context_city_code` / `context_province_code`，新增 `city` / `province`（可选，中文名）。

**理由**：
- 消歧的语义更清晰："我要查的是郑州市的金水区，不是南昌市的金水区"
- 避免内部 ID 泄露到客户端
- 减少服务端维护 code 映射表的复杂度

**新接口**：
```json
{
    "query": "金水区",
    "targetLevel": "district",
    "city": "郑州市",
    "province": "河南省"
}
```

### D3: Python 侧 `_same_province` / `_same_city` 改为基于 `name` 比较

**选择**：删除 `code` 比较逻辑，直接使用 `province_name` / `city_name` 比较。

**理由**：
- `code` 已删除，无法比较
- `name` 在同层级下唯一，比较结果等价
- 代码更简洁，无需处理 `code` 为空的情况

## Risks / Trade-offs

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 服务端 `RegionCache` 需要支持按名称查找 | 中 | `RegionCache.getByName()` 已存在，只需确保性能 |
| 重名区县消歧依赖 `city`/`province` 参数 | 低 | 客户端（Python 侧）已能获取用户画像中的 `city`/`province` name |
| 历史数据兼容 | 低 | `code` 字段从未在 Python 侧实际使用，删除不影响 |
| 测试用例需要更新 | 低 | 批量替换 `code` 为 `name` 即可 |

## Migration Plan

1. **服务端（Java）**：
   - 修改 `get_user_profile` 返回，删除 `code` 字段
   - 修改 `resolve_region_scope` 入参，替换 `context_city_code`/`context_province_code` 为 `city`/`province`
   - 修改 `resolve_region_scope` 返回，删除 `code` 字段
   - 部署到测试环境

2. **Python 侧**：
   - 修改 `mcp_tools.py` 中的调用参数
   - 修改 `region.py` 中的比较逻辑
   - 修改 `engine.py` 中的相关逻辑
   - 修改 `permission_classify_middleware.py` 中的调用逻辑
   - 更新测试用例

3. **文档**：
   - 更新 `docs/权限P0工具需求.md`

4. **验证**：
   - 测试"郑州市金水区"能否正确解析
   - 测试"商丘市用户查询金水区"能否正确消歧
   - 测试权限规则引擎的正确性

## Open Questions

1. `resolve_region_scope` 工具内部是否已有按名称查找的能力？是否需要服务端额外开发？
2. 如果用户查询的行政区不在用户画像的省份/城市下，消歧参数是否还需要传？
