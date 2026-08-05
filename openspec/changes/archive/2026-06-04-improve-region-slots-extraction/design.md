## Context

当前权限审查链路中，`PermissionExtractedSlots` 只提取 `region_text` 一个字段（如"金水区"）。在调用 `resolve_region_scope` 时，消歧参数 `city`/`province` 来自用户画像（用户绑定的城市/省份），而非用户问题中实际提到的城市/省份。

这导致跨市查询时出现问题：
- 用户是商丘市的，问"郑州市金水区的空气质量"
- `region_text` = "金水区"
- 消歧参数 `city` = "商丘市"（来自用户画像）
- MCP 工具可能返回商丘市的金水区（如果存在），而非用户想查的郑州市金水区

## Goals / Non-Goals

**Goals:**
- 让 LLM 分别提取省、市、区县三级行政区名称
- 消歧参数使用用户问题中提到的城市/省份，而非用户绑定的城市/省份
- 支持跨市、跨省查询的正确消歧

**Non-Goals:**
- 不改动 MCP 工具接口（`resolve_region_scope` 的入参不变）
- 不改动权限规则引擎的核心判定逻辑
- 不增加 LLM 的调用次数

## Decisions

### D1: 用 `province`/`city`/`district` 替代 `region_text`

**选择**：删除 `region_text`，新增 `province`/`city`/`district` 三个可选字段。

**理由**：
- 消歧参数直接使用 LLM 提取的值，不依赖用户画像
- 三级字段比单字段更清晰，避免解析歧义
- 与 MCP 工具入参对齐

**替代方案**：保留 `region_text`，增加 `parent_city`/`parent_province` 字段。拒绝原因：字段冗余，且 `region_text` 的解析逻辑复杂。

### D2: LLM 提示词要求分别提取三级行政区

**选择**：提示词中明确要求 LLM 分别输出 `province`/`city`/`district`。

**示例**：
```json
{
  "province": "河南省",
  "city": "郑州市",
  "district": "金水区"
}
```

**规则**：
- 只提取用户问题中**明确提到**的行政区
- 如果用户只提到"金水区"，则 `city=null`，后续使用用户画像消歧
- 如果用户提到"郑州市金水区"，则 `city="郑州市"`，`district="金水区"`

## Risks / Trade-offs

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM 提取准确率下降 | 中 | 增加示例和规则说明，测试验证 |
| 向后兼容性 | 低 | `region_text` 直接删除，测试用例同步更新 |
| 用户只提到区县名时无法消歧 | 低 | 此时回退到用户画像消歧，与现有逻辑一致 |

## Migration Plan

1. 修改 `PermissionExtractedSlots` 模型
2. 修改 LLM 提示词
3. 修改 `_resolve_requested_region` 调用逻辑
4. 修改 `build_permission_facts`
5. 更新测试用例
6. 运行测试验证
