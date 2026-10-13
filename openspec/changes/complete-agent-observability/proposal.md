## Why

当前六图绑定了Langfuse回调，事件台账也有Trace映射，但回调绑定不能证明主/辅助模型、Skill、MCP与重试都已真实采集，更不能支撑可信的Token及成本报表。需要将运行步骤、实际usage、价格版本和授权查询统一关联到已冻结台账。

## What Changes

- 为六个公共图建立主模型、辅助模型及子任务的逐调用观测，覆盖成功、失败、取消、流式与非流式；保存实际usage和采集完整性。
- 区分模型TTFT、模型调用耗时、浏览器首正文和服务运行耗时；缺失数据保持未知，不凭估算填零。
- 将Skill候选、选中、加载与实际执行分开，将工具逻辑调用与每次MCP/本地工具尝试、重试、降级、权限拒绝及业务无结果分开记录。
- 引入版本化、按生效时间匹配的模型价格和Decimal成本计算，明确币种、缓存/推理子集与未知费用。
- 保留现有Langfuse SDK并通过其支持接口验证真实落库、Trace映射与受授权跳转；观测故障有界降级，业务授权继续失败关闭。
- 交付本人/运营/统计分级的观测查询、usage与成本聚合及可复跑验收证据；不建设完整后台页面、预算告警、评测或能力选择。

## Capabilities

### New Capabilities

- `agent-model-observability`：模型逐调用身份、usage、时间、重试与采集完整性。
- `agent-capability-observability`：Skill选择/执行及MCP/工具逻辑调用与尝试归因。
- `agent-cost-accounting`：价格版本、费用分项、币种和缺失语义。
- `agent-observability-access`：租户隔离的观测回读、聚合、Trace查询与删除边界。

### Modified Capabilities

无。既有`agent-runtime-performance`约束导入、缓存和工具懒初始化，本项遵守它，不改变其要求；已冻结身份与事件台账作为依赖。

## Impact

新增观测包、独立管理端模型/查询服务及迁移；模型/辅助模型、Skill/MCP及六图接线归本项分支所有，身份和台账准入规则不可放宽。契约启用与管理路由登记只做交接表授权的兼容接线。独立分支从本地源码冻结提交9a1781e启动，revision使用`0012_observability`并从`0010`分支，协调者最终与反馈分支合并迁移head。真实Langfuse验证需要当前部署可用；真实企业MCP材料缺失时保持安全禁用并明确生产门禁。
