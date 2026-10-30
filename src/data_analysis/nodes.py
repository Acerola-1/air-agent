"""data_analysis 节点流的所有节点函数."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from loguru import logger

from common.capabilities import refresh_authorized_mcp_tools
from common.config import config as app_config
from common.data_governance import DataProcessingFailure, invoke_with_data_deadline
from common.model_budget import ContextBudgetExceeded, request_metrics
from common.model_errors import is_context_overflow
from common.models import ModelRegistry
from common.output_contract import visible_result
from common.output_streaming.answer import stream_final_answer
from common.output_streaming.controls import publish_control_message
from common.output_streaming.publisher import (
    OutputPublisher,
    runtime_authorization_probe,
)
from common.runtime_context import get_message_content, get_routing_context
from common.runtime_events import emit_event
from common.runtime_tools import get_business_tools
from data_analysis.context.preparation import (
    ContextRecoveryFailed,
    PreparedRequest,
    merge_prepared_updates,
    prepare_model_request,
)
from data_analysis.context.recovery import call_with_context_recovery
from data_analysis.menu_skill_mapping import match_menu_skill
from data_analysis.prompts import (
    build_system_prompt,
    filter_business_tools,
)
from data_analysis.state import DataAnalysisState

SKILLS_DIR = Path(__file__).parent / "skills"

# 空回复时追加的引导消息，促使模型基于工具结果生成结论
_EMPTY_REPLY_NUDGE = "请基于以上工具返回的数据，直接输出分析结论。"

# 用户可见的服务不可用提示
_SERVICE_UNAVAILABLE_MESSAGE = "抱歉，当前智能问答服务暂时不可用，请稍后再试。"

# 空回复重试仍为空时的兜底消息
_FALLBACK_EMPTY_REPLY = "服务暂不可用，请稍后重试。"

# 最终输出清洗提示词：仅删除过程性信息，不改动业务内容
_FINAL_OUTPUT_CLEANUP_PROMPT = """你是最终答案清理器。你的任务只是在下方"待清理正文"中删除过程性信息和内部实现信息，除此之外必须原样保留正文。

必须删除的内容包括：
- 工具调用计划、执行进度、失败重试、下一步动作、内部分析过程。
- skill、工具、函数、参数、JSON、SQL、路径、ID、middleware、LangGraph、MCP、ToolMessage、AIMessage、字段名等内部实现信息。
- "我需要/我正在/接下来/已完成/让我/根据技能/调用某工具"等过程化自述。
- 工具错误消息、状态码（如 502、500）、超时提示（如 timeout）、失败原因（如 API 返回错误）等不可原样输出或转述。

必须保留的内容包括：
- 原有业务结论、依据、关键数据、风险提示和建议。
- 原有 Markdown 标题、列表、表格、段落顺序、日期、数值、单位、排序和专有名词。
- 已生成的正文结构和表达风格。
- 权限修正说明（如"因数据权限限制，已为您调整..."等），这些说明是用户必须知晓的重要信息，不可删除。

禁止行为：
- 不得重构、扩写、压缩、总结、重排、翻译、美化或改变业务正文。
- 不得补充新事实、新判断、新建议或新数据。
- 不得解释你做了哪些清理。
- 不得删除权限修正说明。

如果待清理正文已经干净，必须原样输出。
只输出清理后的最终正文，不要输出任何说明。

待清理正文：
{answer}
""".strip()


def _load_allowed_tools(skill_file: Path) -> list[str]:
    """读取单个 Skill 的 allowed-tools 配置."""
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return []
    parts = text.split("---", 2)
    if len(parts) < 3:
        return []
    raw_metadata = cast(object, yaml.safe_load(parts[1]))
    if not isinstance(raw_metadata, Mapping):
        return []
    metadata = cast(Mapping[str, object], raw_metadata)
    raw_value = metadata.get("allowed-tools")
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        raw_tools = raw_value.split()
    elif isinstance(raw_value, Sequence):
        raw_tools = [item for item in raw_value if isinstance(item, str)]
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in raw_tools if item.strip()))


# ──── Skill 文件加载辅助 ────


def _load_skill_body(skill_file: Path) -> str:
    r"""读取 SKILL.md frontmatter 之后的正文内容.

    SKILL.md 格式: ---\n<yaml>\n---\n<body>
    返回 body 部分（去除首尾空白），若文件无正文则返回空字符串.
    """
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ""
    return parts[2].strip()


def _merge_skill_content(skill_body: str, references_content: str) -> str:
    r"""合并 SKILL.md 正文与 references 规则文件内容.

    格式: {SKILL.md 正文} + 空行 + ===== + 空行 + {references 内容}
    若其中一部分为空，则仅返回另一部分.
    使用 ===== 而非 --- 避免与 Markdown 水平线或 YAML frontmatter 边界混淆.
    """
    parts: list[str] = []
    if skill_body:
        parts.append(skill_body)
    if references_content:
        parts.append(references_content)
    return "\n\n=====\n\n".join(parts)


# ──── Phase 2: 确定性节点 ────


async def validate_input(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """入口校验单次用户文本长度；超限时不进入 Skill/MCP/模型路径."""
    from common.input_limits import (
        count_user_characters,
        current_input_limit,
        input_overflow_message,
    )

    question = ""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            question = get_message_content(message)
            break
    limit = current_input_limit()
    count = count_user_characters(question)
    if count <= limit.max_chars:
        return {"input_overflow": False}
    logger.warning(
        "数据分析用户提问超出输入限制: len={}, max={}", count, limit.max_chars
    )
    published = await publish_control_message(
        dict(config or {}),
        graph_id="data-analysis",
        text=input_overflow_message(count, limit.max_chars),
        writer=writer,
        control_code="input_overflow",
    )
    return {"messages": [published], "input_overflow": True}


async def resolve_skill(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """确定性 Skill 解析：menu_name -> Skill 路径 + allowed-tools + 规则内容.

    从 config["configurable"] 取 menu_name 和 mode，
    用 match_menu_skill() 确定性匹配 Skill，
    读取 SKILL.md frontmatter 和 references 规则文件，
    写入 state 字段。
    """
    writer(
        {
            "type": "progress",
            "node": "skill_discovery",
            "message": "正在匹配分析技能...",
        }
    )
    configurable = config.get("configurable", {})
    # TODO: 2026-06-01 后可删除 module 兼容分支，前端统一传 menu_name。
    raw_menu_name = str(
        configurable.get("menu_name") or configurable.get("module") or ""
    ).strip()
    routing = get_routing_context(configurable)
    mode = routing.mode  # "fast" 或 "expert"

    matched = match_menu_skill(raw_menu_name)

    if matched is None:
        logger.info(
            "数据分析 Skill 匹配失配：has_menu={}，降级为通用分析",
            bool(raw_menu_name),
        )
        return {
            "selected_skill": None,
            "selected_skill_path": None,
            "selected_skill_description": None,
            "selected_skill_allowed_tools": [],
            # 修复: 匹配失败时不传 SYSTEM_PROMPT，避免 build_system_prompt 重复注入
            "skill_rules_content": "",
            "skill_search_attempted": True,
            "skill_search_question": "",
        }

    skill_dir = SKILLS_DIR / matched.skill_name
    skill_file = skill_dir / "SKILL.md"
    allowed_tools = _load_allowed_tools(skill_file)

    # 读取 SKILL.md 正文（frontmatter 之后的内容）
    skill_body = _load_skill_body(skill_file)

    # 确定规则文件路径
    rules_file = (
        skill_dir / "references" / ("fast.md" if mode != "expert" else "expert.md")
    )
    if not rules_file.exists():
        rules_file = skill_dir / "references" / "fast.md"

    references_content = ""
    if rules_file.exists():
        references_content = rules_file.read_text(encoding="utf-8")

    # 合并 SKILL.md 正文与 references 内容
    skill_rules = _merge_skill_content(skill_body, references_content)
    from common.observability.skill_adapter import emit_skill_stage

    obs_source = "menu-mapping"
    for stage in ("matched", "selected", "loaded"):
        emit_skill_stage(
            config,
            matched.skill_name,
            stage,
            body=skill_rules,
            status=("succeeded" if skill_rules.strip() else "failed")
            if stage == "loaded"
            else None,
            source=obs_source,
        )

    skill_path = f"/skills/{matched.skill_name}/SKILL.md"

    logger.info(
        "数据分析 Skill 匹配成功：has_menu={}，skill={}，mode={}，allowed_tools={}",
        bool(raw_menu_name),
        matched.skill_name,
        mode,
        allowed_tools,
    )

    return {
        "selected_skill": matched.skill_name,
        "selected_skill_path": skill_path,
        "selected_skill_description": matched.title,
        "selected_skill_allowed_tools": allowed_tools,
        "skill_rules_content": skill_rules,
        "skill_search_attempted": True,
        "skill_search_question": "",
    }


async def prepare_model(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """确定性模型准备：组装 system_prompt + 刷新 MCP.

    每次进入 call_model 前执行：
    1. 刷新 MCP 工具列表
    2. 组装完整 system_prompt（含 Skill 规则、时间、mode 上下文）
    3. 写入 system_prompt 到 state

    注意：available_tools 不写入 state（不可序列化），
    由 call_model 在运行时按 selected_skill_allowed_tools 过滤。
    """
    writer(
        {
            "type": "progress",
            "node": "model_preparation",
            "message": "正在准备分析模型...",
        }
    )

    # 1. 刷新 MCP 工具
    await refresh_authorized_mcp_tools(config)

    # 2. 组装 system_prompt
    configurable = config.get("configurable", {})
    skill_rules = state.get("skill_rules_content", "")
    system_prompt = build_system_prompt(
        skill_rules_content=skill_rules,
        configurable=configurable,
    )

    allowed = state.get("selected_skill_allowed_tools") or []
    logger.info(
        "数据分析模型准备完成：allowed_tools={}，system_prompt_length={}",
        allowed,
        len(system_prompt),
    )

    updates: dict[str, Any] = {"system_prompt": system_prompt}
    return updates


# ──── Phase 3: LLM 节点 ────


async def call_model(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """LLM 推理：决定调工具 / 直接输出.

    从 state 取 messages、system_prompt，
    按 selected_skill_allowed_tools 过滤业务工具，
    组装完整消息列表后调用模型。

    当模型返回空内容且无 tool_calls 时，追加引导消息重试一次，
    促使模型基于已有工具结果生成结论文本。
    """
    writer(
        {
            "type": "progress",
            "node": "analysis",
            "message": "正在分析数据...",
        }
    )

    recent = state.get("messages", [])
    last_human = max(
        (i for i, message in enumerate(recent) if isinstance(message, HumanMessage)),
        default=-1,
    )
    if any(
        isinstance(message, ToolMessage)
        and message.additional_kwargs.get("processing_terminal")
        for message in recent[last_human + 1 :]
    ):
        return visible_result(
            AIMessage(
                content="本次全量计算未完成，尚无完整统计结果。请缩小数据范围或重新查询。"
            ),
            config,
        )

    from common.observability.model_adapter import ensure_model_observability

    ensure_model_observability(config)
    model = ModelRegistry.main_llm
    system_prompt = state.get("system_prompt", "")

    # 按 allowed-tools 过滤业务工具（运行时查找，不存入 state）
    allowed = state.get("selected_skill_allowed_tools") or []
    all_tools = get_business_tools(include_computation=True)
    available = filter_business_tools(all_tools, allowed)
    from common.capabilities import filter_capability_mcp_tools

    available = filter_capability_mcp_tools(
        available, config, business_code="data-analysis"
    )
    from data_analysis.tool_policy import (
        planning_exhausted,
        recoverable_tools,
    )

    available = recoverable_tools(available, state.get("messages", []))
    if planning_exhausted(state.get("messages", [])):
        available = []

    messages = state["messages"]
    # 1. 检查最新用户提问长度
    from common.input_limits import (
        count_user_characters,
        current_input_limit,
        input_overflow_message,
    )

    last_human = ""
    for msg in reversed(messages):
        if (
            getattr(msg, "type", None) == "human"
            or type(msg).__name__ == "HumanMessage"
        ):
            last_human = get_message_content(msg)
            break
    limit = current_input_limit()
    count = count_user_characters(last_human)
    if count > limit.max_chars:
        overflow_msg = input_overflow_message(count, limit.max_chars)
        logger.warning(
            "数据分析用户提问超出输入限制: len={}, max={}",
            count,
            limit.max_chars,
        )
        published = await publish_control_message(
            dict(config or {}),
            graph_id="data-analysis",
            text=overflow_msg,
            writer=writer,
            control_code="input_overflow",
        )
        return {"messages": [published]}

    # 2. 统一发送前准备：投影、工具定义、计量与必要整理
    prepare_kwargs: dict[str, Any] = {
        "system_messages": [system_prompt],
        "tools": available,
        "role": "main",
    }
    prepared = await prepare_model_request(state, dict(config or {}), **prepare_kwargs)
    metrics = prepared.metrics
    emit_event(config, "context_metrics", **metrics)
    if prepared.over_capacity:
        published = await publish_control_message(
            dict(config or {}),
            graph_id="data-analysis",
            text=prepared.failure_reason
            or "本次资料超出当前模型可用输入容量，尚未完成处理。请缩小数据范围后重试。",
            writer=writer,
            control_code="context_budget",
        )
        return merge_prepared_updates(
            prepared,
            {
                **prepared.updates,
                "messages": [published],
                "context_metrics": metrics,
            },
        )

    from common.observability.model_adapter import model_call_scope

    observed_scope = model_call_scope(config, role="main")

    def bind(request: PreparedRequest) -> Any:
        return model.bind_tools(request.schemas) if request.schemas else model

    def invoke(request: PreparedRequest) -> Any:
        return invoke_with_data_deadline(
            bind(request),
            request.messages,
            messages,
            config,
            invocation_metadata=observed_scope.runtime_metadata(),
        )

    # 重试调用模型（最多 3 次）
    data_model_started_at = datetime.now(UTC).isoformat()
    response = None
    for attempt in range(3):
        try:
            response, prepared = await call_with_context_recovery(
                invoke,
                prepared,
                state=state,
                run_config=dict(config or {}),
                prepare_kwargs=prepare_kwargs,
            )
            metrics = prepared.metrics
            emit_event(config, "context_metrics", **metrics)
            break
        except ContextBudgetExceeded as exc:
            if isinstance(exc, ContextRecoveryFailed):
                prepared = exc.prepared
            emit_event(config, "context_metrics", **prepared.metrics)
            published = await publish_control_message(
                dict(config or {}),
                graph_id="data-analysis",
                text=str(exc),
                writer=writer,
                control_code="context_budget",
            )
            return merge_prepared_updates(
                prepared,
                {
                    **prepared.updates,
                    "messages": [published],
                    "context_metrics": prepared.metrics,
                },
            )
        except DataProcessingFailure as exc:
            status = emit_event(
                config,
                "context_status",
                stage="data",
                status="failed",
                message=str(exc),
            )
            published = await publish_control_message(
                dict(config or {}),
                graph_id="data-analysis",
                text=str(exc),
                writer=writer,
                control_code="data_failure",
            )
            return merge_prepared_updates(
                prepared,
                {
                    **prepared.updates,
                    "messages": [published],
                    "data_status": status,
                    "context_metrics": prepared.metrics,
                },
            )
        except Exception as exc:
            logger.warning(
                "数据分析模型调用失败 (attempt {}): {}", attempt + 1, type(exc).__name__
            )
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                # 3 次重试全部失败，推送兜底消息给前端
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id="data-analysis",
                    text=_SERVICE_UNAVAILABLE_MESSAGE,
                    writer=writer,
                    control_code="service_unavailable",
                )
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "messages": [published],
                        "context_metrics": prepared.metrics,
                    },
                )

    # 空回复检测：content 为空且无 tool_calls -> 追加引导重试
    if (
        isinstance(response, AIMessage)
        and not response.tool_calls
        and not get_message_content(response).strip()
    ):
        logger.info("数据分析模型空回复，追加引导消息重试")
        try:
            nudge = await prepare_model_request(
                state,
                dict(config or {}),
                system_messages=[system_prompt],
                tools=available,
                tail_messages=[
                    response,
                    HumanMessage(content=_EMPTY_REPLY_NUDGE),
                ],
                role="main",
            )
            emit_event(config, "context_metrics", **nudge.metrics)
            if nudge.over_capacity:
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id="data-analysis",
                    text=nudge.failure_reason
                    or "本次资料超出当前模型可用输入容量，请缩小数据范围后重试。",
                    writer=writer,
                    control_code="context_budget",
                )
                return merge_prepared_updates(
                    nudge,
                    {
                        **nudge.updates,
                        "messages": [published],
                        "context_metrics": nudge.metrics,
                    },
                )
            prepared = nudge
            metrics = nudge.metrics
            nudge_response = await invoke_with_data_deadline(
                bind(nudge), nudge.messages, messages, config
            )
            if (
                isinstance(nudge_response, AIMessage)
                and not nudge_response.tool_calls
                and not get_message_content(nudge_response).strip()
            ):
                logger.warning("数据分析模型空回复重试仍为空，使用兜底消息")
                response = AIMessage(content=_FALLBACK_EMPTY_REPLY)
            else:
                response = nudge_response
        except Exception as exc:
            logger.warning(
                "数据分析模型空回复重试失败: {}，使用兜底消息", type(exc).__name__
            )
            response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

    from common.knowledge.policy import apply_answer_policy

    response = apply_answer_policy(response, messages)
    return merge_prepared_updates(
        prepared,
        {
            **prepared.updates,
            **visible_result(response, config, metrics=metrics, publish=False),
            "data_model_started_at": data_model_started_at,
        },
    )


# ──── Phase 5: 最终输出节点 ────


def _last_answer_message(state: DataAnalysisState) -> AIMessage | None:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message
    return None


def _extract_last_visible_answer(state: DataAnalysisState) -> str:
    """仅提取本轮原稿，不能把历史答案当本轮终答."""
    message = _last_answer_message(state)
    return get_message_content(message).strip() if message else ""


async def finalize_output(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """最终整理：逐段生成、逐段校验、逐段发布；不再缓存全文后才发送."""
    original = _last_answer_message(state)
    if original is not None and original.additional_kwargs.get("ui_visible") is True:
        return visible_result(original, config, writer=writer)

    answer = _extract_last_visible_answer(state)

    writer(
        {
            "type": "progress",
            "message": "正在整理最终答案...",
        }
    )

    from common.observability.model_adapter import (
        ensure_model_observability,
        invocation_config,
    )

    ensure_model_observability(config)
    model = ModelRegistry.main_llm

    publisher = OutputPublisher(
        dict(config or {}),
        graph_id="data-analysis",
        writer=writer,
        authorization_probe=runtime_authorization_probe(dict(config or {})),
    )
    question = ""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            question = get_message_content(message)
            break

    def cleanup_request(answer_text: str) -> tuple[list[Any], dict[str, Any]]:
        built = _FINAL_OUTPUT_CLEANUP_PROMPT.format(
            answer=answer_text or _FALLBACK_EMPTY_REPLY
        )
        messages = [HumanMessage(content=built)]
        return messages, request_metrics(messages)

    async def reduce_answer(text: str) -> str:
        from common.text_reduction import summarize_complete_text

        reduced, _coverage = await summarize_complete_text(
            text or _FALLBACK_EMPTY_REPLY,
            instruction=(
                "压缩待清理的最终正文：保留全部业务结论、数值、单位、日期、"
                "引用和限制说明，不新增事实，不改变结论。"
            ),
            timeout_seconds=timeout,
            run_config=dict(config or {}),
        )
        return reduced

    timeout = app_config.FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout(timeout):
            cleanup_messages, cleanup_metrics = cleanup_request(answer)
            if cleanup_metrics["input_tokens"] > cleanup_metrics["input_budget_tokens"]:
                answer = await reduce_answer(answer)
                cleanup_messages, cleanup_metrics = cleanup_request(answer)
                if (
                    cleanup_metrics["input_tokens"]
                    > cleanup_metrics["input_budget_tokens"]
                ):
                    raise ContextBudgetExceeded(
                        "最终答案整理请求仍超出模型可用输入容量。"
                    )
            emit_event(config, "context_metrics", **cleanup_metrics)
            try:
                result = await stream_final_answer(
                    model,
                    cleanup_messages,
                    invocation_config(config, role="cleanup"),
                    publisher=publisher,
                    policy_messages=state.get("messages", []),
                    question=question,
                )
            except Exception as exc:  # noqa: BLE001 - 仅超窗进入一次恢复
                if not is_context_overflow(exc):
                    raise
                answer = await reduce_answer(answer)
                cleanup_messages, cleanup_metrics = cleanup_request(answer)
                if (
                    cleanup_metrics["input_tokens"]
                    > cleanup_metrics["input_budget_tokens"]
                ):
                    raise ContextBudgetExceeded(
                        "最终答案整理请求仍超出模型可用输入容量。"
                    ) from exc
                publisher = OutputPublisher(
                    dict(config or {}),
                    graph_id="data-analysis",
                    writer=writer,
                    authorization_probe=runtime_authorization_probe(dict(config or {})),
                )
                result = await stream_final_answer(
                    model,
                    cleanup_messages,
                    invocation_config(config, role="cleanup"),
                    publisher=publisher,
                    policy_messages=state.get("messages", []),
                    question=question,
                )
    except Exception as exc:  # noqa: BLE001 - 清洗失败必须落到受控终态
        logger.warning("数据分析最终输出清洗失败: {}", type(exc).__name__)
        if publisher.total_chars:
            await publisher.abort("generation_failed")
            from common.observability.skill_adapter import record_loaded_execution

            record_loaded_execution(config, succeeded=False)
            return {
                "messages": [
                    AIMessage(
                        content=publisher.emitted_text,
                        id=publisher.message_id,
                        additional_kwargs={
                            "ui_visible": False,
                            "output_id": publisher.output_id,
                            "output_protocol_version": 1,
                            "output_aborted": "generation_failed",
                        },
                    )
                ]
            }
        result = None

    if result is None or (result.aborted and result.units == 0):
        # 未公开任何正文时使用固定可读失败；未清洗原稿可能含过程性内容，不直接公开。
        fallback_text = "分析结果已生成，但最终答案整理未完成。请重试。"
        control = await publish_control_message(
            dict(config or {}),
            graph_id="data-analysis",
            text=fallback_text,
            writer=writer,
            authorization_probe=runtime_authorization_probe(dict(config or {})),
            control_code="cleanup_fallback",
        )
        from common.observability.skill_adapter import record_loaded_execution

        record_loaded_execution(
            config,
            succeeded=bool(fallback_text)
            and fallback_text
            not in {
                _SERVICE_UNAVAILABLE_MESSAGE,
                "分析结果已生成，但最终答案整理未完成。请重试。",
            },
        )
        return {"messages": [control]}

    from common.observability.skill_adapter import record_loaded_execution

    record_loaded_execution(
        config,
        succeeded=not result.aborted
        and bool(result.text)
        and result.text != _SERVICE_UNAVAILABLE_MESSAGE,
    )
    if result.usage:
        emit_event(
            config,
            "context_metrics",
            latest_usage=result.usage,
        )
    return {"messages": [result.message]}
