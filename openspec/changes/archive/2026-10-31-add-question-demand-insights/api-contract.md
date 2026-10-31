# 需求洞察 v1 API

复用6的`api-contract.md`通用meta、窗口、错误码和no-store；所有路径前缀`/agent/insights`。页面`/operations/insights`归7，接现有后台而不是新建站点。

| 方法/后缀 | 内容 | 权限 |
|---|---|---|
| GET `/overview` | 覆盖、积压、未归类/失败与最新完整处理截止 | metrics |
| GET `/questions` | 标准问题榜；rank=intents/users/requests | metrics |
| GET `/topics` | 主题榜，request/users直接去重 | metrics |
| GET `/questions/{id}` | 安全概括/趋势/统计/约束，无来源正文 | metrics |
| GET `/questions/{id}/sources` | 精确来源引用和脱敏意图，按需原问题页 | ops+content |
| GET `/unclassified` | 未归类原因/意图与来源 | ops+content |
| GET `/jobs`、`/jobs/{id}` | 同企业作业状态/安全错误/用量，无委托凭据 | ops |
| POST `/jobs` | 增量/指定窗口backfill，显式预算 | ops+content+insights:manage |
| POST `/jobs/{id}/actions` | retry/pause/renew/cancel，期望job修订 | 同上 |
| POST `/questions/{id}/actions` | rename/assign/merge/split，预览或提交 | 同上 |
| POST `/profiles/{id}/rebuild` | 新profile重算，旧版本保持 | 同上 |
| POST `/profiles/{id}/activate` | 校准/覆盖通过后的CAS切换 | 同上 |

所有写入Origin/CSRF与ctx头，ID/范围只能来自本企业。`operation_id`+`expected_revision`必填；同ID异payload或旧修订409 `revision_conflict`，不触发企业切换；ctx冲突单独`context_changed`。模型故障worker状态failed/paused而非HTTP永久等待，创建返回202 job引用。

问题/主题通用过滤：window、business_code、graph_id、action、pollutant、city_code、topic_id、analysis_version、assignment_revision；limit默认50最多200。精确日期slots保留在意图，但任意文本搜索敏感来源不走URL。游标绑定tenant、scope、版本、过滤、rank和as_of；无效/过期400。

榜单每行包含cluster/topic稳定ID、安全中文名、atomic_intent_count、distinct_requests、distinct_users、share{value,numerator,denominator}、previous_count、growth_rate、new、comparison_status、request_level_negative_feedback及评价覆盖。未归类在分母中单独列；不存在的潜在意图不能补0。

增长默认growth_basis=atomic_intent_count，不随rank切换暗改。today的前期窗口为两个边界各减1业务日；last_7_days/last_30_days分别减7/30日（即`[start-N日,end-N日)`），返回comparison_window供核对；自定义窗口默认comparison_status=not_requested，不自作同比。前期覆盖不足优先not_comparable；只有前期真实完整且计数0才能new=true、growth_rate=null。

meta除6通用字段外增加：`schema_version=demand-insights-v1`、analysis_version、assignment_revision、embedding_profile（安全ID，不含endpoint/secret）、source_cutoff、eligible_requests、processed_requests、classified_intents、unclassified_intents、pending/failed/missing_content_requests、coverage、comparable。仅metrics响应不含user/message/request原始ID、原文或向量。

来源正文按内容权限、精确content_version、保留期和墓碑再读取；只返回一页，不预取thread全历史。跨企业/删除404，源过期显示expired且没有旧文本。7不能依赖6回答快照做问题分类，它的来源是用户原问题。

merge提交目标簇ID和源簇expected_revision；split提交预览token、目标名称和全部source intent分配及各revision。预览token绑定tenant/profile/来源集合/修订/过期时间，不能跨scope复用；提交删除或版本冲突重新预览。不会直接执行LLM建议的批量合并。
