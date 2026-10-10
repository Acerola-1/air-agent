"""密码哈希 + session token。

session token 必须与 `frontend/lib/session-token.ts` **字节级兼容**，否则前端
`middleware.ts` 验不过签发的 cookie，路由保护会失效。格式：

    base64url(body) + "." + base64url(hmac_sha256(secret, body))
    body = f"{user_id}.{account}.{exp}"

注意 `account` 必须是 ASCII（DB 上有 CHECK 约束保证）：JS 侧用 `btoa(body)`，
遇到非 Latin1 字符会直接抛异常。中文只能放 `display_name`，不能进 token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

import bcrypt

SESSION_COOKIE = "air_agent_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 天，与前端一致
BCRYPT_ROUNDS = 10


def _b64url(raw: bytes) -> str:
    """等价于 JS 的 `btoa(...)` 后把 `+`→`-`、`/`→`_` 并去掉末尾 `=`。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


# --------------------------------------------------------------------------- 密码


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- session


@dataclass(frozen=True)
class SessionPayload:
    user_id: str
    account: str
    exp: int


def _ensure_ascii(text: str, field: str) -> bytes:
    """非 ASCII 必须在这里就炸掉。

    JS 侧的 `btoa(body)` 遇到非 Latin1 字符会抛异常，所以中文一旦混进 token，
    前端根本签发不出、也验不了。与其产出一个对方无法处理的 token，不如在签名
    时就报明确错误。
    """
    try:
        return text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"{field} 必须是 ASCII，中文/非 Latin1 字符不能进入 token: {text!r}"
        ) from exc


def build_body(user_id: str, account: str, exp: int) -> str:
    if "." in user_id or "." in account:
        raise ValueError("user_id / account 不能包含 '.'，否则 token 无法被正确解析")
    return f"{user_id}.{account}.{exp}"


def sign_body(body: str, secret: str) -> str:
    """对已构造好的 body 签名，token 与 JS 实现逐字节一致。"""
    body_bytes = _ensure_ascii(body, "session body")
    payload = _b64url(body_bytes)
    signature = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    return f"{payload}.{_b64url(signature)}"


def sign_session(
    user_id: str,
    account: str,
    secret: str,
    *,
    ttl: int = SESSION_TTL_SECONDS,
) -> str:
    return sign_body(build_body(user_id, account, int(time.time()) + ttl), secret)


def verify_session(token: str | None, secret: str) -> SessionPayload | None:
    """校验签名与过期时间；任何异常都返回 None（不抛给调用方）。"""
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, sig_b64 = parts
    body_bytes = _unb64url(payload_b64)
    sig_bytes = _unb64url(sig_b64)
    if body_bytes is None or sig_bytes is None:
        return None
    try:
        body = body_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None

    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig_bytes):
        return None

    fields = body.split(".")
    if len(fields) != 3:
        return None
    user_id, account, exp_raw = fields
    if not user_id or not account:
        return None
    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    if exp < time.time():
        return None
    return SessionPayload(user_id=user_id, account=account, exp=exp)
