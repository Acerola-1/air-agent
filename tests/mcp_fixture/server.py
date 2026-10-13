"""受控双企业 MCP fixture 服务器（任务 1.4；模拟证据，非真实平台）.

行为契约（docs/mcp-auth-contract.md 中登记为 `v1-fixture` 协议）：
- 每个工具调用必须携带 `Authorization: Bearer <fixture token>`；token → 外部主体
  （subject）由本服务解析，未识别 token 拒绝。
- 工具 `query_air(user_id, region)`：`user_id` 是**认证主体参数**（客户端伪造值
  会被授权层拦截器覆盖为绑定主体）；返回体同时回显服务端认证主体与收到的
  user_id，便于断言"实际发送身份"。
- `slow_query` 支持注入延迟（并发交错测试）；`flaky_query` 支持失败计数
  （重试与重试前撤销测试）。
- 服务器以**独立子进程**运行（server_process.py），避免 anyio/cancel-scope
  状态在测试事件循环之间泄漏；计数器经 `/admin/*` 控制面读取。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import CallToolResult

FIXTURE_TOKENS = {
    "tok-tenant-a": "subject-a",
    "tok-tenant-b": "subject-b",
    "tok-rotated-a": "subject-a-rotated",
}

DATA: dict[str, dict[str, Any]] = {
    "subject-a": {"city": "杭州市", "value": 61},
    "subject-b": {"city": "北京市", "value": 88},
    "subject-a-rotated": {"city": "杭州市", "value": 62},
}

CALLS: dict[str, list[dict[str, Any]]] = {}
FLAKY_REMAINING = {"count": 0}


def reset_state() -> None:
    CALLS.clear()
    FLAKY_REMAINING["count"] = 0


def _identity_from_ctx(ctx: Context) -> str:
    request = ctx.request_context.request
    auth = request.headers.get("authorization", "") if request else ""
    token = auth.removeprefix("Bearer ").strip()
    subject = FIXTURE_TOKENS.get(token)
    if subject is None:
        raise ValueError("unauthorized subject")
    return subject


def _build_mcp() -> FastMCP:
    app = FastMCP("air-agent-mcp-fixture")

    @app.tool()
    async def query_air(user_id: str, region: str, ctx: Context) -> CallToolResult:
        """按授权主体查询区域空气质量（fixture）."""
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append({"user_id": user_id, "region": region})
        payload = {
            "code": 200,
            "success": True,
            "auth_subject": subject,
            "received_user_id": user_id,
            "data": {**DATA.get(subject, {}), "region": region},
        }
        return CallToolResult(
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            isError=False,
        )

    @app.tool()
    async def slow_query(
        user_id: str, region: str, delay_ms: int, ctx: Context
    ) -> CallToolResult:
        """可控延迟查询（并发交错测试）."""
        await asyncio.sleep(max(0, int(delay_ms)) / 1000)
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append(
            {"user_id": user_id, "region": region, "slow": True}
        )
        payload = {
            "code": 200,
            "success": True,
            "auth_subject": subject,
            "data": DATA.get(subject, {}),
        }
        return CallToolResult(
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            isError=False,
        )

    @app.tool()
    async def flaky_query(user_id: str, region: str, ctx: Context) -> CallToolResult:
        """前 FLAKY_REMAINING 次调用抛错（重试语义测试）."""
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append(
            {"user_id": user_id, "region": region, "flaky": True}
        )
        if FLAKY_REMAINING["count"] > 0:
            FLAKY_REMAINING["count"] -= 1
            raise RuntimeError("upstream 503")
        payload = {"code": 200, "success": True, "auth_subject": subject, "ok": True}
        return CallToolResult(
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            isError=False,
        )

    return app


def build_asgi() -> Any:
    """MCP app + /admin 控制面（计数读取、重置、flaky 注入）.

    R07：所有 MCP 协议路径（initialize / tools/list / tools/call）都必须携带
    有效 `Authorization: Bearer <fixture token>`；匿名握手一律 401——证明
    "认证 discovery"是可验证的契约，而不是只保护 tools/call。`/admin/*` 控制面
    例外（本机测试进程用）。
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    mcp_asgi = _build_mcp().streamable_http_app()
    LIST_REQUESTS = {"count": 0, "anonymous": 0}

    async def auth_gate(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if path.startswith("/admin"):
            await mcp_asgi(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token not in FIXTURE_TOKENS:
            LIST_REQUESTS["anonymous"] += 1
            from starlette.responses import PlainTextResponse

            await PlainTextResponse("unauthorized fixture subject", status_code=401)(
                scope, receive, send
            )
            return
        LIST_REQUESTS["count"] += 1
        await mcp_asgi(scope, receive, send)

    async def ping(request: Any) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def calls(request: Any) -> JSONResponse:
        subject = request.query_params.get("subject", "")
        return JSONResponse({"count": len(CALLS.get(subject, [])), "all": CALLS})

    async def reset(request: Any) -> JSONResponse:
        reset_state()
        return JSONResponse({"ok": True})

    async def flaky(request: Any) -> JSONResponse:
        body = await request.json()
        FLAKY_REMAINING["count"] = int(body.get("count", 0))
        return JSONResponse({"ok": True})

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: Any):
        async with mcp_asgi.router.lifespan_context(mcp_asgi):
            yield

    async def lists(request: Any) -> JSONResponse:
        return JSONResponse(LIST_REQUESTS)

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/admin/ping", ping),
            Route("/admin/calls", calls),
            Route("/admin/lists", lists),
            Route("/admin/reset", reset, methods=["POST"]),
            Route("/admin/flaky", flaky, methods=["POST"]),
            Mount("/", auth_gate),
        ],
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FixtureServer:
    """子进程 fixture：start/stop + 控制面（计数、重置、flaky 注入）."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self.port = 0
        self.url = ""

    async def start(self) -> str:
        self.port = _free_port()
        env = dict(os.environ)
        tests_dir = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = tests_dir + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_fixture.server_process", str(self.port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        async with httpx.AsyncClient() as client:
            deadline = time.monotonic() + 25
            while True:
                try:
                    res = await client.get(f"http://127.0.0.1:{self.port}/admin/ping")
                    if res.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("fixture server 未启动")
                await asyncio.sleep(0.1)
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        return self.url

    async def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._proc.wait, 5), timeout=8)
            except (TimeoutError, Exception):
                self._proc.kill()
                await asyncio.to_thread(self._proc.wait, 5)
            self._proc = None

    async def reset(self) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(f"http://127.0.0.1:{self.port}/admin/reset")

    async def set_flaky(self, count: int) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"http://127.0.0.1:{self.port}/admin/flaky", json={"count": count}
            )

    async def call_count(self, subject: str) -> int:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"http://127.0.0.1:{self.port}/admin/calls",
                params={"subject": subject},
            )
            return int(res.json()["count"])
