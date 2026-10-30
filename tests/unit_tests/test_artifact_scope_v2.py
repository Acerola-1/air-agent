"""归档 scope v2 协议回归（版本协议、跨租户重放、篡改、旧 scope 拒绝）."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from common.artifact_scope import (
    ARCHIVE_AUDIENCE,
    runtime_scope,
    sign_scope,
    verify_scope,
)

TENANT = "tenant-aaaa-1111"
USER = "user-bbbb-2222"
THREAD = "thread-cccc-3333"
KEY = "archive-test-key"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHIVE_SCOPE_KEYS", json.dumps([KEY, "archive-previous-key"]))


def _legacy_v1_token(user_id: str, thread_id: str, secret: str, exp: int) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"userId": user_id, "threadId": thread_id, "exp": exp}).encode()
        )
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def test_v2_roundtrip() -> None:
    token = sign_scope(TENANT, USER, THREAD)
    scope = verify_scope(token, THREAD)
    assert scope["tenantId"] == TENANT
    assert scope["userId"] == USER
    assert scope["v"] == 2


def test_v1_rejected() -> None:
    token = _legacy_v1_token(USER, THREAD, KEY, int(time.time()) + 600)
    with pytest.raises(PermissionError):
        verify_scope(token, THREAD)


def test_wrong_thread_rejected() -> None:
    token = sign_scope(TENANT, USER, THREAD)
    with pytest.raises(PermissionError):
        verify_scope(token, "other-thread")


def test_tampered_payload_rejected() -> None:
    token = sign_scope(TENANT, USER, THREAD)
    body, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    payload["tenantId"] = "victim-tenant"
    forged = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    with pytest.raises(PermissionError):
        verify_scope(f"{forged}.{sig}", THREAD)


def test_expired_rejected() -> None:
    token = sign_scope(TENANT, USER, THREAD, ttl=-1)
    with pytest.raises(PermissionError):
        verify_scope(token, THREAD)


def test_wrong_audience_rejected() -> None:
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 2,
                    "tenantId": TENANT,
                    "userId": USER,
                    "threadId": THREAD,
                    "aud": "other-audience",
                    "exp": int(time.time()) + 600,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(PermissionError):
        verify_scope(f"{body}.{sig}", THREAD)


def test_key_rotation_accepts_both() -> None:
    current = sign_scope(TENANT, USER, THREAD)
    # 用上一密钥手工签发
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 2,
                    "kid": "k2",
                    "tenantId": TENANT,
                    "userId": USER,
                    "threadId": THREAD,
                    "aud": ARCHIVE_AUDIENCE,
                    "exp": int(time.time()) + 600,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(b"archive-previous-key", body.encode(), hashlib.sha256).hexdigest()
    assert verify_scope(current, THREAD)["tenantId"] == TENANT
    assert verify_scope(f"{body}.{sig}", THREAD)["tenantId"] == TENANT


def test_missing_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCHIVE_SCOPE_KEYS", raising=False)
    with pytest.raises(PermissionError):
        verify_scope("anything.here", THREAD)
    with pytest.raises(PermissionError):
        sign_scope(TENANT, USER, THREAD)


def test_runtime_scope_cross_checks_auth_user() -> None:
    token = sign_scope(TENANT, USER, THREAD)
    config = {
        "configurable": {
            "thread_id": THREAD,
            "__artifact_scope": token,
            "processing_run_id": "run-1",
            "langgraph_auth_user": {
                "identity": USER,
                "tenant_id": TENANT,
                "membership_id": "m-1",
                "session_id": "s-1",
            },
        }
    }
    assert runtime_scope(config) == (TENANT, USER, THREAD, "run-1")
    # 认证身份与 scope 主体不一致（跨租户重放合法签名的场景）
    config["configurable"]["langgraph_auth_user"] = {
        "identity": USER,
        "tenant_id": "victim-tenant",
        "membership_id": "m-1",
        "session_id": "s-1",
    }
    with pytest.raises(PermissionError):
        runtime_scope(config)


def test_runtime_scope_requires_run_id() -> None:
    token = sign_scope(TENANT, USER, THREAD)
    with pytest.raises(PermissionError):
        runtime_scope(
            {"configurable": {"thread_id": THREAD, "__artifact_scope": token}}
        )
