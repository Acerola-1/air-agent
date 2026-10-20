# 后台查询与轻量反馈 v1

此文件冻结6/7可共同开发的外部协议。路径相对FastAPI；浏览器统一加`/api`，不绕BFF。实施时生成并验证OpenAPI快照，不改旧端点请求体。

## 公共请求与响应

- 只接受当前会话企业，不接受用tenant_id扩大范围；跨企业对象404、缺权限403、身份401、context_changed409、IAM/本地依赖503。校验422，游标非法400。
- `window`默认last_7_days；custom需同时给start/end（aware ISO，start<end），窗口最长90天。today以当前Asia/Shanghai业务日00:00至请求as_of；last_7/30从6/29天前00:00至as_of。规范响应注明`partial_day=true`，增长比较另定义。
- 列表`limit=50`、1..200；`sort`仅端点枚举；游标绑定tenant、主体/可见范围、过滤、as_of、版本及最后稳定键；过期/不匹配400。服务端白名单校验所有维度，SQL值参数化。
- 响应meta含`schema_version=admin-console-v1`、`metric_version`、`as_of`、`window{start,end,timezone,kind,partial_day}`、`filters`、`scope{tenant_id,context_version}`、`projected_at`、`data_lag_seconds`、`status`、`limitations[]`。未使用投影时projected_at=null、measurement_source=direct_sql，不编造水位。
- 每个metric含`value`、`numerator/denominator`（适用时）、`sample_count`、`missing_count`、`measurement_source`、`cohort`、`status=measured|partial|not_collected|unknown|estimated`。真实0由measured+0表达；空分母null。单页`items,next_cursor,has_more,limit`；聚合不是本页相加。

## 路由

| 方法/路径 `/agent/console` 后缀 | 主要响应 | 权限与适用过滤 |
|---|---|---|
| GET `/overview` | 分区metrics、币种小计、采集/积压状态 | metrics；window、business/graph |
| GET `/trends` | 日桶、分区cohort和完整性 | metrics；window、business/graph、metric枚举 |
| GET `/users` | 用户用量/活跃/币种小计列表 | ops+metrics；window、business/graph、user_id |
| GET `/runs` | attempt列表，request/answer映射 | ops；window、business/graph、user_id、state、thread_id |
| GET `/runs/{attempt_id}` | 详情/恢复/版本/选择/观测引用，无正文 | ops；不允许替换归属 |
| GET `/requests/{request_id}/content` | 精确问题快照页及可用状态 | ops+content；offset/limit字符分页，最多8000 |
| GET `/answers/{answer_id}/content` | 确认回答指定版本页、摘要与状态 | ops+content；content_version必填，最多8000字符 |
| GET `/feedback` | 当前有效评价+处理case，不含意见 | ops；window按回答确认时间，rating/reason/case_state |
| GET `/feedback/{answer_id}/history` | 评价/处理修订历史，不含原文 | ops；独立分页 |
| GET `/feedback/{answer_id}/content` | 意见与处理说明精确引用分页 | ops+content；禁止顺便全量预取 |
| POST `/feedback/{answer_id}/actions` | 权威case修订及replayed | ops+feedback:manage；含说明另需content |
| GET `/capabilities` | business/skill/tool组的分页聚合 | metrics；group_by单枚举及适用ID，Skill阶段分列 |
| GET `/capabilities/details` | Skill span/工具逻辑调用/attempt明细 | ops；与上表一致的适用维度 |
| GET `/performance` | 服务、浏览器、模型分布及样本覆盖 | metrics；window、business/graph、model/provider、source枚举 |
| GET `/costs` | 按币种与指定维度分页小计/未知 | metrics；window、business/graph、model/provider、group_by |

v1 `/overview`不接受user_id/model/skill/tool过滤；`/performance`的model/provider仅适用于模型分区，其他分区标`not_applicable`，不沿用筛选后假装同队列。用户维度费用/模型可在costs中按user_id精确过滤；已声明不能应用的组合422。

所有按具体user_id过滤、按用户分组或返回可识别用户标识的接口，除metrics外必须另有ops；包括costs的group_by=user_id，不能借“聚合”绕过users端点限制。只有metrics的账号不能通过任意user_id探测个人用量，返回403；整体匿名去重活跃用户数量仍可读。

反馈actions输入：`operation_id` UUID、`expected_revision`非负、`expected_feedback_revision`非负、`action=start|resolve|reopen`、`note`最多1000字符。新case revision=0可用start或resolve创建；resolve需说明，reopen仅已处理可用。重复同ID同载荷返回原结果；载荷冲突与case旧修订409 `revision_conflict`；用户评价修订变化409 `feedback_changed`，提示先回读。写入全部带Origin与当前企业/ctx请求头，不能被普通业务409触发全局企业切换提示。

正文响应`status=available|not_collected|capture_failed|expired|truncated`、`content_version`、`content_sha256`、`offset`、`next_offset`、`total_chars`、`text`；删除/跨企业404优先。缺原文不能从Langfuse、checkpoint、浏览器旧缓存补造。正文所有响应no-store。

## 7的扩展点

7在`/agent/insights`发布自身端点，沿用meta/错误/窗口/游标语义，版本字段另有analysis_version和assignment_revision；前端导入6的operations共享组件。7后端不导入6的router或拼接6的内部SQL，避免实施顺序成为编译依赖。
