"""data_analysis 系统提示词组装与工具过滤."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.context import get_routing_context
from common.prompts import (
    EXPERT_MODE_SYSTEM_PROMPT,
    FAST_MODE_SYSTEM_PROMPT,
    with_data_analysis_output_guard,
)
from common.runtime_tools import tool_name

# data_analysis 系统提示词（从原 graph.py 迁移）
SYSTEM_PROMPT = """
    ## 角色职责
    你是中科宇图的空气质量数据智能分析助手，服务于业务页面旁的"AI分析"功能。
    用户当前页面已展示完整的空气质量数据，你的任务是基于这些**用户已可见的数据**，提供深度解读、趋势分析、异常识别和决策建议。

    ## 核心分析原则
    - 数据已在用户眼前：不要重复罗列原始数据，而是解释数据背后的含义、趋势和关联。
    - 聚焦"为什么"和"怎么办"：不仅告诉用户数据是什么，更要分析原因、影响和应对建议。
    - 对比和关联：善于将当前数据与历史同期、周边城市、行业标准进行对比分析。

    ## 输出要求
    - 结论先行：先给出核心结论，再用关键数据支撑分析。
    - 表格限制：如必须使用表格，不得超过两列；优先使用要点和短段落替代表格。
    - 发现异常或值得关注的点时，明确标注并给出建议。

    ## Skill 使用规则
    你已自动匹配到当前页面的数据分析 Skill，完整执行规则已在下方提供。
    请严格按照规则中的数据获取流程、输出规范和刚性规则执行分析。
    """


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


def build_system_prompt(
    *,
    skill_rules_content: str = "",
    configurable: dict[str, Any] | None = None,
) -> str:
    """组装完整的系统提示词.

    组装顺序: SYSTEM_PROMPT + output_guard + 时间上下文 + mode 上下文 + Skill 规则
    """
    parts = [with_data_analysis_output_guard(SYSTEM_PROMPT)]
    parts.append(build_time_context())
    parts.append(build_mode_context(configurable))

    if skill_rules_content:
        parts.append(skill_rules_content)

    return "\n\n".join(parts)


def filter_business_tools(
    all_tools: list[Any],
    allowed_tool_names: list[str] | None,
) -> list[Any]:
    """过滤业务工具——只保留 allowed_tool_names 中列出的工具.

    框架工具（不在业务工具列表中的）始终保留。
    allowed_tool_names 为空或 None 时，不暴露任何业务工具。
    """
    if not allowed_tool_names:
        return []

    allowed = set(allowed_tool_names)
    filtered: list[Any] = []
    for t in all_tools:
        name = tool_name(t)
        if name is not None and name in allowed:
            filtered.append(t)
    return filtered
