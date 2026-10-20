"""Bounded local bridge for the fixed IAM and model endpoints used by LangGraph.

Run with the Air Agent virtualenv and a private JSON config:
    .venv/bin/python deployment/local_host_bridge.py --config /private/path/bridge.json
"""

from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import os
import socket
import stat
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

IAM_PATH = "/v1/internal/identity/introspect"
KNOWLEDGE_DELEGATION_PATH = "/v1/internal/knowledge-delegations"
MODEL_PATH = "/v1/chat/completions"
IAM_UPSTREAM_URL = "http://127.0.0.1:19100/v1/internal/identity/introspect"
KNOWLEDGE_DELEGATION_UPSTREAM_URL = (
    "http://127.0.0.1:19100/v1/internal/knowledge-delegations"
)
MODEL_UPSTREAM_URL = "http://127.0.0.1:7863/v1/chat/completions"
RAG_UPSTREAM_BASE = "http://127.0.0.1:8099"
RAG_POST_PATHS = frozenset({"/rag/api/query", "/rag/api/knowledge-references/validate"})
RAG_GET_PREFIXES = ("/rag/api/source/", "/rag/api/document/")
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
_CONFIG_KEYS = {
    "bind_host",
    "bind_port",
    "allowed_source_ips",
    "iam_client_id",
    "iam_client_secret",
    "model_bearer",
    "allowed_models",
}
_ROUTE_PATHS = frozenset({IAM_PATH, MODEL_PATH})


def _allowed_bind_host(host: str) -> bool:
    """Allow loopback or a concrete private bridge address, never 0.0.0.0.

    Container networks can move between host bridge subnets after an Apple
    `container` restart; the source-IP allowlist, not the bind address, is the
    authorization boundary.  Refuse unspecified/global/multicast addresses so
    the bridge is never exposed on a public interface by typo.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or (
        address.is_private and not address.is_unspecified and not address.is_multicast
    )


class ConfigError(ValueError):
    """Raised when the private bridge configuration is unsafe or malformed."""


@dataclass(frozen=True)
class BridgeConfig:
    """Validated bridge settings; the upstream destinations are fixed in code."""

    bind_host: str
    bind_port: int
    allowed_source_ips: frozenset[str]
    iam_client_id: str
    iam_client_secret: str = field(repr=False)
    model_bearer: str = field(repr=False)
    allowed_models: frozenset[str] = frozenset()

    @property
    def iam_authorization(self) -> str:
        """Return the fixed Basic credential used only for the IAM upstream."""
        raw = f"{self.iam_client_id}:{self.iam_client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    @property
    def model_authorization(self) -> str:
        """Return the fixed bearer value used only for the model upstream."""
        return "Bearer " + self.model_bearer


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _decode_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_json_constant,
    )


def _nonempty_string(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ConfigError(f"invalid {name}")
    return value


def _parse_config(raw: bytes) -> BridgeConfig:
    try:
        data = _decode_json(raw)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ConfigError("invalid configuration JSON") from exc
    if not isinstance(data, dict) or set(data) != _CONFIG_KEYS:
        raise ConfigError("configuration shape mismatch")

    bind_host = data["bind_host"]
    if not isinstance(bind_host, str) or not _allowed_bind_host(bind_host):
        raise ConfigError("invalid bind_host")
    bind_port = data["bind_port"]
    if (
        isinstance(bind_port, bool)
        or not isinstance(bind_port, int)
        or not 1 <= bind_port <= 65535
    ):
        raise ConfigError("invalid bind_port")

    source_values = data["allowed_source_ips"]
    if not isinstance(source_values, list) or not source_values:
        raise ConfigError("invalid allowed_source_ips")
    try:
        source_ips = [str(ipaddress.ip_address(value)) for value in source_values]
    except (ValueError, TypeError) as exc:
        raise ConfigError("invalid allowed_source_ips") from exc
    if len(set(source_ips)) != len(source_ips):
        raise ConfigError("duplicate allowed_source_ips")

    models_value = data["allowed_models"]
    if not isinstance(models_value, list) or not models_value:
        raise ConfigError("invalid allowed_models")
    models = [_nonempty_string(value, "allowed_models") for value in models_value]
    if len(set(models)) != len(models):
        raise ConfigError("duplicate allowed_models")

    client_id = _nonempty_string(data["iam_client_id"], "iam_client_id")
    client_secret = _nonempty_string(data["iam_client_secret"], "iam_client_secret")
    model_bearer = _nonempty_string(data["model_bearer"], "model_bearer")
    if ":" in client_id or any(char.isspace() for char in model_bearer):
        raise ConfigError("invalid configured credential")

    return BridgeConfig(
        bind_host=bind_host,
        bind_port=bind_port,
        allowed_source_ips=frozenset(source_ips),
        iam_client_id=client_id,
        iam_client_secret=client_secret,
        model_bearer=model_bearer,
        allowed_models=frozenset(models),
    )


def load_config(path: str | os.PathLike[str]) -> BridgeConfig:
    """Read an owner-only regular config file with exact 0600 permissions."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
        ):
            raise ConfigError("configuration file must be owner-only mode 0600")
        with os.fdopen(descriptor, "rb") as config_file:
            descriptor = -1
            raw = config_file.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError("configuration file too large")
        return _parse_config(raw)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class BridgeHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP/1.0 server with one shared, proxy-disabled HTTPX client."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[BaseHTTPRequestHandler],
        config: BridgeConfig,
    ) -> None:
        """Create a listener and an HTTP client that ignores proxy settings."""
        self.config = config
        self.upstream_client = httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
        )
        try:
            super().__init__(server_address, request_handler)
        except Exception:
            self.upstream_client.close()
            raise

    def server_close(self) -> None:
        """Close the listener and its shared upstream client."""
        super().server_close()
        self.upstream_client.close()

    def handle_error(self, request: socket.socket, client_address: Any) -> None:
        """Suppress server tracebacks that could reveal request or runtime data."""


class BridgeHandler(BaseHTTPRequestHandler):
    """Accept only the two fixed POST routes and stream upstream bytes directly."""

    protocol_version = "HTTP/1.0"
    server_version = "LocalHostBridge"
    sys_version = ""

    @property
    def config(self) -> BridgeConfig:
        """Expose the validated settings stored on the server."""
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        """Never log request lines, headers, credentials, or payloads."""

    def log_error(self, format: str, *args: Any) -> None:
        """Never log parser or proxy details."""

    def handle_expect_100(self) -> bool:
        """Reject Expect requests without accepting an unvalidated body."""
        self.close_connection = True
        if not self._source_is_allowed():
            self._send_local(403)
        else:
            self._send_local(417)
        return False

    def _source_is_allowed(self) -> bool:
        try:
            source = str(ipaddress.ip_address(self.client_address[0]))
        except (ValueError, TypeError):
            return False
        return source in self.config.allowed_source_ips

    def _send_local(self, status: int, *, body_allowed: bool = True) -> None:
        body = b"upstream unavailable\n" if status == 502 else b"request rejected\n"
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body) if body_allowed else 0))
        self.send_header("Connection", "close")
        self.end_headers()
        if body_allowed:
            self.wfile.write(body)
            self.wfile.flush()

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        """Return a generic parser error without reflecting request data."""
        self.close_connection = True
        if not self._source_is_allowed():
            code = 403
        elif code == 501:
            code = 405 if self.path in _ROUTE_PATHS else 404
        elif code not in {400, 414, 431}:
            code = 400
        self._send_local(code, body_allowed=getattr(self, "command", "") != "HEAD")

    def _reject_method(self) -> None:
        if not self._source_is_allowed():
            status = 403
        elif self.path in _ROUTE_PATHS:
            status = 405
        else:
            status = 404
        self._send_local(status, body_allowed=self.command != "HEAD")

    def do_GET(self) -> None:
        """Allow read-only knowledge source/document ranges through the bridge."""
        self.close_connection = True
        if not self._source_is_allowed():
            self._send_local(403)
            return
        if not self.path.startswith(RAG_GET_PREFIXES):
            self._send_local(405 if self.path in _ROUTE_PATHS else 404)
            return
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 4096:
            self._send_local(401)
            return
        forwarded = {
            "Authorization": authorization,
            "Accept": self.headers.get("Accept", "*/*"),
        }
        for name in ("X-Knowledge-Remaining-Ms", "Range"):
            value = self.headers.get(name)
            if value:
                forwarded[name] = value
        self._proxy(
            "GET",
            RAG_UPSTREAM_BASE + self.path[len("/rag") :],
            forwarded,
            None,
            None,
        )

    do_HEAD = _reject_method
    do_PUT = _reject_method
    do_DELETE = _reject_method
    do_PATCH = _reject_method
    do_OPTIONS = _reject_method
    do_TRACE = _reject_method
    do_CONNECT = _reject_method

    def do_POST(self) -> None:
        """Validate one permitted request and proxy it to its fixed destination."""
        self.close_connection = True
        if not self._source_is_allowed():
            self._send_local(403)
            return
        if self.path in RAG_POST_PATHS:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer ") or len(authorization) > 4096:
                self._send_local(401)
                return
            content_type = self._request_content_type()
            if content_type is None:
                self._send_local(415)
                return
            body, failure_status = self._read_body()
            if failure_status is not None or body is None:
                self._send_local(failure_status or 400)
                return
            try:
                payload = _decode_json(body)
            except (UnicodeDecodeError, ValueError, TypeError):
                self._send_local(400)
                return
            if not isinstance(payload, dict):
                self._send_local(400)
                return
            headers = {
                "Authorization": authorization,
                "Content-Type": content_type,
                "Accept": "application/json",
            }
            remaining = self.headers.get("X-Knowledge-Remaining-Ms")
            if remaining:
                headers["X-Knowledge-Remaining-Ms"] = remaining
            self._proxy(
                "POST",
                RAG_UPSTREAM_BASE + self.path[len("/rag") :],
                headers,
                content_type,
                body,
            )
            return
        if self.path not in _ROUTE_PATHS and self.path != KNOWLEDGE_DELEGATION_PATH:
            self._send_local(404)
            return

        is_iam = self.path in (IAM_PATH, KNOWLEDGE_DELEGATION_PATH)
        expected_authorization = (
            self.config.iam_authorization if is_iam else self.config.model_authorization
        )
        if not self._authorization_matches(expected_authorization):
            self._send_local(401)
            return

        content_type = self._request_content_type()
        if content_type is None:
            self._send_local(415)
            return
        body, failure_status = self._read_body()
        if failure_status is not None or body is None:
            self._send_local(failure_status or 400)
            return
        try:
            payload = _decode_json(body)
        except (UnicodeDecodeError, ValueError, TypeError):
            self._send_local(400)
            return
        if not isinstance(payload, dict):
            self._send_local(400)
            return
        if is_iam:
            if self.path == IAM_PATH and payload.get("audience") != "langgraph-runtime":
                self._send_local(403)
                return
            upstream_url = (
                IAM_UPSTREAM_URL
                if self.path == IAM_PATH
                else KNOWLEDGE_DELEGATION_UPSTREAM_URL
            )
        else:
            model = payload.get("model")
            if not isinstance(model, str):
                self._send_local(400)
                return
            if model not in self.config.allowed_models:
                self._send_local(403)
                return
            upstream_url = MODEL_UPSTREAM_URL

        self._proxy(
            "POST",
            upstream_url,
            {"Authorization": expected_authorization, "Content-Type": content_type},
            content_type,
            body,
        )

    def _authorization_matches(self, expected: str) -> bool:
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        try:
            received = values[0].encode("ascii")
            expected_bytes = expected.encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(received, expected_bytes)

    def _request_content_type(self) -> str | None:
        values = self.headers.get_all("Content-Type", [])
        if len(values) != 1:
            return None
        value = values[0]
        media_type = value.split(";", 1)[0].strip().lower()
        return value if media_type == "application/json" else None

    def _read_body(self) -> tuple[bytes | None, int | None]:
        if self.headers.get_all("Transfer-Encoding", []):
            return None, 400
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            return None, 400
        value = lengths[0]
        if not value.isascii() or not value.isdecimal():
            return None, 400
        length = int(value)
        if length > MAX_BODY_BYTES:
            return None, 413
        body = self.rfile.read(length)
        if len(body) != length:
            return None, 400
        return body, None

    def _proxy(
        self,
        method: str,
        upstream_url: str,
        headers: dict[str, str],
        content_type: str | None,
        body: bytes | None,
    ) -> None:
        response: httpx.Response | None = None
        headers_started = False
        try:
            request = httpx.Request(
                method,
                upstream_url,
                headers=headers,
                content=body,
            )
            response = self.server.upstream_client.send(request, stream=True)  # type: ignore[attr-defined]
            headers_started = True
            self.send_response_only(response.status_code)
            for name in (
                "content-type",
                "content-disposition",
                "content-range",
                "accept-ranges",
                "x-content-offset",
                "x-content-next-offset",
                "x-content-total-chars",
            ):
                value = response.headers.get(name)
                if value is not None:
                    self.send_header(name.title(), value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            for chunk in response.iter_bytes(chunk_size=None):
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except Exception:
            self.close_connection = True
            if not headers_started:
                try:
                    self._send_local(502)
                except Exception:
                    pass
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass


def main(argv: list[str] | None = None) -> int:
    """Load a private config and serve the loopback/private-bridge listener."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        with BridgeHTTPServer(
            (config.bind_host, config.bind_port), BridgeHandler, config
        ) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (ConfigError, OSError, httpx.HTTPError):
        sys.stderr.write("local host bridge unavailable\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
