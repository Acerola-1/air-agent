# Change 3 接口候选

这是已授权的兼容扩展方案，实施者将实际字段/OpenAPI/合法非法样本写回本文件，不能以本方案代替实现。

| 面 | 最小契约 |
|---|---|
| PUT /agent/feedback/answers/{answer_id} | event_id、expected_revision(初次0)、rating positive/negative/none、可选reason_code和comment<=1000字符；权威revision/当前评价/重复标记 |
| GET /agent/feedback/answers | answer_ids最多50；仅本人同租户已确认回答；message/attempt/content_version、rating/reason/revision、是否可评价 |
| GET /agent/feedback/operations | agent:operations:read；有界分页<=200、游标绑定tenant/filter；无意见原文 |
| GET /agent/feedback/operations/{operation_id}/comment | 运营权限+agent:content:read；跨租户/删除拒绝；本人可在本人反馈回读接口读取自己意见，不扩大其他原文权限 |
| GET /agent/feedback/metrics | agent:metrics:read；confirm_answer队列，当前评价/复制和浏览器计时，分子分母/采集覆盖 |
| browser.feedback | 服务端写事件可复用现有信封；payload answer_id/rating/reason_code/comment_ref/expected_revision/client_operation_id；comment_ref仅非正文标识 |
| browser.copy | payload answer_id/copy_scope(full)/client_operation_id；clipboard成功才投递 |
| browser.timing | payload attempt_id/measurement_id/first_visible_answer_ms/display_complete_ms/clock(monotonic)/progress_seen/completion_state；未测值null且有状态 |

评级写入首选专用PUT，服务内生成browser.feedback并调用同一幂等事务，不再额外投递一次。浏览器通用端点可受理无意见的同类命令并走同一服务；任一路径相同event_id不会双写。意见正文由专用PUT同事务保存，不设额外临时意见上传流程。feedback只允许当前本人确认回答；browser timing的attempt与运行、thread、request由服务端复核，信封里重复标识不得自相矛盾。

未来时钟最多5分钟漂移；事件迟到不跨运行合法起点及保留边界。计时不得跨刷新合并performance.now。逐项拒绝可用reason/code列出invalid_association、deleted、revision_conflict、event_conflict、invalid_timing，不泄露另一个企业存在性。采集开关启用时间是事实，历史not_collected不重写。

生成副本、基础旧样本与源声明的变动必须全部进入公共diff清单；冻结source commit仍可读取原件比较。Change 5的runtime启用状态不可抢先修改。
