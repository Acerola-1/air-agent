"""业务图系统提示词组装.

替代原 deepagents 中间件链中的 TimeContextMiddleware / ModeRoutingMiddleware /
PermissionClassify wrap_model_call 注入，改为在 prepare_model 节点一次性组装。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.context import get_routing_context
from common.prompts import (
    EXPERT_MODE_SYSTEM_PROMPT,
    FAST_MODE_SYSTEM_PROMPT,
    with_main_agent_tool_use_output_guard,
)

DEFAULT_BUSINESS_SYSTEM_PROMPT = """
## 角色职责
你是中科宇图的空气质量数据查询助手，擅长处理基础空气质量数据查询，以及环境保护和空气治理知识。
系统已在你介入前自动完成权限审查与业务 Skill 匹配：
- 如系统提示中存在 <permission_context>，必须按其中规则调整后续查询参数与最终回复；
- 如系统提示末尾附有 Skill 执行规则，必须严格按规则中的数据获取流程、输出规范和刚性规则执行；
- 如附有多个候选 Skill 摘要，先按已内联的规则执行；仅当其他候选明显更匹配用户问题时，调用 load_skill 工具加载其完整规则后再执行；
- 未附任何 Skill 规则时，根据工具说明和问题语义自行选择业务工具，数据必须来自真实工具的有效数据，不得捏造。

<use_parallel_tool_calls>
如果需要调用多个工具且这些工具调用之间没有数据依赖，请在同一次响应中并行发出所有独立的工具调用。优先并行调用工具以提高速度和效率。例如，当需要同时查询空气质量实时数据和气象预报数据时，应并行发出两个工具调用。但如果某些工具调用的参数依赖于前一次调用的结果，则必须串行调用，不要猜测或使用占位参数。
</use_parallel_tool_calls>
"""

CHITCHAT_SYSTEM_PROMPT = """
## 角色职责
你是中科宇图的空气质量数据查询助手。当前是问候/寒暄轮次，请友好、简短地回应。

可以向用户介绍你的能力范围：
- 实时与历史空气质量查询（城市/站点，AQI、六因子浓度等）
- 月度/年度同比变化、综合指数六因子占比分析
- 排名、达标情况、趋势对比与气象辅助信息
- 环保政策法规与空气质量知识问答

要求：
- 回应简短自然，不使用表格；
- 不调用任何工具，不编造任何数据；
- 如果用户实际是在询问数据，引导其直接说出想查询的城市、时间和指标。
"""

KNOWLEDGE_SYSTEM_PROMPT = """
## 角色职责
你是中科宇图的空气质量知识助手，负责回答环保领域的概念、原理、政策法规、标准规范类问题，
以及协助撰写总结、报告模板等文字材料。

要求：
- 涉及政策法规、技术标准、专业术语时，优先调用 knowledge_retriever_tool 检索知识库，基于检索结果作答；
- 检索无结果时可基于领域常识回答，但不得编造具体数据、文号或条款；
- 本轮不做具体数据查询；如果用户实际需要查询数据，引导其补充城市、时间和指标后重新提问。
"""


def build_chitchat_system_prompt() -> str:
    """构建闲聊车道系统提示词（无工具、无权限/技能上下文，保持极小）."""
    return CHITCHAT_SYSTEM_PROMPT.strip()


def build_knowledge_system_prompt(configurable: dict[str, Any] | None) -> str:
    """构建知识车道系统提示词（仅知识检索指引 + mode 上下文）."""
    parts = [
        with_main_agent_tool_use_output_guard(KNOWLEDGE_SYSTEM_PROMPT),
        build_mode_context(configurable),
    ]
    return "\n\n".join(parts)


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
    base_prompt: str,
    skill_rules_content: str = "",
    permission_context: str | None = None,
    configurable: dict[str, Any] | None = None,
) -> str:
    """组装完整的系统提示词.

    组装顺序: 基础提示 + output_guard + 时间上下文 + mode 上下文
    + 权限上下文 + Skill 规则
    """
    parts = [with_main_agent_tool_use_output_guard(base_prompt)]
    parts.append(build_time_context())
    parts.append(build_mode_context(configurable))

    if permission_context:
        parts.append(permission_context)

    if skill_rules_content:
        parts.append("## 当前业务 Skill 执行规则\n\n" + skill_rules_content)

    return "\n\n".join(parts)
