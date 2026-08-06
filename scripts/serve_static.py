"""本地静态文件服务器: 托管 src/web/static/ 到 8125 端口.

仅供本地开发使用. 独立于 langgraph-api (后者在 2024 端口).
跨域请求通过 Access-Control-Allow-Origin: * 开放 (本地无安全风险).
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# CORS 开放头, 允许 8125 -> 2024 的前端请求透传到 langgraph-api
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}

# text/* 类的 Content-Type 必须带 charset=utf-8
# 关键: 不带 charset 时, 中文 Mac/Windows 浏览器会按本地默认 (常是 GBK) 解码 UTF-8 文件, 出现乱码.
TEXT_MIME_CHARSET = {
    "text/html":        "text/html; charset=utf-8",
    "text/css":         "text/css; charset=utf-8",
    "text/javascript":  "text/javascript; charset=utf-8",
    "text/plain":       "text/plain; charset=utf-8",
    "application/javascript": "application/javascript; charset=utf-8",
    "application/json": "application/json; charset=utf-8",
    "application/xml":  "application/xml; charset=utf-8",
}


def _resolve_content_type(path: str) -> str:
    """根据文件路径返回带 charset 的 Content-Type."""
    base_ct, _ = mimetypes.guess_type(path)
    if not base_ct:
        return "application/octet-stream"
    if base_ct in TEXT_MIME_CHARSET:
        return TEXT_MIME_CHARSET[base_ct]
    if base_ct.startswith("text/"):
        return f"{base_ct}; charset=utf-8"
    return base_ct


class StaticHandler(BaseHTTPRequestHandler):
    """轻量静态文件 handler, 完全自己控制响应头.

    不基于 SimpleHTTPRequestHandler 是因为后者在 send_head 里 send_header
    然后立刻 end_headers, 等 end_headers 跑到时响应头已经发出, 改不了 Content-Type.
    这里从 do_GET/do_HEAD/do_OPTIONS 直接控制, 干净.
    """

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._serve_file(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        # HEAD = GET 但不返回 body
        self._serve_file(send_body=False)

    def _serve_file(self, send_body: bool) -> None:
        """读取请求路径对应的本地文件, 返回 200 + body (或仅 headers)."""
        # 把 path 限制在 static_dir 范围内, 防越权 (../ 之类)
        static_root = Path(self.server.static_dir).resolve()  # type: ignore[attr-defined]
        # self.path 形如 "/static/app.js" 或 "/" 或 "/static/libs/marked.min.js"
        # 与 web/server.py 的 app.mount("/static", StaticFiles(...)) 保持一致:
        # URL 里的 "/static/" 前缀对应 static_dir 的根
        rel = self.path.split("?", 1)[0].split("#", 1)[0]
        if rel.startswith("/static/"):
            rel = rel[len("/static"):]
        elif rel == "/static" or rel == "/static/":
            rel = "/"
        if rel == "/" or rel == "":
            target = static_root / "index.html"
        else:
            rel_clean = rel.lstrip("/")
            target = (static_root / rel_clean).resolve()
            if not str(target).startswith(str(static_root)):
                self.send_error(403, "Forbidden")
                return

        if not target.is_file():
            self.send_error(404, f"Not found: {self.path}")
            return

        try:
            data = target.read_bytes()
        except OSError as e:
            self.send_error(500, str(e))
            return

        content_type = _resolve_content_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Last-Modified", self.date_time_string(target.stat().st_mtime))
        self.send_header("Cache-Control", "no-cache")
        self._send_cors_headers()
        self.end_headers()
        if send_body:
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass  # 客户端断开, 忽略

    def _send_cors_headers(self) -> None:
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} - {format % args}\n"
        )


class _StaticServer(HTTPServer):
    """在 server 实例上挂一个 static_dir 属性, 供 handler 访问."""

    def __init__(self, addr, handler_cls, static_dir: str) -> None:
        super().__init__(addr, handler_cls)
        self.static_dir = static_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).parent.parent / "src" / "web" / "static"),
        help="静态文件根目录 (默认 src/web/static)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STATIC_PORT", 8125)),
        help="监听端口 (默认 8125)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("STATIC_HOST", "0.0.0.0"),
        help="监听地址 (默认 0.0.0.0)",
    )
    args = parser.parse_args()

    static_dir = Path(args.dir).resolve()
    if not static_dir.is_dir():
        print(f"错误: 静态文件目录不存在: {static_dir}", file=sys.stderr)
        return 1

    server = _StaticServer((args.host, args.port), StaticHandler, str(static_dir))

    print("==========================================")
    print("  Air Agent 静态前端")
    print(f"  http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    print(f"  static_dir: {static_dir}")
    print("  CORS: *  (本地 dev 模式)")
    print("  charset: text/* 类显式 utf-8 (避免中文浏览器按 GBK 解码)")
    print("==========================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[静态服务器] 收到 SIGINT, 退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
