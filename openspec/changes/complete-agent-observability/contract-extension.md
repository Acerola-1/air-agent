# Change 5 接口候选

本文件是实现前契约方案，实际字段/白名单/枚举、价格操作方式及样本须实施后补齐。所有事实关联可信tenant/user/membership/thread/request/attempt/submission/platform run，不接受客户端自由覆盖。

| 面 | 最小契约 |
|---|---|
| GET /agent/observability/runs/{attempt_id} | 本人或agent:operations:read；无正文；本次run/子调用/Trace/采集覆盖 |
| GET /agent/observability/observations | agent:operations:read，过滤user/graph/skill/service/tool/model/version/time；limit<=200，游标tenant/filter绑定 |
| GET /agent/observability/metrics | agent:metrics:read；Token、模型时间、Skill/工具、币种成本小计/缺失、版本/截止/lag |
| GET /agent/observability/traces/{trace_id} | 先本地检查归属+运营权限，再可信代理读取白名单；无共享控制台旁路 |
| 价格管理与重算 | agent:pricing:manage；实现可选管理API或受控CLI，必须文档化输入/结果/错误/审计，不能默认调用owner宽写 |
| runtime.model_usage | attempt_id/model/provider/observation_id/logical_call_id/submission_id/role、input_tokens/output_tokens/total_tokens/cache_read_tokens/cache_write_tokens/reasoning_tokens、measurement_status/usage_source |
| runtime.ttft | attempt_id/observation_id/ttft_ms/clock(monotonic)/first_piece_kind/measurement_status；不可测单独缺失记录，不编造0事件 |
| runtime.cost | attempt_id/observation_id/amount(十进制字符串)/currency/price_ref/calculation_revision/measurement_status；未知不伪造amount=0，可无cost事实另由观测缺失标识 |
| runtime.span | attempt_id/observation_id/parent_observation_id/span_kind/span_name/stage/status/duration_ms/skill_id/skill_version/selection_source；Skill阶段matched/selected/loaded/executed分别记录 |
| runtime.tool_call | attempt_id/observation_id/logical_call_id/tool_name/service_key/tool_call_id/retry_index/status/duration_ms/skill_id/fallback_kind/error_category；无原始参数/结果 |

字段都采用显式白名单及类型；nullable/required按“已采集事实与缺失状态”分别建样本，不能只开enabled。numeric usage的token键例外严格限制类型/runtime来源/顶层与具体event_type，同时修src/common/event_ledger/contract.py、admin/api/app/services/event_contract.py和TS副本；字符串、bool、负数、嵌套secret继续拒绝。

推荐观测状态started/succeeded/failed/cancelled/timed_out/unknown；工具结果success/empty_result/validation_rejected/permission_denied/technical_error/timed_out/cancelled/unknown。终态证据不因迟到started回退；工具逻辑call只有一次、重试有多个attempt。价格分项与币种为版本化配置，实际模型别名必须有证据；未知实际模型不猜价。

事实与事件/outbox同事务；缺失不会生成伪usage/cost。更新样本时保留原基础非法用例，启用预留类型只改本域预留断言为明确集合。反馈browser类型不可抢先开启。默认Langfuse旧callback自动input/output采集也必须受脱敏约束，只有真实回读才可声称已落库。
