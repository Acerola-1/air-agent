"""MCP fixture 服务器子进程入口（由 tests 以独立进程启动）.

用法：`python -m mcp_fixture.server_process <port>`
行为契约见 server.py 模块文档。
"""

from __future__ import annotations

import sys

import uvicorn

from mcp_fixture.server import build_asgi


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: server_process <port>", file=sys.stderr)  # noqa: T201
        return 2
    port = int(sys.argv[1])
    uvicorn.run(build_asgi(), host="127.0.0.1", port=port, log_level="critical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
