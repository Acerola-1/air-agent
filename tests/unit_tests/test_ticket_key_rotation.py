"""R09 回归（轮次01 修复）：运行票据 key ID 必须稳定且显式。

修复前断言：`RUNTIME_TICKET_KEYS` 以字符串数组按数组位置生成 `v1..vN`；
把旧密钥从 `[old]` 调整为 `[new, old]` 后旧密钥被重编号为 v2，
用 v1 签发的旧票据验签失败（评审 runtime/admin 反例
`previous_ticket_valid_during_rotation=false`）。
"""

from __future__ import annotations

import json

import pytest

from common.identity.ticket import TicketError, parse_keys, verify_ticket

OLD_KID = "k-old"
NEW_KID = "k-new"


def _sign_manual(kid: str, secret: str, *, iat: int, exp: int) -> str:
    """以 admin 侧 rt-v1 协议形状手工构造票据（等价于 sign_runtime_ticket）。"""
    import base64
    import hashlib
    import hmac

    payload = {
        "typ": "rt-v1",
        "sid": "00000000-0000-0000-0000-000000000001",
        "uid": "00000000-0000-0000-0000-000000000002",
        "tid": "00000000-0000-0000-0000-000000000003",
        "mid": "00000000-0000-0000-0000-000000000004",
        "aud": "langgraph-runtime",
        "iat": iat,
        "exp": exp,
        "crev": 1,
        "urev": 0,
        "trev": 1,
        "mrev": 1,
    }
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        .decode("ascii")
        .rstrip("=")
    )
    sig = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"


def test_parse_keys_accepts_stable_named_kids() -> None:
    """对象/数组映射形态：kid 与密钥绑定，位置无关。"""
    raw = json.dumps({NEW_KID: "secret-new", OLD_KID: "secret-old"})
    keys = parse_keys(raw)
    assert dict(keys)[OLD_KID] == "secret-old"
    assert dict(keys)[NEW_KID] == "secret-new"


def test_old_ticket_valid_when_new_key_prepended() -> None:
    """配置从 [old] 变为 [new, old]（顺序/数量变化）：v1 时代签发的旧票据必须可验。"""
    import time

    now = int(time.time())
    old_ticket = _sign_manual(OLD_KID, "secret-old", iat=now - 5, exp=now + 55)

    # 修复前的数组位置编号：old 从 v1 变成 v2 → 旧票据 kid=v1 直接失效。
    keys_after = parse_keys(json.dumps({NEW_KID: "secret-new", OLD_KID: "secret-old"}))
    claims = verify_ticket(
        old_ticket, keys_after, expected_audience="langgraph-runtime"
    )
    assert claims.session_id.startswith("00000000-0000-0000-0000-000000000001")


def test_withdrawn_key_no_longer_verifies() -> None:
    """撤去旧 key 后旧票据必须拒绝。"""
    import time

    now = int(time.time())
    old_ticket = _sign_manual(OLD_KID, "secret-old", iat=now - 5, exp=now + 55)
    keys = parse_keys(json.dumps({NEW_KID: "secret-new"}))
    with pytest.raises(TicketError):
        verify_ticket(old_ticket, keys, expected_audience="langgraph-runtime")


def test_legacy_array_format_still_parses_with_index_kids() -> None:
    """过渡兼容：旧字符串数组仍可用（索引 kid），显式 kid 为首选部署形态。"""
    keys = parse_keys('["a", "b"]')
    assert keys == [("v1", "a"), ("v2", "b")]


def test_invalid_key_config_fails_closed() -> None:
    for raw in ('{"": "secret"}', '[""]', '{"kid": 5}', "not-json"):
        with pytest.raises(TicketError):
            parse_keys(raw)
