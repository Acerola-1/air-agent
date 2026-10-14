"""模型调用观测适配：主/辅助模型逐尝试 callback.

设计约束（design §4 / spec agent-model-observability）：

- 优先 LangChain 标准 BaseCallbackHandler（不改变图导出类型——禁止
  with_listeners 包装编译结果，镜像 GraphLoad 会拒绝 RunnableBinding）。
- 每次物理尝试独立 observation_id；同一逻辑调用（重试/空回复重试）共享
  logical_call_id；调用方通过 `model_call_scope` 显式声明逻辑调用。
- usage 归一化：提供商 usage_metadata / response_metadata 实际返回优先；
  缺失保持 measurement_status=usage_missing，不估算补零。
- 流式 usage（末块）与结束回调（on_llm_end）合并去重：同 observation 的
  最终 usage 覆盖式合并，不重复累计（spec：100+20=120 而非 240）。
- TTFT：仅流式且首个可识别模型 Token（text/tool_calls/reasoning 片）可测；
  工具规划有首片类型；非流式 not_collected；不冒充 0。
- 缺可信上下文（无受控受理运行）不猜租户/最新 run：静默跳过采集。
"""

from __future__ import annotations

import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger

from common.observability.collector import WorkItem, get_collector, utcnow
from common.observability.context import (
    ObservationContext,
    new_id,
    observation_context_from_config,
)
from common.observability.store import (
    observability_enabled,
    record_model_observation,
)

# usage 计数字段（缓存/推理是子集，不得加进总量）
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def _merge_usage_sources(sources):
    merged = {}
    invalid = False
    for counts, status in sources:
        invalid |= status == "invalid_usage"
        for key, value in counts.items():
            if key in merged and merged[key] != value:
                invalid = True
            else:
                merged[key] = value
    return (
        merged,
        "invalid_usage" if invalid else normalize_usage(merged)[1],
    )


def normalize_usage(raw: Any) -> tuple[dict[str, int], str]:
    """提取实际计数并核对各来源；冲突不静默选值或重复累计."""
    generations = getattr(raw, "generations", None)
    if generations:
        aggregate = normalize_usage(getattr(raw, "llm_output", None) or {})
        if len(generations) != 1 or len(generations[0]) != 1:
            # 批次缺聚合证据不能用第一条回答冒充整个物理请求。
            return aggregate
        message = getattr(generations[0][0], "message", None)
        return _merge_usage_sources([normalize_usage(message), aggregate])
    if isinstance(raw, dict):
        nested = [
            raw[k] for k in ("token_usage", "usage") if isinstance(raw.get(k), dict)
        ]
        if nested:
            return _merge_usage_sources([normalize_usage(value) for value in nested])
        candidate = raw
    else:
        metadata = getattr(raw, "response_metadata", None) or {}
        candidates = [getattr(raw, "usage_metadata", None)]
        candidates += [metadata.get(k) for k in ("token_usage", "usage")]
        return _merge_usage_sources(
            [normalize_usage(value) for value in candidates if isinstance(value, dict)]
        )
    if not isinstance(candidate, dict) or not candidate:
        return {}, "usage_missing"
    mapping = {
        "input_tokens": ("input_tokens", "prompt_tokens", "input"),
        "output_tokens": ("output_tokens", "completion_tokens", "output"),
        "total_tokens": ("total_tokens",),
        "cache_read_tokens": (
            "cache_read_tokens",
            "input_token_details.cache_read",
            "prompt_tokens_details.cached_tokens",
        ),
        "cache_write_tokens": ("cache_write_tokens", "input_token_details.cache_write"),
        "reasoning_tokens": (
            "reasoning_tokens",
            "output_token_details.reasoning",
            "completion_tokens_details.reasoning_tokens",
        ),
    }
    result = {}
    invalid = False
    for key, names in mapping.items():
        for name in names:
            value = candidate
            for part in name.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                invalid = True
                break
            if key in result and result[key] != value:
                invalid = True
            else:
                result[key] = value
    if invalid:
        return result, "invalid_usage"
    if not result:
        return {}, "usage_missing"
    if (
        "input_tokens" in result
        and result.get("cache_read_tokens", 0) + result.get("cache_write_tokens", 0)
        > result["input_tokens"]
    ):
        return result, "invalid_usage"
    if (
        "output_tokens" in result
        and result.get("reasoning_tokens", 0) > result["output_tokens"]
    ):
        return result, "invalid_usage"
    if (
        all(k in result for k in ("input_tokens", "output_tokens", "total_tokens"))
        and result["total_tokens"] != result["input_tokens"] + result["output_tokens"]
    ):
        return result, "invalid_usage"
    return result, "measured"


@dataclass
class _AttemptState:
    """一次物理尝试的进程内状态（TTFT/usage 合并）."""

    ctx: ObservationContext
    observation_id: str
    logical_call_id: str
    role: str
    model: str
    provider: str | None
    retry_index: int
    run_id: str = ""
    langfuse_trace_id: str = ""
    langfuse_observation_id: str | None = None
    started_monotonic: float = 0.0
    started_at: Any = None
    ttft_ms: int | None = None
    first_piece_kind: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    usage_status: str = "not_collected"
    usage_source: str | None = None
    ended: bool = False
    parent_observation_id: str | None = None


class ModelObservabilityHandler(BaseCallbackHandler):
    """LangChain 标准 LLM 回调：不改变导出类型，仅采集.

    绑定方式：把 handler 实例放进运行 config 的 `callbacks`（LangChain 会向
    子可运行继承）。DeepAgents 图与节点流图都能收到 on_llm_start/on_llm_end/
    on_llm_error。无可信观测上下文的运行（未受控入口/离线直调）不采集。
    """

    raise_error = False

    def __init__(
        self,
        ctx: ObservationContext,
        *,
        langfuse_handler: Any = None,  # noqa: ANN401 - 同 config 的旧 CallbackHandler
    ) -> None:
        """绑定运行观测上下文；可选持有旧 Langfuse handler 引用."""
        self._ctx = ctx
        self._langfuse_handler = langfuse_handler
        self._lock = threading.Lock()
        self._by_run: dict[str, _AttemptState] = {}
        self._logical_attempts: dict[str, int] = {}
        register_observation_handler(self)

    # ------------------------------------------------------- 逻辑调用声明

    def begin_logical_call(self, role: str, model: str) -> str:
        """声明一个逻辑调用，返回 logical_call_id（重试共享）."""
        return new_id()

    # ------------------------------------------------------- LangChain 回调

    def _model_name(self, serialized: dict[str, Any] | None, run: str = "") -> str:
        """从 serialized kwargs 提取模型名（缺省 unknown）."""
        if serialized and isinstance(serialized.get("kwargs"), dict):
            for key in ("model", "model_name", "model_id"):
                value = serialized["kwargs"].get(key)
                if isinstance(value, str) and value:
                    return value
        return "unknown"

    def _provider_of(self, serialized: dict[str, Any] | None) -> str | None:
        if not serialized:
            return None
        identifier = str(serialized.get("id") or "")
        if "openai" in identifier:
            return "openai-compatible"
        if "anthropic" in identifier:
            return "anthropic"
        return identifier.rsplit(".", 1)[-1] or None

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """记录一次物理尝试的开始（LangChain 回调）."""
        run_key = str(run_id)
        state = self._start_attempt(
            serialized,
            run_key,
            tags=tags,
            run_metadata=metadata or {},
            invocation_params=kwargs.get("invocation_params"),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
        )
        if state is None:
            return

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """兼容非 chat 模型的开始回调."""
        state = self._start_attempt(
            serialized,
            str(run_id),
            tags=kwargs.get("tags"),
            run_metadata=kwargs.get("metadata") or {},
            invocation_params=kwargs.get("invocation_params"),
            parent_run_id=str(kwargs["parent_run_id"])
            if kwargs.get("parent_run_id")
            else None,
        )
        if state is None:
            return

    def _start_attempt(
        self,
        serialized: dict[str, Any] | None,
        run_key: str,
        *,
        tags: list[str] | None,
        run_metadata: dict[str, Any] | None,
        invocation_params: dict[str, Any] | None,
        parent_run_id: str | None = None,
    ) -> _AttemptState | None:
        if not observability_enabled():
            return None
        metadata = dict(run_metadata or {})
        if not metadata:
            metadata = (invocation_params or {}).get("metadata") or {}
        # 逻辑调用上下文通过 invocation metadata 注入（见 model_call_scope）；
        # 缺声明时以 run 自身作为独立逻辑调用（仍逐尝试可审计）。
        logical_call_id = str(metadata.get("obs_logical_call_id") or uuid.uuid4())
        role = str(metadata.get("obs_role") or "other")
        declared = _MODEL_CALL_DECLARATION.get()
        if declared is not None:
            role, logical_call_id = declared
        if role not in {
            "main",
            "normalizer",
            "summarizer",
            "subagent",
            "cleanup",
            "other",
        }:
            role = "other"
        if "internal-summary" in (tags or []):
            role = "summarizer"
        parent_observation = _MODEL_PARENT.get()
        with self._lock:
            retry_index = self._logical_attempts.get(logical_call_id, 0)
            self._logical_attempts[logical_call_id] = retry_index + 1
            if len(self._logical_attempts) > 1000:
                self._logical_attempts.pop(next(iter(self._logical_attempts)))
        model = self._model_name(serialized)
        provider = self._provider_of(serialized)
        state = _AttemptState(
            ctx=self._ctx,
            observation_id=new_id(),
            logical_call_id=logical_call_id,
            role=role,
            model=model,
            provider=provider,
            retry_index=retry_index,
            run_id=run_key,
            started_monotonic=time.monotonic(),
            started_at=utcnow(),
            parent_observation_id=parent_observation,
        )
        # 旧 Langfuse handler 在其 on_llm_end 会清理 _runs 条目（先于本 handler
        # 的结束回调），因此 trace id 必须在开始回调时捕获。
        state.langfuse_trace_id = _find_langfuse_trace_id(run_key) or (
            _find_langfuse_trace_id(parent_run_id) if parent_run_id else ""
        )
        for ref in list(_LANGFUSE_HANDLERS):
            handler = ref()
            entry = (
                handler._runs.get(uuid.UUID(run_key)) if handler is not None else None
            )
            if entry is not None:
                state.langfuse_observation_id = (
                    str(getattr(entry, "id", "") or "") or None
                )
        with self._lock:
            self._by_run[run_key] = state

        async def started_write():
            await record_model_observation(
                state.ctx,
                observation_id=state.observation_id,
                logical_call_id=state.logical_call_id,
                role=state.role,
                model=state.model,
                provider=state.provider,
                status="started",
                started_at=state.started_at,
                measurement_status="not_collected",
                retry_index=state.retry_index,
                parent_observation_id=state.parent_observation_id,
            )

        get_collector().enqueue(WorkItem("model_started", state.ctx, started_write))
        return state

    def on_llm_new_token(
        self,
        token: str,
        *,
        run_id: Any = None,
        chunk: Any = None,
        **kwargs: Any,
    ) -> None:
        """首个可识别模型 Token → TTFT（流式；只记一次）."""
        state = self._state_for(str(run_id))
        if state is None:
            return
        message = getattr(chunk, "message", chunk)
        usage, usage_status = normalize_usage(message)
        if usage_status != "usage_missing":
            state.usage = usage
            state.usage_status = usage_status
            state.usage_source = "provider_stream_cumulative"
        if state.ttft_ms is not None:
            return
        additional = getattr(message, "additional_kwargs", None) or {}
        tools = (
            getattr(message, "tool_call_chunks", None)
            or getattr(message, "tool_calls", None)
            or additional.get("tool_calls")
        )
        reasoning = additional.get("reasoning_content") or additional.get("reasoning")
        if not token and not tools and not reasoning:
            return
        kind = "tool_calls" if tools else "reasoning" if reasoning else "text"
        state.ttft_ms = int((time.monotonic() - state.started_monotonic) * 1000)
        state.first_piece_kind = kind

    def _state_for(self, run_key: str) -> _AttemptState | None:
        """按 run_id 取尝试状态（缺省 None）."""
        with self._lock:
            return self._by_run.get(run_key)

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        """成功结束：usage 归一化合并 + 终态写入（LangChain 回调）."""
        state = self._state_for(str(run_id))
        if state is None or state.ended:
            return
        state.ended = True
        generations = getattr(response, "generations", None)
        message = getattr(generations[0][0], "message", None) if generations else None
        metadata = getattr(message, "response_metadata", None) or {}
        actual_model = metadata.get("model_name") or metadata.get("model")
        if isinstance(actual_model, str) and 0 < len(actual_model) <= 128:
            state.model = actual_model
        if message is not None:
            key = (
                state.ctx.tenant_id,
                state.ctx.attempt_id,
                state.ctx.submission_id or "",
            )
            with _TOOL_PARENT_LOCK:
                for call in getattr(message, "tool_calls", None) or []:
                    _TOOL_PARENTS[(*key, str(call.get("id") or ""))] = (
                        state.observation_id
                    )
                while len(_TOOL_PARENTS) > 1000:
                    _TOOL_PARENTS.pop(next(iter(_TOOL_PARENTS)))
        usage, status = normalize_usage(response)
        if status in {"measured", "invalid_usage"}:
            if state.usage:
                # 结束回调以最终修订优先（覆盖，不累计）
                state.usage.update({k: v for k, v in usage.items() if v is not None})
            else:
                state.usage = usage
            state.usage_status = status
            state.usage_source = "provider_final"
        elif state.usage_status == "not_collected":
            state.usage_status = "usage_missing"
        self._finish(state, status="succeeded", usage_override=state.usage)
        self._queue_trace_link(state)

    def on_llm_error(
        self, error: BaseException, *, run_id: Any = None, **kwargs: Any
    ) -> None:
        """失败结束：保留部分 usage 事实（LangChain 回调）."""
        state = self._state_for(str(run_id))
        if state is None or state.ended:
            return
        state.ended = True
        # 失败尝试可能已有部分 usage（网关中断）：保留为已测部分
        self._finish(
            state,
            status="failed",
            usage_override=state.usage or None,
            usage_status="measured" if state.usage else "usage_missing",
        )
        self._queue_trace_link(state)

    def _finish(
        self,
        state: _AttemptState,
        *,
        status: str,
        usage_override: dict[str, int] | None,
        usage_status: str | None = None,
    ) -> None:
        duration_ms = int((time.monotonic() - state.started_monotonic) * 1000)
        ctx = state.ctx
        collector = get_collector()
        started_at = state.started_at
        ended_at = utcnow()

        async def _write() -> None:
            await record_model_observation(
                ctx,
                observation_id=state.observation_id,
                logical_call_id=state.logical_call_id,
                role=state.role,
                model=state.model,
                provider=state.provider,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                ttft_ms=state.ttft_ms,
                first_piece_kind=state.first_piece_kind,
                usage=usage_override,
                measurement_status=usage_status or state.usage_status,
                usage_source=state.usage_source,
                retry_index=state.retry_index,
                parent_observation_id=state.parent_observation_id,
            )

        collector.enqueue(WorkItem(kind="model_observation", ctx=ctx, fn=_write))
        with self._lock:
            self._by_run.pop(state.run_id, None)

    def _queue_trace_link(self, state: _AttemptState) -> None:
        """把同 config 内旧 Langfuse CallbackHandler 的真实 trace 写入本地映射.

        LangChain 用同一 run_id 依次调用所有 handler；旧 handler 在
        `_runs[run_id].trace_id` 保存其创建的真实 Langfuse trace id
        （events_only 部署读路径在 ClickHouse，映射以该 id 为准）。
        拿不到就跳过——未确认同步不冒充已落库（spec）。
        """
        trace_id = state.langfuse_trace_id or _find_langfuse_trace_id(state.run_id)
        if not trace_id:
            return
        collector = get_collector()
        ctx = state.ctx

        async def _write() -> None:
            from common.observability.store import record_trace_link

            try:
                result = await record_trace_link(
                    ctx,
                    trace_id=str(trace_id),
                    observation_id=state.observation_id,
                    remote_observation_id=state.langfuse_observation_id,
                    primary=state.role == "main",
                )
                logger.bind(observability=True).info(
                    "TRACE_LINK result={} trace={}",
                    result.get("reason"),
                    str(trace_id)[:12],
                )
            except Exception as exc:  # noqa: BLE001
                logger.bind(observability=True).warning(
                    "TRACE_LINK failed: {} {}", type(exc).__name__, "write_failed"
                )
                raise

        collector.enqueue(WorkItem(kind="trace_link", ctx=ctx, fn=_write))

    # 供测试注入 started_at 的钩子
    def inject_started(self, run_id: str, started_at: Any) -> None:
        """测试辅助：注入精确 started_at（默认用单调时钟推导）."""
        state = self._state_for(run_id)
        if state is not None:
            state.started_at = started_at


class model_call_scope:
    """声明一个逻辑模型调用的观测上下文（上下文管理器）.

    用法（节点内显式声明）：

        with model_call_scope(config, role="main") as scope:
            response = await model.ainvoke(messages, config=scope.config_for(...))

    通过 invocation metadata 传递逻辑调用 ID / 角色 / 父观测（非秘密标识）；
    这些键进入回调可见的 metadata，不会进入模型请求体。
    """

    def __init__(self, config: dict[str, Any] | None, *, role: str) -> None:
        """记录宿主 config 与模型角色，生成逻辑调用 ID."""
        self._config = config or {}
        self.logical_call_id = new_id()
        self.role = role

    def __enter__(self) -> model_call_scope:
        """返回自身（logical_call_id 已在构造时生成）."""
        self._token = _MODEL_CALL_DECLARATION.set((self.role, self.logical_call_id))
        return self

    def __exit__(self, *exc: Any) -> None:  # noqa: ANN002
        """无资源需要释放（观测写入走队列）."""
        _MODEL_CALL_DECLARATION.reset(self._token)
        return None

    def runtime_metadata(self) -> dict[str, str]:
        """返回随 ainvoke config.metadata 传递的非秘密观测键."""
        """供 ainvoke/aattach 的 config["metadata"] 合并的非秘密观测键."""
        return {
            "obs_logical_call_id": self.logical_call_id,
            "obs_role": self.role,
        }


# Langfuse 旧 CallbackHandler 弱引用注册表（graph 绑定时注册；trace id 反查用）
_LANGFUSE_HANDLERS: list[weakref.ref[Any]] = []
_LANGFUSE_LOCK = threading.Lock()


def register_langfuse_handler(handler: Any) -> None:
    """with_langfuse_tracing 绑定时注册旧 CallbackHandler（弱引用，去重）."""
    if not any(ref() is handler for ref in _LANGFUSE_HANDLERS):
        _LANGFUSE_HANDLERS.append(weakref.ref(handler))


def _find_langfuse_trace_id(run_id: str) -> str:
    """按 run_id 反查旧 handler 的真实 Langfuse trace id（找不到返回空）."""
    for ref in list(_LANGFUSE_HANDLERS):
        handler = ref()
        if handler is None:
            continue
        try:
            entry = handler._runs.get(uuid.UUID(run_id))
            if entry is not None:
                trace_id = str(getattr(entry, "trace_id", "") or "")
                if trace_id:
                    return trace_id
        except Exception:  # noqa: BLE001
            continue
    return ""


def ensure_model_observability(config: dict[str, Any] | None) -> None:
    """执行期注入：把观测回调**原地**放进节点/中间件拿到的运行 config.

    后台运行的 config 由平台从库重建，auth 期注入的 Python 对象不跨进程存活；
    LangGraph 节点与 deepagents 中间件拿到的 config 是执行期真实对象，
    原地扩展后同节点的模型调用经 ambient contextvar 继承回调。
    重复调用幂等（同类型 handler 已存在即跳过，防重复计数）。
    """
    if not observability_enabled() or not isinstance(config, dict):
        return
    ctx = observation_context_from_config(config)
    if ctx is None:
        return
    callbacks = config.get("callbacks")
    from langchain_core.callbacks import BaseCallbackManager

    def same(handler):
        old = handler._ctx
        return (
            old.tenant_id,
            old.user_id,
            old.attempt_id,
            old.submission_id,
            old.platform_run_id,
        ) == (
            ctx.tenant_id,
            ctx.user_id,
            ctx.attempt_id,
            ctx.submission_id,
            ctx.platform_run_id,
        )

    if isinstance(callbacks, BaseCallbackManager):
        existing = [
            cb for cb in callbacks.handlers if isinstance(cb, ModelObservabilityHandler)
        ]
        if existing and all(same(cb) for cb in existing):
            return
        updated = callbacks.copy()
        for cb in existing:
            updated.remove_handler(cb)
        updated.add_handler(ModelObservabilityHandler(ctx), inherit=True)
        config["callbacks"] = updated
        return
    callbacks = [
        cb
        for cb in (callbacks or [])
        if not isinstance(cb, ModelObservabilityHandler) or same(cb)
    ]
    if not any(isinstance(cb, ModelObservabilityHandler) for cb in callbacks):
        callbacks.append(ModelObservabilityHandler(ctx))
    config["callbacks"] = callbacks


def attach_run_observability(value: dict[str, Any]) -> None:
    """Auth 钩子（threads.create_run）收口注入：把观测回调挂进运行 config.

    失败开放：观测注入的任何异常不阻断运行创建（业务准入仍由台账裁决）。
    回调随运行 config 继承到全部嵌套模型/子任务调用（主 + 辅助），
    不改变图导出对象类型（无 with_listeners 包装）。
    """
    try:
        if not observability_enabled():
            return
        ctx = observation_context_from_config(value)
        if ctx is None:
            return
        base = value.get("kwargs")
        if not isinstance(base, dict):
            base = {}
            value["kwargs"] = base
        config = base.get("config")
        if not isinstance(config, dict):
            config = {}
            base["config"] = config
        callbacks = config.get("callbacks")
        updated = attach_observation_callbacks(
            {"callbacks": callbacks if isinstance(callbacks, list) else []}, ctx
        )
        config["callbacks"] = updated.get("callbacks") or []
    except Exception as exc:  # noqa: BLE001 - 观测注入失败开放
        logger.bind(observability=True).warning(
            "observability attach failed (non-blocking): {}",
            type(exc).__name__,
        )


def attach_observation_callbacks(
    config: dict[str, Any] | None, ctx: ObservationContext
) -> dict[str, Any]:
    """把 ModelObservabilityHandler 注入运行 config（返回新 config）.

    若已有同类型 handler（同一运行的嵌套调用）则复用，不叠加（防重复计数）。
    """
    if not observability_enabled():
        return config or {}
    base = dict(config or {})
    from langchain_core.callbacks import BaseCallbackManager

    original = base.get("callbacks")
    callbacks = (
        original.copy()
        if isinstance(original, BaseCallbackManager)
        else list(original or [])
    )
    handlers = (
        callbacks.handlers if isinstance(callbacks, BaseCallbackManager) else callbacks
    )

    def scope_key(c):
        return (
            c.tenant_id,
            c.user_id,
            c.attempt_id,
            c.submission_id,
            c.platform_run_id,
        )

    same = False
    for cb in list(handlers):
        if not isinstance(cb, ModelObservabilityHandler):
            continue
        if scope_key(cb._ctx) == scope_key(ctx) and not same:
            same = True
        elif isinstance(callbacks, BaseCallbackManager):
            callbacks.remove_handler(cb)
        else:
            callbacks.remove(cb)
    if not same:
        handler = ModelObservabilityHandler(ctx)
        if isinstance(callbacks, BaseCallbackManager):
            callbacks.add_handler(handler, inherit=True)
        else:
            callbacks.append(handler)
    base["callbacks"] = callbacks
    return base


def invocation_config(
    config: dict[str, Any], *, role: str, logical_call_id: str | None = None
) -> dict[str, Any]:
    """显式传递可信上下文及回调，不依赖辅助调用的ambient配置是否继承."""
    result = dict(config)
    ensure_model_observability(result)
    result["metadata"] = {
        **(result.get("metadata") or {}),
        "obs_role": role,
        "obs_logical_call_id": logical_call_id or new_id(),
    }
    return result


_OBSERVATION_HANDLERS: list[weakref.ref[Any]] = []
_OBSERVATION_LOCK = threading.Lock()


def register_observation_handler(handler):
    """弱引用注册实际运行回调，不使已结束请求的状态持续积累."""
    with _OBSERVATION_LOCK:
        _OBSERVATION_HANDLERS[:] = [
            ref for ref in _OBSERVATION_HANDLERS if ref() is not None
        ]
        _OBSERVATION_HANDLERS.append(weakref.ref(handler))
        del _OBSERVATION_HANDLERS[:-1000]


def notify_langfuse_trace(run_id, trace_id, remote_observation_id=None):
    """SDK创建真实generation之后通知观察者，消除回调顺序竞争."""
    with _OBSERVATION_LOCK:
        handlers = [ref() for ref in _OBSERVATION_HANDLERS]
    for handler in handlers:
        if handler is not None:
            state = handler._state_for(str(run_id))
            if state is not None:
                state.langfuse_trace_id = str(trace_id)
                state.langfuse_observation_id = (
                    str(remote_observation_id) if remote_observation_id else None
                )


_MODEL_CALL_DECLARATION: ContextVar[tuple[str, str] | None] = ContextVar(
    "observation_model_call_declaration", default=None
)
_MODEL_PARENT: ContextVar[str | None] = ContextVar(
    "observation_model_parent", default=None
)
_TOOL_PARENTS: dict[tuple[str, str, str, str], str] = {}
_TOOL_PARENT_LOCK = threading.Lock()


@contextmanager
def model_tool_scope(config, tool_call_id):
    """工具内辅助模型关联实际发出工具调用的模型，忽略客户端parent声明."""
    ctx = observation_context_from_config(config)
    parent = None
    if ctx:
        with _TOOL_PARENT_LOCK:
            parent = _TOOL_PARENTS.get(
                (
                    ctx.tenant_id,
                    ctx.attempt_id,
                    ctx.submission_id or "",
                    str(tool_call_id),
                )
            )
    token = _MODEL_PARENT.set(parent)
    try:
        yield
    finally:
        _MODEL_PARENT.reset(token)
