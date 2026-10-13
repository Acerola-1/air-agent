"""统一会话与运行票据协议单测（不依赖数据库）。

覆盖任务 3.1/3.3 的纯函数面：不透明 token 生成/摘要、cookie 解析（旧 HMAC
识别但不授予）、运行票据签发/验签（篡改、未知 kid、密钥轮换）、票据内容
无业务凭据、Origin 校验、密码哈希。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from app.security import (
    LegacySessionPayload,
    RuntimeTicket,
    _legacy_verify,
    compose_session_cookie,
    hash_password,
    new_session_token,
    origin_allowed,
    parse_session_cookie,
    sign_runtime_ticket,
    token_hash_of,
    verify_password,
    verify_runtime_ticket,
)

SECRET = "unit-test-secret"
ALLOWED = ["http://localhost:3000", "http://localhost:3100"]


# --------------------------------------------------------------------- 密码


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_password_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


# --------------------------------------------------------------------- 不透明会话


def test_new_session_token_shape() -> None:
    session_id, raw, digest = new_session_token()
    uuid.UUID(session_id)
    assert len(raw) >= 32
    assert digest == token_hash_of(raw) == hashlib.sha256(raw.encode()).hexdigest()
    # 原始 token 绝不等于摘要，也不可从摘要反推
    assert raw not in digest and digest not in raw


def test_session_tokens_unique() -> None:
    a = new_session_token()[1]
    b = new_session_token()[1]
    assert a != b


def test_parse_session_cookie() -> None:
    sid = str(uuid.uuid4())
    cookie = compose_session_cookie(sid, "opaque-token-value")
    parsed_sid, token = parse_session_cookie(cookie)
    assert parsed_sid == sid and token == "opaque-token-value"


def test_parse_rejects_non_uuid_prefix_and_malformed() -> None:
    assert parse_session_cookie(None) == (None, None)
    assert parse_session_cookie("no-dot") == (None, None)
    assert parse_session_cookie("notauuid.token") == (None, None)
    assert parse_session_cookie(f"{uuid.uuid4()}.") == (None, None)


def test_legacy_hmac_still_recognizable_for_migration() -> None:
    """旧 HMAC 格式仍可被迁移工具识别（识别≠授权——授权路径已删除）。"""
    body = f"{uuid.uuid4()}.someuser.{int(time.time()) + 60}"
    payload_b64 = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    sig = (
        base64.urlsafe_b64encode(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    token = f"{payload_b64}.{sig}"
    verified = _legacy_verify(token, SECRET)
    assert isinstance(verified, LegacySessionPayload)
    assert verified.account == "someuser"
    # 新会话解析器绝不把旧格式当作有效会话
    sid, tok = parse_session_cookie(token)
    assert sid is None and tok is None


def test_legacy_verify_rejects_tampered_and_expired() -> None:
    body = f"{uuid.uuid4()}.u.{int(time.time()) + 60}"
    payload_b64 = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    sig = (
        base64.urlsafe_b64encode(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    assert _legacy_verify(f"{payload_b64}.{sig[:-2]}xx", SECRET) is None
    expired_body = f"{uuid.uuid4()}.u.{int(time.time()) - 5}"
    eb64 = base64.urlsafe_b64encode(expired_body.encode()).decode().rstrip("=")
    esig = (
        base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), expired_body.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert _legacy_verify(f"{eb64}.{esig}", SECRET) is None


# --------------------------------------------------------------------- 运行票据


def _ticket(**overrides: object) -> RuntimeTicket:
    now = int(time.time())
    base: dict = {
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "membership_id": str(uuid.uuid4()),
        "audience": "langgraph-runtime",
        "issued_at": now,
        "expires_at": now + 60,
        "session_ctx_version": 3,
        "user_auth_revision": 0,
        "tenant_auth_revision": 1,
        "membership_revision": 2,
        "kid": "v1",
    }
    base.update(overrides)
    return RuntimeTicket(**base)  # type: ignore[arg-type]


def test_ticket_sign_verify_roundtrip() -> None:
    token = sign_runtime_ticket(_ticket(), [("v1", SECRET)])
    payload = verify_runtime_ticket(token, [("v1", SECRET)])
    assert payload is not None
    assert payload["aud"] == "langgraph-runtime"
    assert payload["typ"] == "rt-v1"


def test_ticket_tamper_rejected() -> None:
    token = sign_runtime_ticket(_ticket(), [("v1", SECRET)])
    body, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "==="))
    payload["p"]["tid"] = str(uuid.uuid4())  # 换租户
    forged_body = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    assert verify_runtime_ticket(f"{forged_body}.{sig}", [("v1", SECRET)]) is None


def test_ticket_wrong_secret_rejected() -> None:
    token = sign_runtime_ticket(_ticket(), [("v1", SECRET)])
    assert verify_runtime_ticket(token, [("v1", "other-secret")]) is None


def test_ticket_unknown_kid_rejected() -> None:
    token = sign_runtime_ticket(_ticket(), [("v9", SECRET)])
    assert verify_runtime_ticket(token, [("v1", SECRET)]) is None


def test_ticket_key_rotation_verifies_both() -> None:
    old = sign_runtime_ticket(_ticket(), [("v1", "old-secret")])
    assert verify_runtime_ticket(old, [("v2", "new-secret"), ("v1", "old-secret")])
    new = sign_runtime_ticket(_ticket(), [("v2", "new-secret")])
    assert verify_runtime_ticket(new, [("v2", "new-secret"), ("v1", "old-secret")])


def test_ticket_has_no_business_credentials() -> None:
    token = sign_runtime_ticket(_ticket(), [("v1", SECRET)])
    payload = verify_runtime_ticket(token, [("v1", SECRET)]) or {}
    text = json.dumps(payload)
    for secret_word in ("password", "token_hash", "credential", "secret"):
        assert secret_word not in text.lower()


# --------------------------------------------------------------------- CSRF/Origin


def test_origin_allowed_exact_match() -> None:
    assert origin_allowed("http://localhost:3000", ALLOWED)
    assert not origin_allowed("http://evil.example", ALLOWED)
    assert not origin_allowed("http://localhost:3000.evil.example", ALLOWED)


def test_origin_none_treated_allowed_for_non_browser() -> None:
    assert origin_allowed(None, ALLOWED)
    assert origin_allowed("", ALLOWED)


def test_ticket_key_pairs_object_format_stable_kids() -> None:
    """R09：RUNTIME_TICKET_KEYS 对象形态——kid 与密钥绑定，重排不失效。"""
    from app.config import Settings

    old = sign_runtime_ticket(_ticket(), [("k-old", "secret-old")])
    settings = Settings(
        _env_file=None, runtime_ticket_keys='{"k-new": "secret-new", "k-old": "secret-old"}'
    )
    pairs = settings.runtime_ticket_key_pairs
    assert ("k-old", "secret-old") in pairs
    # 交错部署：新 key 前插后，旧 kid 签发的票据仍可验签
    assert verify_runtime_ticket(old, pairs) is not None
    assert verify_runtime_ticket(old, [("k-old", "secret-old")]) is not None
    # 撤去旧 key 后拒绝
    assert verify_runtime_ticket(old, [("k-new", "secret-new")]) is None


def test_ticket_key_pairs_invalid_config_raises() -> None:
    from app.config import Settings

    for raw in ('{"": "s"}', '{"a": 5}', '["", "x"]', "nope"):
        try:
            pairs = Settings(_env_file=None, runtime_ticket_keys=raw).runtime_ticket_key_pairs
        except ValueError:
            continue
        raise AssertionError(f"invalid config accepted: {raw!r} -> {pairs}")
