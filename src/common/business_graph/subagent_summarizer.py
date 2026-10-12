"""非结构化工具输出的全量、有界摘要."""

from __future__ import annotations

import json
from typing import Any

from common.text_reduction import summarize_complete_text


async def summarize_large_tool_output(
    *,
    user_question: str,
    tool_name: str,
    tool_args: dict[str, Any],
    raw_output: str,
    timeout_seconds: float | None = None,
) -> tuple[str, dict[str, object]]:
    """全量归纳非结构化资料，失败向上传播."""
    result, coverage = await summarize_complete_text(
        raw_output,
        instruction=f"问题：{user_question}\n来源：{tool_name}\n参数：{json.dumps(tool_args, ensure_ascii=False)}\n完整归纳问题相关资料；统计类结论必须依据完整数据，不能凭摘要猜测。",
        timeout_seconds=timeout_seconds,
    )
    return result, coverage
