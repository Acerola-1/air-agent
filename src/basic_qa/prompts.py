"""业务图系统提示词组装.

替代原 deepagents 中间件链中的 TimeContextMiddleware / ModeRoutingMiddleware，
改为在 prepare_model 节点一次性组装。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.computation import COMPUTATION_SYSTEM_RULES
from common.prompts import (
    ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE,
    AIR_QUALITY_UNIT_NORMALIZATION_GUIDE,
    ANTI_PROCESS_RESIDUE_GUARD,
    ARTIFACT_GENERATION_GUIDE,
    DATA_AUTHENTICITY_GUARD,
    DATA_DELAY_RETRY_GUIDE,
    EXPERT_MODE_SYSTEM_PROMPT,
    FAST_MODE_SYSTEM_PROMPT,
    MAIN_AGENT_TOOL_USE_OUTPUT_GUARD,
    POLLUTANT_LEVEL_BOUNDARY_GUIDE,
    TIME_PARAMETER_NORMALIZATION_GUIDE,
)
from common.runtime_context import get_routing_context


def with_main_agent_tool_use_output_guard(system_prompt: str) -> str:
    """将主 Agent 共享输出约束追加到业务图系统提示词."""
    return (
        f"{system_prompt.strip()}\n\n"
        f"{DATA_AUTHENTICITY_GUARD}\n\n"
        f"{ANTI_PROCESS_RESIDUE_GUARD}\n\n"
        f"{MAIN_AGENT_TOOL_USE_OUTPUT_GUARD}\n\n"
        f"{ARTIFACT_GENERATION_GUIDE}\n\n"
        f"{ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE}\n\n"
        f"{TIME_PARAMETER_NORMALIZATION_GUIDE}\n\n"
        f"{DATA_DELAY_RETRY_GUIDE}\n\n"
        f"{AIR_QUALITY_UNIT_NORMALIZATION_GUIDE}\n\n"
        f"{POLLUTANT_LEVEL_BOUNDARY_GUIDE}"
    )


def build_time_context() -> str:
    """构建当前北京时间上下文（等效于 TimeContextMiddleware）."""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    current_time = now.strftime("%Y-%m-%d %H:%M:%S")
    current_date = now.strftime("%Y-%m-%d")
    return f"""
【当前真实时间】
- 当前北京时间：{current_time}
- 当前日期：{current_date}

【时间回答规则】
- 用户询问当前时间、当前日期、今天是几号、现在几点等通用时间问题时，直接根据上述真实时间回答。
- 用户问题中的今天、昨天、当前、最近、上周、上月等相对时间，必须以上述真实时间换算，不得使用模型训练数据中的年份或日期。
""".strip()


def build_mode_context(configurable: dict[str, Any] | None) -> str:
    """构建运行模式上下文（等效于 ModeRoutingMiddleware）."""
    routing = get_routing_context(configurable)
    parts = [routing.prompt]
    if routing.mode == "expert":
        parts.append(EXPERT_MODE_SYSTEM_PROMPT)
    else:
        parts.append(FAST_MODE_SYSTEM_PROMPT)
    return "\n\n".join(parts)


def build_basic_qa_system_prompt(configurable: dict[str, Any] | None) -> str:
    """使用独立版本的会话模板，保留远程管理并隔离旧路由模板."""
    base = """你是中科宇图的空气质量助手。按会话理解用户需求，不猜测城市、时间或指标。
普通知识可以解释；具体法规、条款和限值必须先检索并使用返回的 citation_id。
数据结论必须依据本轮真实查询或明确标识的历史结果；不得把现状推断为已证实污染来源。
用户只要求复述时保持原结果的对象和时间，不擅自查询或编造。"""
    from common.langfuse_prompts import get_managed_prompt

    resolved = get_managed_prompt("air-agent/basic-qa/conversation-system-v2", base)
    return "\n\n".join(
        (
            with_main_agent_tool_use_output_guard(resolved),
            build_time_context(),
            build_mode_context(configurable),
            COMPUTATION_SYSTEM_RULES,
        )
    )
