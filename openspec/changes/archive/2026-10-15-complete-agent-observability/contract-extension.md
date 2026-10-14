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


## 实现版确认（匿名运行04，2026-10-13）

- 五类型已按本文件字段启用（发布契约 + 包内副本 + TS 生成副本一致，哈希随实施更新）。
- `runtime.model_usage` 实际写入字段：attempt_id/observation_id/model/provider/role/logical_call_id/submission_id/measurement_status/usage_source + 六个 token 计数（可负场景不写）；`state_revision` 用于幂等修订。
- 窄例外实现：禁止键扫描跳过"runtime 来源 + runtime.model_usage + 顶层六键"，随后专用判定（非 bool 非负整数），字符串/bool/负数/浮点/嵌套键照常拒绝；新增原因码 `usage_tokens_not_non_negative_int` 已登记 samples.reason_codes。
- `runtime.span` 实际写入：span_kind(skill)/span_name/stage(matched|selected|loaded|executed)/status/duration_ms/skill_id/selection_source。
- `runtime.tool_call` 实际写入：logical_call_id/tool_name/service_key/retry_index/status(八分类)/duration_ms/fallback_kind/error_category；无参数/结果字段。
- 价格操作契约：`POST /agent/observability/prices`（重叠 409 + 审计）、`POST /agent/observability/prices/recalculate`（幂等，限 attempt 或时间窗）；权限 `agent:pricing:manage`。
- 样本：33 个 case（含 12 个本项新增正/反例）；validator 三份 + 两副本全部一致测试通过。
