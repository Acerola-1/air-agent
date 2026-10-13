/** 由 frontend/scripts/gen-agent-events-contract.mjs 生成，勿手改。
 * 事实源：docs/contracts/agent-events-v1/{event-registry,states}.json */

import type { EventRegistry, StatesContract } from './contract';

export const EVENT_REGISTRY: EventRegistry = {
  "contract": "agent-events",
  "major_version": 1,
  "schema_version": "1.0",
  "status": "freeze_candidate",
  "updated_at": "2026-10-13T00:00:00Z",
  "produced_by": "anonymous-02/add-agent-event-ledger",
  "notes": "冻结候选：协调者独立验收通过前不得视为已冻结。第 3/5 项不得据此提前并行改共享文件。",
  "sources": {
    "runtime": {
      "trust": "server",
      "description": "Agent 运行进程（含六图 lifecycle 适配与补偿 worker）的可信服务端事实",
      "authentication": "运行票据/内部凭据，浏览器不可获得",
      "may_claim_terminal_facts": true
    },
    "server": {
      "trust": "server",
      "description": "台账服务/受控受理入口的自身事实（管理员后台与内部端点）",
      "authentication": "内部端点凭据或统一会话",
      "may_claim_terminal_facts": true
    },
    "system": {
      "trust": "server",
      "description": "补偿校准、重放与清理工具产生的系统事实",
      "authentication": "运维权限 + 审计",
      "may_claim_terminal_facts": true
    },
    "browser": {
      "trust": "client",
      "description": "浏览器交互扩展（反馈/复制/浏览器计时）；v1 全部类型预留未启用",
      "authentication": "统一会话 + 活跃成员",
      "may_claim_terminal_facts": false
    }
  },
  "envelope": {
    "required": [
      "schema_version",
      "event_id",
      "event_type",
      "occurred_at",
      "source",
      "tenant_id",
      "user_id",
      "membership_id",
      "payload"
    ],
    "optional": [
      "received_at",
      "thread_id",
      "request_id",
      "attempt_id",
      "run_id",
      "answer_id",
      "message_id",
      "revision"
    ],
    "server_overrides_client": [
      "schema_version",
      "source",
      "tenant_id",
      "user_id",
      "membership_id",
      "received_at"
    ],
    "forbidden_payload_key_patterns": [
      "password",
      "password_hash",
      "passwd",
      "token",
      "ticket",
      "credential",
      "secret",
      "authorization",
      "cookie",
      "api_key",
      "apikey",
      "private_key"
    ],
    "limits": {
      "single_event_bytes": 16384,
      "browser_batch_events": 50,
      "browser_batch_bytes": 262144,
      "server_single_event_bytes": 65536
    }
  },
  "event_types": {
    "request.accepted": {
      "enabled": true,
      "sources": [
        "server",
        "runtime"
      ],
      "family": "request",
      "payload_fields": [
        "graph_id",
        "operation_kind",
        "submission_digest_prefix",
        "user_message_id",
        "admitted_at"
      ],
      "required_payload_fields": [
        "graph_id",
        "operation_kind"
      ],
      "summary": "可信受理：逻辑提问被接受并建立尝试（不含正文）"
    },
    "run.admission_confirmed": {
      "enabled": true,
      "sources": [
        "runtime",
        "server",
        "system"
      ],
      "family": "run",
      "payload_fields": [
        "platform_run_id",
        "admission_outcome",
        "admission_unknown_reason",
        "admitted_at"
      ],
      "required_payload_fields": [
        "platform_run_id",
        "admission_outcome"
      ],
      "summary": "平台运行创建结果确认（confirmed 或 admission_unknown）"
    },
    "run.state_observed": {
      "enabled": true,
      "sources": [
        "runtime",
        "server",
        "system"
      ],
      "family": "run",
      "payload_fields": [
        "state",
        "evidence_source",
        "platform_status",
        "state_revision",
        "cancel_requested",
        "transport_connected",
        "waiting_kind"
      ],
      "required_payload_fields": [
        "state",
        "evidence_source",
        "state_revision"
      ],
      "summary": "带证据与修订号的主状态观测；乱序/重复不得静默覆盖更强证据"
    },
    "run.cancel_requested": {
      "enabled": true,
      "sources": [
        "runtime",
        "server",
        "system"
      ],
      "family": "run",
      "payload_fields": [
        "origin",
        "phase",
        "platform_run_id",
        "requested_at"
      ],
      "required_payload_fields": [
        "origin",
        "phase"
      ],
      "summary": "停止意图（独立于实际终态）；来源与已知阶段必须显式"
    },
    "run.resumed": {
      "enabled": true,
      "sources": [
        "runtime",
        "server",
        "system"
      ],
      "family": "run",
      "payload_fields": [
        "resume_index",
        "platform_run_id",
        "resumed_at"
      ],
      "required_payload_fields": [
        "resume_index",
        "platform_run_id"
      ],
      "summary": "人工恢复操作；不增加逻辑提问，平台运行映射追加一条"
    },
    "answer.confirmed": {
      "enabled": true,
      "sources": [
        "runtime",
        "server"
      ],
      "family": "answer",
      "payload_fields": [
        "answer_id",
        "message_id",
        "content_version",
        "confirmed_at"
      ],
      "required_payload_fields": [
        "answer_id",
        "content_version"
      ],
      "summary": "用户可见最终正文版本确认；内部推理/工具输出/草稿不得使用"
    },
    "answer.partial": {
      "enabled": true,
      "sources": [
        "runtime",
        "server"
      ],
      "family": "answer",
      "payload_fields": [
        "answer_id",
        "message_id",
        "content_version",
        "observed_at"
      ],
      "required_payload_fields": [
        "answer_id",
        "content_version"
      ],
      "summary": "已展示但未确认的部分正文"
    },
    "trace.linked": {
      "enabled": true,
      "sources": [
        "runtime",
        "server"
      ],
      "family": "trace",
      "payload_fields": [
        "provider",
        "project_scope",
        "trace_id",
        "primary",
        "link_source"
      ],
      "required_payload_fields": [
        "provider",
        "trace_id",
        "primary"
      ],
      "summary": "Trace 映射（非秘密逻辑标识）；主 Trace 选择依据可审计"
    },
    "resource.deleted": {
      "enabled": true,
      "sources": [
        "server",
        "system"
      ],
      "family": "resource",
      "payload_fields": [
        "resource_type",
        "resource_id",
        "deletion_state",
        "deleted_at"
      ],
      "required_payload_fields": [
        "resource_type",
        "resource_id",
        "deletion_state"
      ],
      "summary": "会话/原文删除标记；删除优先于投影重建与重投"
    },
    "browser.feedback": {
      "enabled": false,
      "sources": [
        "browser"
      ],
      "family": "browser",
      "reserved_for": "change-3",
      "payload_fields": [
        "answer_id",
        "rating",
        "reason_code",
        "comment_ref"
      ],
      "required_payload_fields": [
        "answer_id",
        "rating"
      ],
      "summary": "[预留] 回答点赞/点踩；第 3 项实施"
    },
    "browser.copy": {
      "enabled": false,
      "sources": [
        "browser"
      ],
      "family": "browser",
      "reserved_for": "change-3",
      "payload_fields": [
        "answer_id",
        "copy_scope"
      ],
      "required_payload_fields": [
        "answer_id"
      ],
      "summary": "[预留] 复制埋点；第 3 项实施"
    },
    "browser.timing": {
      "enabled": false,
      "sources": [
        "browser"
      ],
      "family": "browser",
      "reserved_for": "change-3",
      "payload_fields": [
        "attempt_id",
        "first_visible_answer_ms",
        "clock",
        "progress_seen"
      ],
      "required_payload_fields": [
        "attempt_id",
        "clock"
      ],
      "summary": "[预留] 浏览器首可见终答正文耗时（performance.now 单调时钟）；第 3 项实施"
    },
    "runtime.model_usage": {
      "enabled": false,
      "sources": [
        "runtime"
      ],
      "family": "observability",
      "reserved_for": "change-5",
      "payload_fields": [
        "attempt_id",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens"
      ],
      "required_payload_fields": [
        "attempt_id",
        "model"
      ],
      "summary": "[预留] 模型 usage；第 5 项实施（缓存/推理是 usage 子集，不得重复加到总量）"
    },
    "runtime.ttft": {
      "enabled": false,
      "sources": [
        "runtime"
      ],
      "family": "observability",
      "reserved_for": "change-5",
      "payload_fields": [
        "attempt_id",
        "ttft_ms",
        "clock"
      ],
      "required_payload_fields": [
        "attempt_id",
        "ttft_ms"
      ],
      "summary": "[预留] 单次模型 TTFT；第 5 项实施"
    },
    "runtime.cost": {
      "enabled": false,
      "sources": [
        "runtime"
      ],
      "family": "observability",
      "reserved_for": "change-5",
      "payload_fields": [
        "attempt_id",
        "amount",
        "currency",
        "price_ref"
      ],
      "required_payload_fields": [
        "attempt_id",
        "amount",
        "currency"
      ],
      "summary": "[预留] 成本；缺价格/usage 时不是零；第 5 项实施"
    },
    "runtime.span": {
      "enabled": false,
      "sources": [
        "runtime"
      ],
      "family": "observability",
      "reserved_for": "change-5",
      "payload_fields": [
        "attempt_id",
        "span_kind",
        "span_name",
        "duration_ms"
      ],
      "required_payload_fields": [
        "attempt_id",
        "span_kind"
      ],
      "summary": "[预留] 服务端 span；第 5 项实施"
    },
    "runtime.tool_call": {
      "enabled": false,
      "sources": [
        "runtime"
      ],
      "family": "observability",
      "reserved_for": "change-5",
      "payload_fields": [
        "attempt_id",
        "tool_name",
        "status",
        "duration_ms"
      ],
      "required_payload_fields": [
        "attempt_id",
        "tool_name",
        "status"
      ],
      "summary": "[预留] 工具/MCP 调用观测；第 5 项实施"
    }
  }
} as EventRegistry;

export const RUN_STATES: StatesContract = {
  "contract": "agent-run-states",
  "major_version": 1,
  "status": "freeze_candidate",
  "produced_by": "anonymous-02/add-agent-event-ledger",
  "states": {
    "admitting": {
      "zh": "受理中",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "可信受理事务进行中"
    },
    "admission_unknown": {
      "zh": "受理结果待确认",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "上游可能已受理但未取得确认；不得自动重发"
    },
    "queued": {
      "zh": "排队",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "已确认受理，尚未开始推进"
    },
    "running": {
      "zh": "运行中",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "平台运行推进中"
    },
    "waiting_input": {
      "zh": "等待人工",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "原生中断等待人工输入；不以恢复操作新建提问"
    },
    "succeeded": {
      "zh": "正常完成",
      "terminal": true,
      "counts_as_ended": true,
      "summary": "有可信完成证据"
    },
    "failed": {
      "zh": "失败",
      "terminal": true,
      "counts_as_ended": true,
      "summary": "有可信失败证据"
    },
    "cancelled": {
      "zh": "实际取消",
      "terminal": true,
      "counts_as_ended": true,
      "summary": "平台确认已取消（非仅用户意图）"
    },
    "timed_out": {
      "zh": "可信超时",
      "terminal": true,
      "counts_as_ended": true,
      "summary": "有实际 deadline/平台超时证据"
    },
    "status_unknown": {
      "zh": "状态待确认",
      "terminal": false,
      "counts_as_ended": false,
      "summary": "平台不可用/对象缺失/证据不足；可继续查询，不判成功/取消/超时"
    }
  },
  "evidence_sources": {
    "platform_read": {
      "rank": 3,
      "zh": "受授权平台原生状态回读"
    },
    "runtime_callback": {
      "rank": 2,
      "zh": "可信 runtime 回调"
    },
    "browser_observed": {
      "rank": 1,
      "zh": "浏览器感知（最低，只作补充）"
    }
  },
  "terminal_precedence": {
    "rule": "同一尝试的有效终态由 rank 更高的来源裁定；低 rank 不得覆盖高 rank 终态；冲突保留两侧事实并记录修订。",
    "start_event_after_terminal": "历史事实保留，当前状态不回退为 running"
  },
  "cancel_origins": {
    "user_stop": "用户主动停止",
    "navigation": "页面导航离开",
    "tenant_switch": "企业切换",
    "transport_disconnect": "传输断连（无用户停止动作）",
    "permission_revocation": "权限撤销",
    "unknown": "未知原因，不猜测归因为 user_stop"
  },
  "cancel_phases": {
    "queued": "尚未开始",
    "processing": "运行中",
    "output": "输出中",
    "unknown": "阶段未知"
  },
  "client_state_flags": {
    "stop_requested": "用户已请求停止（意图）",
    "cancel_confirmed": "平台已确认取消（事实）",
    "stop_pending": "停止未确认（前端展示态）",
    "transport_connected": "连接状态，独立于主状态"
  },
  "notes": [
    "waiting_input 与 status_unknown 都不计入已结束分母。",
    "浏览器流结束/onDone/signal.aborted 都不是终态证据。",
    "取消已完成运行平台返回 404；不得把 404 当已终态或已取消。"
  ]
} as StatesContract;
