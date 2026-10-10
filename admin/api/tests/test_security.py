"""app.security 的纯单元测试（不依赖数据库，不 spawn 进程）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from app.security import (
    SessionPayload,
    build_body,
    hash_password,
    sign_body,
    sign_session,
    verify_password,
    verify_session,
)

SECRET = "unit-test-secret"
USER_ID = "0192f3a4-5b6c-7d8e-9f00-112233445566"
ACCOUNT = "root_user-1"
EXP = 1893456000


class TestPassword:
    def test_hash_and_verify(self) -> None:
        h = hash_password("root")
        assert h != "root"
        assert h.startswith("$2")
        assert verify_password("root", h)

    def test_wrong_password_rejected(self) -> None:
        assert not verify_password("nope", hash_password("root"))

    def test_same_password_different_salt(self) -> None:
        assert hash_password("root") != hash_password("root")

    def test_corrupt_hash_does_not_raise(self) -> None:
        assert verify_password("root", "not-a-bcrypt-hash") is False


class TestTokenFormat:
    def test_token_is_two_base64url_parts(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        payload, signature = token.split(".")
        assert "=" not in payload and "=" not in signature
        assert "+" not in payload and "/" not in payload
        assert "+" not in signature and "/" not in signature

    def test_body_layout_is_userid_account_exp(self) -> None:
        assert build_body(USER_ID, ACCOUNT, EXP) == f"{USER_ID}.{ACCOUNT}.{EXP}"


class TestVerify:
    def test_roundtrip(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        assert verify_session(token, SECRET) == SessionPayload(USER_ID, ACCOUNT, EXP)

    def test_sign_session_uses_future_expiry(self) -> None:
        payload = verify_session(sign_session(USER_ID, ACCOUNT, SECRET, ttl=60), SECRET)
        assert payload is not None
        assert payload.exp > time.time()

    def test_wrong_secret(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        assert verify_session(token, "other-secret") is None

    def test_empty_secret_rejected(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        assert verify_session(token, "") is None

    def test_tampered_payload_rejected(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        _, signature = token.split(".")
        forged = sign_body(build_body("attacker-id", ACCOUNT, EXP), SECRET).split(".")[0]
        assert verify_session(f"{forged}.{signature}", SECRET) is None

    def test_tampered_account_rejected(self) -> None:
        token = sign_body(build_body(USER_ID, ACCOUNT, EXP), SECRET)
        _, signature = token.split(".")
        forged = sign_body(build_body(USER_ID, "admin", EXP), SECRET).split(".")[0]
        assert verify_session(f"{forged}.{signature}", SECRET) is None

    def test_expired_rejected(self) -> None:
        assert verify_session(sign_body(build_body(USER_ID, ACCOUNT, 1), SECRET), SECRET) is None

    @pytest.mark.parametrize(
        "bad",
        [None, "", "no-dot", "a.b.c", "!!!!.!!!!", "abc.", ".abc", "e30.e30"],
    )
    def test_malformed_rejected(self, bad: str | None) -> None:
        assert verify_session(bad, SECRET) is None

    def test_non_ascii_body_cannot_be_signed(self) -> None:
        """中文绝不该出现在 token 里：JS 侧 btoa 会抛异常，所以签名时就要报明确错误。"""
        with pytest.raises(ValueError, match="ASCII"):
            sign_body("uid.北京.1893456000", SECRET)

    def test_non_ascii_payload_rejected_on_verify(self) -> None:
        """即使有人手工构造出签名合法但非 ASCII 的 token，也必须拒绝。"""
        body_bytes = "uid.北京.1893456000".encode()
        signature = hmac.new(SECRET.encode("utf-8"), body_bytes, hashlib.sha256).digest()
        token = f"{base64.urlsafe_b64encode(body_bytes).decode().rstrip('=')}."
        token += base64.urlsafe_b64encode(signature).decode().rstrip("=")
        assert verify_session(token, SECRET) is None

    def test_dotted_account_cannot_be_signed(self) -> None:
        """account 含点会把 body 切成 4 段，必须在签名时就拦住。"""
        with pytest.raises(ValueError, match=r"\."):
            build_body(USER_ID, "a.b", EXP)

    def test_multi_segment_body_rejected(self) -> None:
        token = sign_body("uid.a.b.1893456000", SECRET)
        assert verify_session(token, SECRET) is None
