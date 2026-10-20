"""Security and streaming checks for the bounded local host bridge."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from deployment import local_host_bridge as bridge


class UpstreamState:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.first_stream_chunk = threading.Event()
        self.release_stream = threading.Event()
        self.stream_finished = threading.Event()


def _upstream_handler(state: UpstreamState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            with state.lock:
                state.calls.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "content_type": self.headers.get("Content-Type"),
                        "body": body,
                        "headers": dict(self.headers.items()),
                    }
                )
            if self.path == bridge.MODEL_PATH and b'"stream":true' in body:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                first = b"data: first\n\n"
                self.wfile.write(f"{len(first):X}\r\n".encode() + first + b"\r\n")
                self.wfile.flush()
                state.first_stream_chunk.set()
                state.release_stream.wait(5)
                second = b"data: second\n\n"
                self.wfile.write(f"{len(second):X}\r\n".encode() + second + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                state.stream_finished.set()
                return
            response = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()

    return Handler


@pytest.fixture
def bridge_harness(monkeypatch: pytest.MonkeyPatch):
    state = UpstreamState()
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _upstream_handler(state))
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_base = f"http://127.0.0.1:{upstream.server_port}"
    monkeypatch.setattr(
        bridge,
        "IAM_UPSTREAM_URL",
        upstream_base + "/v1/internal/identity/introspect",
    )
    monkeypatch.setattr(
        bridge, "MODEL_UPSTREAM_URL", upstream_base + "/v1/chat/completions"
    )
    config = bridge.BridgeConfig(
        bind_host="127.0.0.1",
        bind_port=1,
        allowed_source_ips=frozenset({"127.0.0.1"}),
        iam_client_id="runtime-client",
        iam_client_secret="test-iam-secret",
        model_bearer="test-model-placeholder",
        allowed_models=frozenset({"test-model"}),
    )
    server = bridge.BridgeHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler, config)
    bridge_thread = threading.Thread(target=server.serve_forever, daemon=True)
    bridge_thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    bridge_thread.join(timeout=2)
    upstream.shutdown()
    upstream.server_close()
    upstream_thread.join(timeout=2)


def _post(
    base_url: str,
    path: str,
    body: bytes,
    authorization: str,
    **headers: str,
) -> httpx.Response:
    request_headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        **headers,
    }
    return httpx.post(
        base_url + path,
        content=body,
        headers=request_headers,
        timeout=3,
        trust_env=False,
    )


def test_rejects_unlisted_source_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    state = UpstreamState()
    config = bridge.BridgeConfig(
        bind_host="127.0.0.1",
        bind_port=1,
        allowed_source_ips=frozenset({"192.168.64.1"}),
        iam_client_id="runtime-client",
        iam_client_secret="secret",
        model_bearer="placeholder",
        allowed_models=frozenset({"test-model"}),
    )
    server = bridge.BridgeHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = _post(
            f"http://127.0.0.1:{server.server_port}",
            bridge.IAM_PATH,
            b'{"audience":"langgraph-runtime"}',
            config.iam_authorization,
        )
        assert response.status_code == 403
        assert not state.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rejects_wrong_credentials_and_never_calls_upstream(bridge_harness) -> None:
    state, base_url = bridge_harness
    response = _post(
        base_url,
        bridge.IAM_PATH,
        b'{"audience":"langgraph-runtime"}',
        "Basic wrong",
    )
    assert response.status_code == 401
    assert state.calls == []


def test_rejects_unlisted_path_query_and_method(bridge_harness) -> None:
    state, base_url = bridge_harness
    valid_auth = "Bearer test-model-placeholder"
    body = b'{"model":"test-model"}'
    assert (
        _post(base_url, "/v1/chat/completions/extra", body, valid_auth).status_code
        == 404
    )
    assert (
        _post(
            base_url, bridge.MODEL_PATH + "?target=elsewhere", body, valid_auth
        ).status_code
        == 404
    )
    response = httpx.get(
        base_url + bridge.MODEL_PATH,
        timeout=3,
        trust_env=False,
    )
    assert response.status_code == 405
    assert state.calls == []


def test_rejects_wrong_audience_and_model(bridge_harness) -> None:
    state, base_url = bridge_harness
    iam = _post(
        base_url,
        bridge.IAM_PATH,
        b'{"audience":"other"}',
        "Basic cnVudGltZS1jbGllbnQ6dGVzdC1pYW0tc2VjcmV0",
    )
    model = _post(
        base_url,
        bridge.MODEL_PATH,
        b'{"model":"unlisted-model"}',
        "Bearer test-model-placeholder",
    )
    non_string_models = [
        _post(
            base_url,
            bridge.MODEL_PATH,
            json.dumps({"model": invalid}).encode(),
            "Bearer test-model-placeholder",
        )
        for invalid in ({}, [])
    ]
    assert iam.status_code == 403
    assert model.status_code == 403
    assert [response.status_code for response in non_string_models] == [400, 400]
    assert state.calls == []


def test_rejects_bad_json_and_body_over_limit(bridge_harness) -> None:
    state, base_url = bridge_harness
    auth = "Bearer test-model-placeholder"
    assert _post(base_url, bridge.MODEL_PATH, b"{bad", auth).status_code == 400
    oversized = b" " * (bridge.MAX_BODY_BYTES + 1)
    assert _post(base_url, bridge.MODEL_PATH, oversized, auth).status_code == 413
    assert state.calls == []


def test_forwards_only_fixed_route_auth_and_content_type(bridge_harness) -> None:
    state, base_url = bridge_harness
    raw_body = b'{"audience":"langgraph-runtime","subject":"opaque"}'
    response = _post(
        base_url,
        bridge.IAM_PATH,
        raw_body,
        "Basic cnVudGltZS1jbGllbnQ6dGVzdC1pYW0tc2VjcmV0",
        **{"X-Secret-Header": "do-not-forward", "X-Target": "http://elsewhere"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"ok":true}'
    assert len(state.calls) == 1
    call = state.calls[0]
    assert call["path"] == bridge.IAM_PATH
    assert call["authorization"] == "Basic cnVudGltZS1jbGllbnQ6dGVzdC1pYW0tc2VjcmV0"
    assert call["content_type"] == "application/json"
    assert call["body"] == raw_body
    forwarded_headers = {key.lower() for key in call["headers"]}
    assert "x-secret-header" not in forwarded_headers
    assert "x-target" not in forwarded_headers


def test_sse_chunks_arrive_before_upstream_finishes(bridge_harness) -> None:
    state, base_url = bridge_harness
    body = json.dumps(
        {"model": "test-model", "stream": True}, separators=(",", ":")
    ).encode()
    state.release_stream.clear()
    try:
        with httpx.stream(
            "POST",
            base_url + bridge.MODEL_PATH,
            content=body,
            headers={
                "Authorization": "Bearer test-model-placeholder",
                "Content-Type": "application/json",
            },
            timeout=3,
            trust_env=False,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream"
            chunks = response.iter_raw()
            first = next(chunks)
            assert first == b"data: first\n\n"
            assert state.first_stream_chunk.is_set()
            assert not state.stream_finished.is_set()
            state.release_stream.set()
            assert b"data: second\n\n" in b"".join(chunks)
        assert state.stream_finished.wait(1)
    finally:
        state.release_stream.set()


def test_bridge_bind_host_accepts_private_gateway_and_rejects_unspecified():
    from deployment import local_host_bridge as bridge

    assert bridge._allowed_bind_host("127.0.0.1")
    assert bridge._allowed_bind_host("192.168.64.1")
    assert bridge._allowed_bind_host("192.168.65.1")
    assert not bridge._allowed_bind_host("0.0.0.0")
    assert not bridge._allowed_bind_host("8.8.8.8")
    assert not bridge._allowed_bind_host("not-an-ip")
