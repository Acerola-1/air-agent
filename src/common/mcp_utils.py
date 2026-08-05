"""MCP 工具通用辅助函数.

提供处理 MCP 工具返回值和选择工具的通用函数，避免循环依赖。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from loguru import logger


def coerce_jsonish(value: Any) -> Any:
    """将 MCP 工具返回值规范化为 JSON 对象.

    兼容 LangChain/MCP content wrapper，例如：
    - [{"type": "text", "text": "{...}"}]
    - {"output": [{"type": "text", "text": "{...}"}]}
    - {"outputs": {"output": [{"type": "text", "text": "{...}"}]}}
    """
    if value is None:
        return None

    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return coerce_jsonish(text)

        outputs = value.get("outputs")
        if isinstance(outputs, dict) and "output" in outputs:
            return coerce_jsonish(outputs.get("output"))

        output = value.get("output")
        if isinstance(output, list | dict | str):
            return coerce_jsonish(output)

        content = value.get("content")
        if isinstance(content, list | dict | str):
            return coerce_jsonish(content)

        return value

    if isinstance(value, list):
        if len(value) == 1:
            return coerce_jsonish(value[0])
        for item in value:
            coerced = coerce_jsonish(item)
            if isinstance(coerced, dict):
                return coerced
        return value

    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return text

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.debug("MCP 返回值不是纯 JSON，保留原始字符串")
        return value


def tool_name(tool: Any) -> str | None:
    """提取工具名."""
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def select_tool(tools: Sequence[Any], name: str) -> Any | None:
    """按名称从动态 MCP 工具列表中选择工具."""
    for tool in tools:
        if tool_name(tool) == name:
            return tool
    return None
