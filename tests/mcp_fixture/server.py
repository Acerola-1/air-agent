"""受控双企业 MCP fixture 服务器（模拟证据，非真实平台）.

行为契约（受控模拟服务；不是任何生产协议或人员授权来源）：
- 每个工具调用必须携带 `Authorization: Bearer <fixture token>`；token → 外部主体
  （subject）由本服务解析，未识别 token 拒绝。
- 工具 `query_air(user_id, region)`：`user_id` 是普通业务参数；新连接模型不再自动
  覆盖主体字段，返回体回显服务端认证主体与收到的 user_id，便于断言"真实认证
  与不伪造参数"。
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
HTTP_FAILURE = {"remaining": 0, "failed_503": 0, "revoke": False}
BARRIER_STATE: dict[str, Any] = {
    "armed": False,
    "wait_event": None,
    "release_event": None,
}
IAM_STATE: dict[str, Any] = {
    "active": True,
    "context_matches": True,
    "membership_revision": 1,
}


def reset_state() -> None:
    CALLS.clear()
    FLAKY_REMAINING["count"] = 0
    HTTP_FAILURE.update(remaining=0, failed_503=0, revoke=False)
    if (
        BARRIER_STATE.get("release_event") is not None
        and not BARRIER_STATE["release_event"].is_set()
    ):
        BARRIER_STATE["release_event"].set()
    BARRIER_STATE["armed"] = False
    BARRIER_STATE["wait_event"] = None
    BARRIER_STATE["release_event"] = None
    IAM_STATE["active"] = True
    IAM_STATE["context_matches"] = True
    IAM_STATE["membership_revision"] = 1


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
    async def helper_get_latest_time(
        user_id: str, region: str, ctx: Context
    ) -> CallToolResult:
        """真实业务 Skill 白名单中的受控工具名，仅供独立调度验收."""
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append(
            {"user_id": user_id, "region": region, "tool": "helper_get_latest_time"}
        )
        return CallToolResult(
            content=[
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "date": "2026-10-15",
                            "auth_subject": subject,
                            "received_user_id": user_id,
                        }
                    ),
                }
            ],
            isError=False,
        )

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
    async def query(user_id: str, region: str, ctx: Context) -> CallToolResult:
        """同名 query 工具（Gate 0 / A03 探针与验收）."""
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append(
            {"user_id": user_id, "region": region, "tool": "query"}
        )
        payload = {
            "code": 200,
            "success": True,
            "auth_subject": subject,
            "received_user_id": user_id,
            "tool": "query",
            "data": {**DATA.get(subject, {}), "region": region, "from_query": True},
        }
        return CallToolResult(
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            isError=False,
        )

    @app.tool()
    async def slow_query(
        user_id: str,
        region: str,
        delay_ms: int,
        ctx: Context,
        barrier: bool = False,
    ) -> CallToolResult:
        """可控延迟查询（并发交错与确定性请求屏障测试）."""
        if barrier and BARRIER_STATE["armed"]:
            if BARRIER_STATE["wait_event"] is not None:
                BARRIER_STATE["wait_event"].set()
            if BARRIER_STATE["release_event"] is not None:
                await BARRIER_STATE["release_event"].wait()
        elif delay_ms > 0:
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

    @app.tool()
    async def delayed_query(user_id: str, ctx: Context) -> CallToolResult:
        """受控取消探针，不用于真实平台."""
        subject = _identity_from_ctx(ctx)
        CALLS.setdefault(subject, []).append({"user_id": user_id, "delayed": True})
        await asyncio.sleep(2)
        return CallToolResult(
            content=[
                {"type": "text", "text": json.dumps({"success": True, "data": [1]})}
            ]
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
        if scope.get("method") == "POST" and HTTP_FAILURE["remaining"]:
            from starlette.requests import Request

            body = await Request(scope, receive).body()
            try:
                message = json.loads(body)
            except (ValueError, TypeError):
                message = {}
            if message.get("method") == "tools/call":
                HTTP_FAILURE["remaining"] -= 1
                HTTP_FAILURE["failed_503"] += 1
                if HTTP_FAILURE["revoke"]:
                    IAM_STATE["active"] = False
                from starlette.responses import PlainTextResponse

                await PlainTextResponse(
                    "controlled transport failure", status_code=503
                )(scope, receive, send)
                return
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await mcp_asgi(scope, replay_receive, send)
            return
        await mcp_asgi(scope, receive, send)

    async def ping(request: Any) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def calls(request: Any) -> JSONResponse:
        subject = request.query_params.get("subject", "")
        tool = request.query_params.get("tool", "")
        if subject:
            items = list(CALLS.get(subject, []))
        else:
            items = [c for sub in CALLS.values() for c in sub]
        if tool:
            items = [
                c
                for c in items
                if c.get("tool") == tool or (tool == "query_air" and "tool" not in c)
            ]
        return JSONResponse({"count": len(items), "all": CALLS})

    async def reset(request: Any) -> JSONResponse:
        reset_state()
        return JSONResponse({"ok": True})

    async def flaky(request: Any) -> JSONResponse:
        body = await request.json()
        FLAKY_REMAINING["count"] = int(body.get("count", 0))
        return JSONResponse({"ok": True})

    async def http_failure(request: Any) -> JSONResponse:
        body = await request.json()
        HTTP_FAILURE.update(
            remaining=int(body.get("count", 0)),
            revoke=bool(body.get("revoke", False)),
        )
        return JSONResponse({"ok": True})

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: Any):
        async with mcp_asgi.router.lifespan_context(mcp_asgi):
            yield

    async def lists(request: Any) -> JSONResponse:
        return JSONResponse({**LIST_REQUESTS, "failed_503": HTTP_FAILURE["failed_503"]})

    async def barrier_arm(request: Any) -> JSONResponse:
        BARRIER_STATE["armed"] = True
        BARRIER_STATE["wait_event"] = asyncio.Event()
        BARRIER_STATE["release_event"] = asyncio.Event()
        return JSONResponse({"ok": True, "armed": True})

    async def barrier_wait_arrived(request: Any) -> JSONResponse:
        event = BARRIER_STATE["wait_event"]
        if event is None:
            return JSONResponse(
                {"arrived": False, "error": "not_armed"}, status_code=400
            )
        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
            return JSONResponse({"ok": True, "arrived": True})
        except TimeoutError:
            return JSONResponse(
                {"ok": False, "arrived": False, "error": "timeout"}, status_code=504
            )

    async def barrier_release(request: Any) -> JSONResponse:
        event = BARRIER_STATE["release_event"]
        if event is not None:
            event.set()
        return JSONResponse({"ok": True, "released": True})

    async def iam_introspect(request: Any) -> JSONResponse:
        body = await request.json()
        exec_ctx = body.get("execution_context", {})
        expected_revs = exec_ctx.get("expected_revisions", {})
        if not IAM_STATE["active"]:
            return JSONResponse({"active": False, "context_matches": False})
        matches = IAM_STATE["context_matches"]
        current_rev = IAM_STATE["membership_revision"]
        expected_rev = expected_revs.get("membership_revision", 1)
        if current_rev != expected_rev:
            matches = False
        revisions = {
            "user_auth_revision": expected_revs.get("user_auth_revision", 1),
            "tenant_auth_revision": expected_revs.get("tenant_auth_revision", 1),
            "membership_revision": current_rev,
            "session_ctx_version": expected_revs.get("session_ctx_version", 1),
        }
        return JSONResponse(
            {
                "active": True,
                "context_matches": matches,
                "revisions": revisions,
            }
        )

    async def iam_set_state(request: Any) -> JSONResponse:
        body = await request.json()
        if "active" in body:
            IAM_STATE["active"] = bool(body["active"])
        if "context_matches" in body:
            IAM_STATE["context_matches"] = bool(body["context_matches"])
        if "membership_revision" in body:
            IAM_STATE["membership_revision"] = int(body["membership_revision"])
        return JSONResponse({"ok": True, "state": IAM_STATE})

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/admin/ping", ping),
            Route("/admin/calls", calls),
            Route("/admin/lists", lists),
            Route("/admin/reset", reset, methods=["POST"]),
            Route("/admin/flaky", flaky, methods=["POST"]),
            Route("/admin/http-failure", http_failure, methods=["POST"]),
            Route("/admin/barrier/arm", barrier_arm, methods=["POST"]),
            Route("/admin/barrier/wait_arrived", barrier_wait_arrived),
            Route("/admin/barrier/release", barrier_release, methods=["POST"]),
            Route("/admin/iam/state", iam_set_state, methods=["POST"]),
            Route("/v1/internal/identity/introspect", iam_introspect, methods=["POST"]),
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

    async def call_count(self, subject: str = "", tool: str = "") -> int:
        async with httpx.AsyncClient() as client:
            params: dict[str, str] = {}
            if subject:
                params["subject"] = subject
            if tool:
                params["tool"] = tool
            res = await client.get(
                f"http://127.0.0.1:{self.port}/admin/calls",
                params=params,
            )
            return int(res.json()["count"])

    async def arm_barrier(self) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(f"http://127.0.0.1:{self.port}/admin/barrier/arm")

    async def protocol_counts(self) -> dict[str, int]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{self.port}/admin/lists")
            response.raise_for_status()
            return response.json()

    async def fail_next_http_call(self, *, revoke: bool = False) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{self.port}/admin/http-failure",
                json={"count": 1, "revoke": revoke},
            )
            response.raise_for_status()

    async def wait_barrier_arrived(self) -> None:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"http://127.0.0.1:{self.port}/admin/barrier/wait_arrived",
                timeout=12.0,
            )
            assert res.json().get("arrived") is True

    async def release_barrier(self) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(f"http://127.0.0.1:{self.port}/admin/barrier/release")

    async def set_iam_state(
        self,
        *,
        active: bool | None = None,
        context_matches: bool | None = None,
        membership_revision: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if active is not None:
            payload["active"] = active
        if context_matches is not None:
            payload["context_matches"] = context_matches
        if membership_revision is not None:
            payload["membership_revision"] = membership_revision
        async with httpx.AsyncClient() as client:
            await client.post(
                f"http://127.0.0.1:{self.port}/admin/iam/state", json=payload
            )
