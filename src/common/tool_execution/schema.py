"""公共工具schema的内容摘要；不读取图状态或身份."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool


def content_version(value: Any) -> str:
    """Hash canonical public definition content, never connection credentials."""
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )


def schema_version_for(server_key: str, tool: Any) -> str:
    """保持原始名称、描述、参数定义的摘要算法；服务键不改变schema版本."""
    function = convert_to_openai_tool(tool)["function"]
    name = function["name"]
    return content_version(
        {
            "name": name,
            "description": function.get("description") or name,
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
    )
