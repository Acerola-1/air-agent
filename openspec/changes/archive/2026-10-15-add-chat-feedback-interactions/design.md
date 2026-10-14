## Context

冻结源码9a1781e，业务迁移0010；身份、RLS、request/attempt/submission/answer和永久删除标记已经实现。`agent_answers`记录message_id和content_version；`GET /agent/requests/{id}`可回读回答。浏览器三种预留事件目前全部拒绝，受理函数直接插入且没有完成启用后的关联/重复处理，不能只改enabled开关。`ChatMessage`目前没有answer_id；复制按钮没有await剪贴板。动机见proposal，行为见三个spec。

## Goals / Non-Goals

目标：先实现一条真实回答→确认映射→评价/复制→PG回读的完整链路，再扩充计时、停止阶段和统计。前后端沿用既有组件、中文语气、输入法和停止确认行为。非目标：重构聊天状态架构、生成新后台页面、开启runtime类型、评价答案正确性或任务解决率。

## Decisions

### 1. 独立反馈域与同事务事实

新建`models_feedback.py`、`schemas_feedback.py`、`services/feedback.py`、`routers/feedback.py`及`frontend/lib/feedback/`、`components/chat/feedback/`。当前评价表唯一键(tenant_id,user_id,answer_id)，包含rating、revision、updated_at；历史操作ID幂等，反馈与answer/attempt采用同租户复合关联。意见正文独立受限存储，不写公共payload。事件/outbox遵守同一事务；只通过授权的最小浏览器分派调用反馈服务，不复制身份系统或owner请求连接。

新增revision=`0011_feedback`，down_revision=`0010`；分支中单独升级验证。Change 5的0012_observability也从0010分支；集成者新增merge revision，不把对方迁移当成本分支前置。后续附加迁移用`0011f_*`避免撞号，不改0001–0010。

### 2. 写接口与并发协议

使用现有`POST /agent/events/browser`受理browser.feedback/copy/timing。feedback payload拟扩充`rating`(positive/negative/none)、`reason_code`、`comment_ref`、`expected_revision`、`client_operation_id`；意见正文由专用反馈PUT端点同事务保存，不设独立临时上传流程，不留下孤儿意见。使用`PUT /agent/feedback/answers/{answer_id}`，携event_id、expected_revision、rating和可选reason/comment；统一核心服务保证两条受理途径不会各写一次。反馈历史的received/updated用服务端时间，浏览器occurred_at仅作低信任操作时间。

`GET /agent/feedback/answers?answer_ids=...`本人回读最多50个回答，响应含answer/message/attempt/content_version、当前rating/reason/revision及是否可评价。新增`GET /agent/feedback/operations`需agent:operations:read，分页最多200；默认无意见正文，读取意见另需agent:content:read。`GET /agent/feedback/metrics`需agent:metrics:read。事件ID相同内容重复复用结果，不同内容冲突；CAS先按当前修订号校验，响应丢失重试先查操作幂等结果，不能先CAS导致合法重投409。首次修订0，成功修改+1；并发冲突回读，无自动覆盖。

### 3. 回答与消息映射

发送/刷新时从台账获取权威回答映射；结合tenant/thread/request/attempt/message_id/content_version匹配，不能按数组下标或最近attempt配对。只有确认映射才开放评价/统计复制。历史存在多版但平台UI折叠时，不擅自重做对话呈现；测试必须明确实际显示消息对应哪版。新增client数据字段可选以兼容旧历史，无归属历史显示无法评价。优先在新反馈client包处理回读/映射，chat-store仅保留必要ID和生命周期接线。

### 4. 客户端体验与有界采集

点击同评级再次点击可撤销；点踩原因可选：answer_incorrect、irrelevant、incomplete、slow、other；不同原因与修改通过明确保存提交。等待显示保存中，失败不显示已保存，保留意见；重试使用原操作ID。键盘、aria-pressed、焦点恢复遵守UX-CONTRACT。复制等待clipboard Promise成功，失败提示不计事件；用户复制成功但埋点未确认时不谎称复制失败。

采集队列单页最多100条，每批50/256KiB、重试最多3次，切换企业/用户立即丢弃旧队列，不跨身份persist；复制不存正文。计时用performance.now提交起点和真正正文DOM渲染后的可见时刻，首正文/完成独立，rAF回调绑定generation。页面重开新测量ID，不跨页面相减；旧history回读不新建测量。未来漂移最多5分钟，迟到允许但限制到真实运行时间范围及配置保留期。

### 5. 指标与状态保持

默认按确认回答的cohort展示当前有效评价/复制；按操作时间的活动趋势另命名。启用时间之前无采集的历史另列not_collected。满意率positive/(positive+negative)，覆盖率rated/eligible，复制率distinct_copied/eligible_copyable，返回分子分母、as_of/lag/version。计时由有效明细算分位数。停止阶段只增加本次UI事实，复用既有stop-intent和navigation，不将pagehide或onDone推断为cancelled，不放宽stopPending。

### 6. 并行文件权限

遵守handoff及`docs/handoffs/parallel-03-05.md`。本项独占聊天页面/store/浏览器client，Change 5不修改这些文件。管理main.py仅追加反馈router，ledger.py仅浏览器事件分派，基础ledger.py服务仅保留适配入口；新增反馈事务逻辑放独立服务。契约只能启用本项browser三类型和对应局部样本，更新生成副本；runtime预留仍拒绝。公共改动在交付报告逐项列出，由协调者合并，不自行合并另一分支。

## Risks / Trade-offs

- 消息归一化会合并助手消息 → 使用权威ID验证，歧义禁用，不猜测。
- 复制事实来自不可信浏览器 → 服务端验证归属、关联、字段、时间和幂等；指标标client_observed，不承诺证明物理复制。
- 多标签页并发 → CAS及幂等优先，提示回读后重新选择。
- 启用登记容易让旧插入路径半完成 → 混合批次/重复/删除/事务故障作为必须反例。
- 外部环境缺失 → 能完成代码和受控测试，但真实浏览器/PG未验证不得勾对应任务或声称通过。

## Migration Plan

先隔离PG从零升级到0011_feedback，再在0010已有样本上升级，验证非owner RLS、复合约束、旧数据和重复写。新配置默认保持禁用，部署迁移和服务后再开启browser三类；关闭采集时保留既有事实，前端反馈显示不可用。回滚通过关开关、回退应用兼容版本，已采集事实不DROP；删除/备份恢复演练沿用永久标记，汇总不复活意见。
