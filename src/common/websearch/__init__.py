"""外部联网搜索客户端（受人工审批约束的补充数据来源）."""

from common.websearch.deepseek import (
    DEFAULT_RESULT_COUNT,
    MAX_RESULT_COUNT,
    DeepSeekSearchError,
    SearchResult,
    search_web,
)

__all__ = [
    "DEFAULT_RESULT_COUNT",
    "MAX_RESULT_COUNT",
    "DeepSeekSearchError",
    "SearchResult",
    "search_web",
]
