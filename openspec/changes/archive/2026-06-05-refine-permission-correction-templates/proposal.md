## Why

当前权限修正文案主要使用通用模板，无法按权限方案中的用户层级、越权场景、时间粒度和站点裁剪场景进行差异化说明。手工测试和实际链路排查时，用户只能看到“因数据权限限制已调整查询区域”这类粗粒度提示，难以理解为何替换、替换到哪里、是否可以通过查询最近 7 天获取原区域数据。

## What Changes

- 按《AI智能问数权限方案设计-260521.md》中的行政区修正策略矩阵细分 `correction_text`。
- 为省级、地市、区县用户的典型越权场景生成不同修正文案，包括区县上卷、外省上卷、本省其他区县替换、外省替换、省级汇总替换、外市替换。
- 为月/年粒度越权替换生成专门文案，明确月/年统计数据不支持跨行政区查看。
- 优化时间截断文案，表达“查询日期范围部分超出豁免窗口”，并尽量保留原请求区域、指标和合法时间范围。
- 保留现有站点部分越权和站点归属缺失文案：部分越权提示“仅展示可访问站点”，归属缺失提示“该数据暂不可用”。
- 增加可单测的修正文案 builder，避免在规则引擎中继续拼接粗粒度字符串。

## Capabilities

### New Capabilities

- 无

### Modified Capabilities

- `permission-classify-middleware`: 权限上下文注入和最终拒绝回复需要使用细分后的修正文案，不得覆盖或降级为通用文案。
- `permission-result-schema`: 权限结果需要携带足够的修正文案上下文或直接输出矩阵化 `correction_text`，供主流程和最终回复使用。

## Impact

- 影响 `src/common/permission/engine.py` 中时间截断、行政区替换、硬拒绝和站点裁剪相关文案生成。
- 可能影响 `src/common/permission/region.py` 暴露的确定性行政区上下文字段，若现有字段不足以判断修正类型，需要补充或复用已有 `user_region`、`requested_region`、`replacement_region`。
- 影响 `src/common/middleware/permission_classify_middleware.py` 中权限上下文注入和 `_rejection_message()` 的用户可见文案。
- 需要更新权限规则、权限中间件和结果模型相关单元测试，覆盖文档矩阵中的核心修正场景。
