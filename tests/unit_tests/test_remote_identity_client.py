"""remote IAM 客户端纯契约测试（G1.1，A01–A04 的受控 HTTP 部分）.

- A01：模式选择（非法值失败关闭；local/remote 显式）；
- A02：严格 DTO（HTTP200 形状非法不放行；bool/字符串修订拒绝）；
- A04：故障映射（超时/断网/401/429/503 → IdentityBackendUnavailable 503 语义）。
真实 IAM HTTP 正例在隔离环境联调中另行覆盖（g1 联调脚本）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from common.identity import remote
from common.identity.db import IdentityBackendUnavailable
from common.identity.remote import (
    IdentityBackendConfigError,
    RemoteIdentityRejected,
    RemoteProtocolError,
    introspect_subject_token,
    parse_human_identity,
    parse_recheck,
    recheck_execution_context,
)

CONFIG = {
    "IAM_BASE_URL": "http://127.0.0.1:50100",
    "IAM_ISSUER": "air-agent-iam",
    "IAM_INTERNAL_CLIENT_ID": "client",
    "IAM_INTERNAL_CLIENT_SECRET": "secret",
    "IAM_RUNTIME_AUDIENCE": "langgraph-runtime",
}


def _identity_payload(**overrides):  # noqa: ANN003
    payload = {
        "schema_version": "iam.identity.v1",
        "active": True,
        "issuer": "air-agent-iam",
        "audience": "langgraph-runtime",
        "principal": {
            "type": "human",
            "id": "11111111-1111-4111-8111-111111111111",
            "realm": "workforce",
        },
        "user": {
            "id": "11111111-1111-4111-8111-111111111111",
            "account": "acct",
            "display_name": "n",
            "status": "active",
        },
        "session": {
            "id": "44444444-4444-4444-8444-444444444444",
            "ctx_version": 2,
            "created_at": "2026-10-16T00:00:00Z",
            "expires_at": "2026-10-17T00:00:00Z",
            "absolute_expires_at": "2026-10-17T00:00:00Z",
            "idle_expires_at": None,
        },
        "current_tenant": {
            "id": "22222222-2222-4222-8222-222222222222",
            "code": "c",
            "name": "n",
            "status": "active",
        },
        "membership": {
            "id": "33333333-3333-4333-8333-333333333333",
            "tenant_id": "22222222-2222-4222-8222-222222222222",
            "department_id": None,
            "status": "active",
            "revision": 1,
        },
        "platform_roles": [],
        "tenant_roles": [],
        "permissions": ["agent:operations:read"],
        "revisions": {
            "user_auth_revision": 0,
            "tenant_auth_revision": 1,
            "membership_revision": 1,
            "session_ctx_version": 2,
        },
        "context_version": 2,
        "platform_admin": False,
        "context_usable": True,
        "needs_tenant_selection": False,
        "evaluated_at": "2026-10-16T00:00:00Z",
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------- A01 模式选择


def test_invalid_backend_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "auto-fallback")
    with pytest.raises(IdentityBackendConfigError):
        remote.identity_backend_mode()


def test_default_and_explicit_local(monkeypatch):
    monkeypatch.delenv("AGENT_IDENTITY_BACKEND", raising=False)
    assert remote.identity_backend_mode() == "local"
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "LOCAL")
    assert remote.identity_backend_mode() == "local"
    assert not remote.is_remote()


def test_remote_missing_config_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    for key in (
        "IAM_BASE_URL",
        "IAM_ISSUER",
        "IAM_INTERNAL_CLIENT_ID",
        "IAM_INTERNAL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(IdentityBackendConfigError):
        remote.require_remote_config()
    with pytest.raises(IdentityBackendConfigError):
        introspect_subject_token("x" * 20, expected_audience="langgraph-runtime")


# ------------------------------------------------------------- A02 严格 DTO


def test_parse_identity_happy_path():
    ident = parse_human_identity(
        _identity_payload(),
        expected_issuer="air-agent-iam",
        expected_audience="langgraph-runtime",
    )
    assert ident.user_id == "11111111-1111-4111-8111-111111111111"
    assert ident.tenant_id == "22222222-2222-4222-8222-222222222222"
    assert ident.permissions == frozenset({"agent:operations:read"})
    rt = ident.to_runtime_identity()
    assert rt.session_ctx_version == 2 and rt.user_auth_revision == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version="iam.introspection.v1"),
        lambda p: p.update(active=False),
        lambda p: p.update(issuer="other"),
        lambda p: p.update(audience="knowledge"),
        lambda p: p["principal"].update(type="service_account"),
        lambda p: p["user"].update(id="other"),
        lambda p: p["revisions"].update(user_auth_revision=True),  # bool 冒充整数
        lambda p: p["revisions"].update(user_auth_revision="1"),  # 字符串修订
        lambda p: p["revisions"].pop("membership_revision"),
        lambda p: p.update(context_version=3),  # 与 session_ctx_version 不一致
        lambda p: p.update(permissions=["a", "a"]),  # 重复
        lambda p: p.pop("session"),
        lambda p: p["session"].pop("ctx_version"),
        lambda p: p["current_tenant"].update(status="suspended"),
        lambda p: p.update(context_usable="yes"),
    ],
)
def test_parse_identity_rejects_malformed(mutate):
    payload = _identity_payload()
    mutate(payload)
    with pytest.raises(RemoteProtocolError):
        parse_human_identity(
            payload,
            expected_issuer="air-agent-iam",
            expected_audience="langgraph-runtime",
        )


def test_parse_recheck_shape():
    active, matches, revs = parse_recheck(
        {
            "active": True,
            "context_matches": True,
            "revisions": {
                "user_auth_revision": 0,
                "tenant_auth_revision": 1,
                "membership_revision": 1,
                "session_ctx_version": 2,
            },
            "evaluated_at": "2026-10-16T00:00:00Z",
        }
    )
    assert active and matches and revs["session_ctx_version"] == 2
    with pytest.raises(RemoteProtocolError):
        parse_recheck({"active": "true", "context_matches": True, "revisions": {}})


# ------------------------------------------------------------- A04 故障映射


def _client_with(handler) -> None:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        transport=transport,
        base_url=CONFIG["IAM_BASE_URL"],
        auth=(CONFIG["IAM_INTERNAL_CLIENT_ID"], CONFIG["IAM_INTERNAL_CLIENT_SECRET"]),
    )
    # 直接替换共享客户端，避免真实网络（形状与 _shared_client 构造一致）
    remote._CLIENT = client  # noqa: SLF001 - 测试注入


def test_transport_error_maps_to_unavailable(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)

    def handler(request):  # noqa: ANN001
        raise httpx.ConnectError("boom")

    _client_with(handler)
    with pytest.raises(IdentityBackendUnavailable):
        introspect_subject_token("t" * 20, expected_audience="langgraph-runtime")
    remote.close_shared_client()


def test_timeout_maps_to_unavailable(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)

    def handler(request):  # noqa: ANN001
        raise httpx.ReadTimeout("slow")

    _client_with(handler)
    with pytest.raises(IdentityBackendUnavailable):
        recheck_execution_context(
            "ref",
            {
                "user_auth_revision": 0,
                "tenant_auth_revision": 0,
                "membership_revision": 0,
                "session_ctx_version": 1,
            },
            expected_audience="langgraph-runtime",
        )
    remote.close_shared_client()


def test_http_401_internal_client_maps_to_unavailable(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)
    _client_with(
        lambda request: httpx.Response(401, json={"error": {"code": "unauthenticated"}})
    )
    with pytest.raises(IdentityBackendUnavailable):
        introspect_subject_token("t" * 20, expected_audience="langgraph-runtime")
    remote.close_shared_client()


def test_inactive_envelope_maps_to_rejected(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)
    _client_with(
        lambda request: httpx.Response(
            200,
            json={
                "schema_version": "iam.introspection.v1",
                "active": False,
                "reason": "invalid_or_revoked",
            },
        )
    )
    with pytest.raises(RemoteIdentityRejected):
        introspect_subject_token("t" * 20, expected_audience="langgraph-runtime")
    remote.close_shared_client()


def test_malformed_json_maps_to_protocol_error(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)
    _client_with(lambda request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(RemoteProtocolError):
        introspect_subject_token("t" * 20, expected_audience="langgraph-runtime")
    remote.close_shared_client()


def test_recheck_inactive_and_mismatch(monkeypatch):
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)
    revisions = {
        "user_auth_revision": 0,
        "tenant_auth_revision": 0,
        "membership_revision": 0,
        "session_ctx_version": 1,
    }
    _client_with(
        lambda request: httpx.Response(
            200,
            json={"active": False, "context_matches": False, "revisions": revisions},
        )
    )
    with pytest.raises(RemoteIdentityRejected):
        recheck_execution_context(
            "ref", revisions, expected_audience="langgraph-runtime"
        )
    _client_with(
        lambda request: httpx.Response(
            200, json={"active": True, "context_matches": False, "revisions": revisions}
        )
    )
    with pytest.raises(remote.RemoteContextMismatch):
        recheck_execution_context(
            "ref", revisions, expected_audience="langgraph-runtime"
        )
    remote.close_shared_client()


def test_valid_payload_roundtrip_through_http(monkeypatch):
    """HTTP 层正例：严格解析后的身份与 G0 探针形状一致（受控 transport）."""
    monkeypatch.setattr(remote, "require_remote_config", lambda: CONFIG)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_identity_payload())

    _client_with(handler)
    ident = introspect_subject_token("t" * 20, expected_audience="langgraph-runtime")
    assert ident.user_id == "11111111-1111-4111-8111-111111111111"
    # Basic 凭据形态（base64 client:secret）
    import base64

    assert captured["auth"] == "Basic " + base64.b64encode(b"client:secret").decode()
    assert captured["body"]["audience"] == "langgraph-runtime"
    remote.close_shared_client()
