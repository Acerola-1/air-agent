"""data_analysis 系统提示词组装与工具过滤."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.computation import COMPUTATION_SYSTEM_RULES
from common.prompts import (
    ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE,
    AIR_QUALITY_UNIT_NORMALIZATION_GUIDE,
    DATA_AUTHENTICITY_GUARD,
    DATA_DELAY_RETRY_GUIDE,
    EXPERT_MODE_SYSTEM_PROMPT,
    FAST_MODE_SYSTEM_PROMPT,
    POLLUTANT_LEVEL_BOUNDARY_GUIDE,
    TIME_PARAMETER_NORMALIZATION_GUIDE,
)
from common.runtime_context import get_routing_context
from common.runtime_tools import tool_name


def with_data_analysis_output_guard(system_prompt: str) -> str:
    """将数据分析页面伴随输出约束追加到业务图系统提示词."""
    return (
        f"{system_prompt.strip()}\n\n"
        f"{DATA_AUTHENTICITY_GUARD}\n\n"
        f"{ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE}\n\n"
        f"{TIME_PARAMETER_NORMALIZATION_GUIDE}\n\n"
        f"{DATA_DELAY_RETRY_GUIDE}\n\n"
        f"{AIR_QUALITY_UNIT_NORMALIZATION_GUIDE}\n\n"
        f"{POLLUTANT_LEVEL_BOUNDARY_GUIDE}"
    )


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

    ## 知识检索与依据
    - 具体法规、标准、条款、文号、限值必须调用 knowledge_search，并仅引用返回的 citation_id；
    - 引用格式 [[cite:<citation_id>]]；location_status=not_provided 时不要补页码；
    - 未取得可核验依据时明确说明，不得凭常识补写具体规定；
    - 知识检索不授予任何额外数据分析权限，菜单约束保持不变。

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

    parts.append(COMPUTATION_SYSTEM_RULES)

    return "\n\n".join(parts)


def filter_business_tools(
    all_tools: list[Any],
    allowed_tool_names: list[str] | None,
) -> list[Any]:
    """过滤业务工具——只保留 allowed_tool_names 中列出的工具.

    框架工具（不在业务工具列表中的）始终保留。
    allowed_tool_names 为空或 None 时，不暴露任何数据分析业务工具，但保留
    knowledge_search：无菜单时可做知识问答，不能据此启动任意数据分析。
    create_artifact / get_beijing_time / knowledge_search 作为跨技能通用工具
    始终保留，保证 Agent 在任何技能命中场景下都能产出画布与可核验知识依据。
    """
    if not allowed_tool_names:
        return [
            t
            for t in all_tools
            if tool_name(t) in {"compute_artifact", "knowledge_search"}
        ]

    # 始终可见的通用工具：画布输出 + 时间 + 知识检索，不受技能 allowed-tools 限制
    always_visible = {
        "create_artifact",
        "get_beijing_time",
        "compute_artifact",
        "knowledge_search",
    }
    allowed = set(allowed_tool_names) | always_visible
    filtered: list[Any] = []
    for t in all_tools:
        name = tool_name(t)
        if name is not None and name in allowed:
            filtered.append(t)
    return filtered


# 保留历史常量供兼容检查，运行时组装不启用此旧片段。
DATA_ANALYSIS_OUTPUT_GUARD = """
## 输出格式要求
- 使用 Markdown 输出，结论先行，层级清晰，内容简洁专业。
- 当前回答服务于用户已打开的可视化页面，只做页面数据的伴随分析，不复刻页面已有图表、表格、排名或明细。
- 默认使用短段落和要点总结，优先提炼趋势、异常、差距、风险、原因线索和必要建议。
- 表格仅用于小规模关键对比：最多 4 列、6 行数据；不得输出长表格、大明细表或完整原始数据表。页面已有明细较多时，改用要点摘要、Top/Bottom 项、异常项或区间概括。

## 用户可见输出约束
- 只允许输出结论、依据、关键数据、风险提示和必要建议。
- 如果上下文中不存在有效数据，简短回复"没有获取到必要数据，请稍后重试。"
- 不得输出工具调用过程、执行进度、失败重试、错误状态码(如502、500)、超时提示、内部实现名词(如skill、工具名、JSON、SQL、路径、MCP等)。工具返回错误时，仅用业务语言说明"暂未获取到相关数据"并基于已有数据继续分析。
""".strip()
