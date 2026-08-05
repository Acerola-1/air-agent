"""最终输出确定性清洗.

替代原 FinalOutputCleanupMiddleware 的"答案重生成"LLM 调用：
清洗目标（去思考标签、工具痕迹、内部上下文块）绝大多数可由规则完成，
仅在检测到明显过程性残留且显式开启兜底开关时才走 LLM。
"""

from __future__ import annotations

import re

# 需要整块删除的内部标签/上下文块
_BLOCK_PATTERNS = (
    re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_response>.*?</tool_response>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<permission_context>.*?</permission_context>", re.DOTALL),
    re.compile(r"<runtime_context>.*?</runtime_context>", re.DOTALL),
)

# 未闭合的残留标签
_DANGLING_TAG_PATTERN = re.compile(
    r"</?(?:think|thinking|tool_call|tool_response|permission_context|runtime_context)>",
    re.IGNORECASE,
)

# 过程性残留信号：命中时说明规则清洗可能不充分，可触发 LLM 兜底（默认关闭）
_RESIDUE_PATTERN = re.compile(
    r"(ToolMessage|AIMessage|HumanMessage|find_skill|load_skill|SKILL\.md|"
    r"middleware|LangGraph|MCP\s*(?:server|工具|调用)|Traceback|"
    r"我(?:需要|正在|将)调用|接下来(?:我将)?调用|让我(?:先)?调用|"
    r"状态码\s*5\d\d|HTTP\s*5\d\d|\btimeout\b|timed?\s*out)",
    re.IGNORECASE,
)

# 连续 3 个以上空行压缩为 2 个
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def deterministic_cleanup(text: str) -> str:
    """规则清洗最终答案：删除内部标签块与残留标签，压缩多余空行."""
    cleaned = text
    for pattern in _BLOCK_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = _DANGLING_TAG_PATTERN.sub("", cleaned)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def has_process_residue(text: str) -> bool:
    """检测正文是否仍含明显的过程性/内部实现残留."""
    return bool(_RESIDUE_PATTERN.search(text))
