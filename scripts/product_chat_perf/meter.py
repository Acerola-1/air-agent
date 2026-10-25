"""Task-owned loopback model relay enforcing physical-attempt token budgets."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from scripts.product_chat_perf.plan import digest
from scripts.product_chat_perf.scheduling import Budget
from scripts.unified_campaign.guard import assert_target_allowed


def create_meter(config: dict[str, Any], budget: Budget) -> ThreadingHTTPServer:
    """Create, but do not start, a bounded relay on a task-owned loopback port."""
    assert_target_allowed(config["upstream_base_url"])
    client_key = os.environ.get(config["client_key_env"], "")
    upstream_key = os.environ.get(config["upstream_key_env"], "")
    if not client_key or not upstream_key:
        raise ValueError("计量器缺少受保护凭据环境变量")
    models = set(config["allowed_models"])
    public_config = {
        key: value for key, value in config.items() if not key.endswith("key_env")
    }
    config_hash = digest(public_config)
    counters = {"inflight": 0, "peak_inflight": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args: Any) -> None:
            return

        def authorized(self) -> bool:
            return self.headers.get("authorization") == f"Bearer {client_key}"

        def reply(self, status: int, value: dict[str, Any]) -> None:
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if not self.authorized():
                self.reply(401, {"error": "meter_unauthorized"})
                return
            if self.path != "/budget":
                self.reply(404, {"error": "not_found"})
                return
            with lock:
                current = dict(counters)
            self.reply(
                200,
                {
                    "run_id": config["run_id"],
                    "configuration_digest": config_hash,
                    "budget": budget.snapshot(),
                    **current,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if not self.authorized():
                self.reply(401, {"error": "meter_unauthorized"})
                return
            if self.path not in {"/chat/completions", "/v1/chat/completions"}:
                self.reply(404, {"error": "unsupported_route"})
                return
            try:
                size = int(self.headers.get("content-length") or "0")
                if size <= 0 or size > 2 * 1024 * 1024:
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                if body.get("model") not in models:
                    raise ValueError
                output_limit = int(
                    body.get("max_completion_tokens")
                    or body.get("max_tokens")
                    or config["max_output_tokens"]
                )
                if output_limit <= 0 or output_limit > int(config["max_output_tokens"]):
                    raise ValueError
                if "max_completion_tokens" not in body:
                    body["max_tokens"] = output_limit
                if body.get("stream"):
                    body["stream_options"] = {
                        **body.get("stream_options", {}),
                        "include_usage": True,
                    }
                reserve = (
                    len(json.dumps(body, ensure_ascii=False).encode())
                    + 512
                    + output_limit
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, {"error": "invalid_frozen_model_request"})
                return
            if not budget.reserve(reserve):
                self.reply(429, {"error": "test_model_budget_exhausted"})
                return
            usage: int | None = None
            with lock:
                counters["inflight"] += 1
                counters["peak_inflight"] = max(
                    counters["peak_inflight"], counters["inflight"]
                )
            try:
                with (
                    httpx.Client(
                        timeout=float(config["request_deadline_seconds"])
                    ) as client,
                    client.stream(
                        "POST",
                        config["upstream_base_url"].rstrip("/") + "/chat/completions",
                        json=body,
                        headers={"authorization": f"Bearer {upstream_key}"},
                    ) as response,
                ):
                    self.send_response(response.status_code)
                    self.send_header(
                        "content-type",
                        response.headers.get("content-type", "application/json"),
                    )
                    self.end_headers()
                    buffered = ""
                    if body.get("stream"):
                        for line in response.iter_lines():
                            if line.startswith("data:"):
                                try:
                                    value = json.loads(line[5:].strip())
                                    if isinstance(value.get("usage"), dict):
                                        usage = value["usage"].get(
                                            "total_tokens", usage
                                        )
                                except (json.JSONDecodeError, AttributeError):
                                    pass
                            self.wfile.write((line + "\n").encode())
                            self.wfile.flush()
                    else:
                        buffered = response.read().decode()
                        try:
                            usage = (json.loads(buffered).get("usage") or {}).get(
                                "total_tokens"
                            )
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        self.wfile.write(buffered.encode())
            except (httpx.HTTPError, BrokenPipeError, ConnectionResetError):
                # No raw upstream exception, payload or credential is logged.
                pass
            finally:
                budget.settle(reserve, usage)
                with lock:
                    counters["inflight"] -= 1

    server = ThreadingHTTPServer(("127.0.0.1", int(config.get("port", 0))), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    """Start an explicitly configured physical-attempt budget relay."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    with open(args.config, encoding="utf-8") as stream:
        config = json.load(stream)
    limits = config["budgets"]
    budget = Budget(
        float(limits["max_seconds"]),
        int(limits["max_requests"]),
        int(limits["max_tokens"]),
    )
    server = create_meter(config, budget)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
