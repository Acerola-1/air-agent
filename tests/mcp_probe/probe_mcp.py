"""任务 1.3 探针：统一业务平台 MCP 的匿名可达契约探测（initialize / tools/list）。

只做无副作用的协议层握手与 schema 列举，**不携带任何凭据、不调用业务工具**。
输出脱敏：不落 URL 主机与工具描述原文，只落认证方式观察、主体字段名与计数。

用法：
    .venv/bin/python tests/mcp_probe/probe_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> int:
    url = os.environ.get("MCP_SERVER_URL", "")
    if not url:
        print(json.dumps({"reachable": False, "reason": "MCP_SERVER_URL 未设置"}))  # noqa: T201
        return 1

    result: dict[str, object] = {"url_scheme": url.split("://")[0]}
    try:
        async with streamablehttp_client(url, timeout=20) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                result["initialize"] = {
                    "protocol_version": getattr(init, "protocolVersion", None),
                    "server_name": getattr(init.serverInfo, "name", None),
                }
                tools = await session.list_tools()
                listed = tools.tools or []
                result["tool_count"] = len(listed)
                identity_fields: dict[str, list[str]] = {}
                for tool in listed:
                    schema = tool.inputSchema or {}
                    props = (
                        schema.get("properties", {}) if isinstance(schema, dict) else {}
                    )
                    fields = sorted(
                        name
                        for name in props
                        if any(
                            k in name.lower()
                            for k in (
                                "user",
                                "tenant",
                                "org",
                                "dept",
                                "account",
                                "region",
                                "city",
                            )
                        )
                    )
                    if fields:
                        identity_fields[tool.name] = fields
                result["tools_with_identity_like_fields"] = identity_fields
                result["anonymous_call_succeeded"] = True
    except Exception as exc:  # noqa: BLE001
        result["anonymous_call_succeeded"] = False
        result["error_type"] = type(exc).__name__
        result["error_note"] = str(exc)[:300]

    print(json.dumps(result, ensure_ascii=False, indent=2))  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
