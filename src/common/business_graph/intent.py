"""业务图入口意图分类（车道路由）.

三车道设计：
- chitchat: 纯问候/寒暄/自我介绍 → 小提示词、零工具、单轮 LLM 直答
- knowledge: 概念/政策/写作类知识问答（不含时间与行政区）→ 仅本地知识类工具
- data_query: 其余全部 → 权限审查 + 技能路由 + 业务工具全流程

分类为纯规则（正则），不引入额外 LLM/embedding 调用；判定口径与
common.permission.rules 的 no_check 白名单同源，保证意图车道与权限预分类一致。

核心原则——错误代价不对称：把数据查询误判成闲聊（用户拿不到数据）远比
把闲聊误判成数据查询（只是慢几秒）严重，因此不确定时一律 fail-open 到
data_query 全流程，快车道只对高置信信号开放。
"""

from __future__ import annotations

from common.permission.rules import (
    _CHAT_GREETING_PATTERN,
    _KNOWLEDGE_PATTERN,
    _LOCATION_PATTERN,
    _TIME_PATTERN,
)

INTENT_CHITCHAT = "chitchat"
INTENT_KNOWLEDGE = "knowledge"
INTENT_DATA_QUERY = "data_query"


def classify_intent_rule(question: str) -> tuple[str, str]:
    """按规则判定问题的意图车道，返回 (intent, reason).

    reason 仅用于日志与测试断言，不写入 state。
    """
    normalized = question.strip()
    if not normalized:
        return INTENT_CHITCHAT, "empty_question"

    # 整句锚定的问候白名单：只吃纯问候，不会误伤
    # "在吗，帮我查保定空气质量" 这类带问候前缀的真实查询
    if _CHAT_GREETING_PATTERN.search(normalized):
        return INTENT_CHITCHAT, "greeting_whitelist"

    # 知识问答：命中知识关键词且不含时间/行政区（与权限 no_check 口径一致）
    has_time = bool(_TIME_PATTERN.search(normalized))
    has_location = bool(_LOCATION_PATTERN.search(normalized))
    if _KNOWLEDGE_PATTERN.search(normalized) and not has_time and not has_location:
        return INTENT_KNOWLEDGE, "knowledge_pattern"

    # 兜底：宽进全流程
    return INTENT_DATA_QUERY, "fail_open"
