"""data_analysis 节点流的所有节点函数."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from loguru import logger

from common.config import config as app_config
from common.context import get_message_content, get_routing_context
from common.context_guard import prepare_model_context
from common.conversation_summarizer import update_conversation_summary
from common.data_governance import DataProcessingFailure, invoke_with_data_deadline
from common.mcp_client import ensure_mcp_tools
from common.model_budget import (
    ContextBudgetExceeded,
    enforce_request_budget,
    fit_tool_schemas,
    request_metrics,
)
from common.models import ModelRegistry
from common.output_contract import visible_result
from common.runtime_events import emit_event
from common.runtime_tools import get_business_tools
from data_analysis.menu_skill_mapping import match_menu_skill
from data_analysis.prompt_builder import (
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

    for stage in ("matched", "selected", "loaded"):
        emit_skill_stage(
            config,
            matched.skill_name,
            stage,
            body=skill_rules,
            status=("succeeded" if skill_rules.strip() else "failed")
            if stage == "loaded"
            else None,
            source="menu-mapping",
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
    await ensure_mcp_tools()

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
    updates.update(
        await update_conversation_summary(
            {**state, "system_prompt": system_prompt},
            config,
            tools=filter_business_tools(
                get_business_tools(include_computation=True), allowed
            ),
        )
    )

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

    messages = state["messages"]
    # 1. 检查最新用户提问长度
    last_human = ""
    for msg in reversed(messages):
        if (
            getattr(msg, "type", None) == "human"
            or type(msg).__name__ == "HumanMessage"
        ):
            last_human = get_message_content(msg)
            break
    max_input_chars = getattr(app_config, "MAX_INPUT_CHARS", 8000)
    if len(last_human) > max_input_chars:
        overflow_msg = (
            f"您的提问内容过长（共 {len(last_human)} 字符，系统单次提问上限为 {max_input_chars} 字符），"
            "请精简或分段提问。"
        )
        logger.warning(
            "数据分析用户提问超出输入限制: len={}, max={}",
            len(last_human),
            max_input_chars,
        )
        writer({"type": "final_output_done", "message": overflow_msg})
        return visible_result(AIMessage(content=overflow_msg), config)

    # 2. 模型调用前只读上下文修剪 (注入早期历史摘要 + 保护当期轮次与旧工具治理)
    protected_messages = prepare_model_context(
        messages,
        summary=state.get("summary"),
        summary_covered_message_id=state.get("summary_covered_message_id"),
    )

    full_messages: list[Any] = []
    if system_prompt:
        full_messages.append(SystemMessage(content=system_prompt))
    full_messages.extend(protected_messages)

    # 绑定工具
    schemas = fit_tool_schemas(full_messages, available)
    try:
        metrics = enforce_request_budget(full_messages, schemas)
    except ContextBudgetExceeded as exc:
        metrics = request_metrics(full_messages, schemas)
        emit_event(config, "context_metrics", **metrics)
        return visible_result(AIMessage(content=str(exc)), config, metrics=metrics)
    emit_event(config, "context_metrics", **metrics)

    if available:
        model_with_tools = model.bind_tools(schemas)
    else:
        model_with_tools = model

    # 重试调用模型（最多 3 次）
    from common.observability.model_adapter import model_call_scope

    observed_scope = model_call_scope(config, role="main")
    data_model_started_at = datetime.now(UTC).isoformat()
    response = None
    for attempt in range(3):
        try:
            response = await invoke_with_data_deadline(
                model_with_tools,
                full_messages,
                messages,
                config,
                invocation_metadata=observed_scope.runtime_metadata(),
            )
            break
        except DataProcessingFailure as exc:
            status = emit_event(
                config,
                "context_status",
                stage="data",
                status="failed",
                message=str(exc),
            )
            return {
                **visible_result(AIMessage(content=str(exc)), config, metrics=metrics),
                "data_status": status,
            }
        except Exception as exc:
            logger.warning(
                "数据分析模型调用失败 (attempt {}): {}", attempt + 1, type(exc).__name__
            )
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                # 3 次重试全部失败，推送兜底消息给前端
                writer(
                    {
                        "type": "final_output_done",
                        "message": _SERVICE_UNAVAILABLE_MESSAGE,
                    }
                )
                return visible_result(
                    AIMessage(content=_SERVICE_UNAVAILABLE_MESSAGE),
                    config,
                    metrics=metrics,
                )

    # 空回复检测：content 为空且无 tool_calls -> 追加引导重试
    if (
        isinstance(response, AIMessage)
        and not response.tool_calls
        and not get_message_content(response).strip()
    ):
        logger.info("数据分析模型空回复，追加引导消息重试")
        nudge_messages = [
            *full_messages,
            response,
            HumanMessage(content=_EMPTY_REPLY_NUDGE),
        ]
        try:
            metrics = enforce_request_budget(nudge_messages, schemas)
            emit_event(config, "context_metrics", **metrics)
            nudge_response = await invoke_with_data_deadline(
                model_with_tools, nudge_messages, messages, config
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

    return {
        **visible_result(response, config, metrics=metrics, publish=False),
        "data_model_started_at": data_model_started_at,
    }


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
    """收集完整清洗结果后发布唯一终答，失败不暴露半段草稿."""
    original = _last_answer_message(state)
    if original is not None and original.additional_kwargs.get("ui_visible") is True:
        return visible_result(original, config, writer=writer)

    answer = _extract_last_visible_answer(state)

    if not answer:
        logger.warning("数据分析最终输出未找到可见业务答案，使用兜底回复")
        answer = _FALLBACK_EMPTY_REPLY

    writer(
        {
            "type": "progress",
            "message": "正在整理最终答案...",
        }
    )

    # 调用清洗 LLM 流式生成清洗后的答案
    from common.observability.model_adapter import ensure_model_observability

    ensure_model_observability(config)
    from common.observability.model_adapter import invocation_config

    observed_config = invocation_config(config, role="cleanup")
    model = ModelRegistry.main_llm
    prompt = _FINAL_OUTPUT_CLEANUP_PROMPT.format(answer=answer)
    full_cleaned = ""

    try:
        async with asyncio.timeout(app_config.FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS):
            enforce_request_budget([HumanMessage(content=prompt)])
            async for chunk in model.astream(
                [HumanMessage(content=prompt)],
                config={**observed_config, "tags": ["nostream", "internal-cleanup"]},
            ):
                if (getattr(chunk, "response_metadata", {}) or {}).get(
                    "finish_reason"
                ) in {"length", "content_filter"}:
                    raise ValueError("终答清洗输出未完整结束")
                content = getattr(chunk, "content", None)
                if isinstance(content, list):
                    content = "".join(str(item) for item in content)
                elif content is None:
                    content = ""
                else:
                    content = str(content)

                if not content:
                    continue

                full_cleaned += content
    except Exception as exc:
        logger.warning("数据分析最终输出清洗失败: {}", type(exc).__name__)
        full_cleaned = "分析结果已生成，但最终答案整理未完成。请重试。"

    cleaned = full_cleaned.strip()

    # 兜底：如果清洗后为空，使用兜底消息
    if not cleaned:
        cleaned = _SERVICE_UNAVAILABLE_MESSAGE

    original = _last_answer_message(state)
    from common.observability.skill_adapter import record_loaded_execution

    record_loaded_execution(
        config, succeeded=bool(cleaned) and cleaned != _SERVICE_UNAVAILABLE_MESSAGE
    )
    return visible_result(
        original.model_copy(update={"content": cleaned})
        if original
        else AIMessage(content=cleaned),
        config,
        writer=writer,
    )
