"""密码哈希、统一可撤销会话与运行票据。

0003 起会话改为**服务端可撤销的不透明 token**：

    cookie 值 = "<session_id>.<token>"
    - session_id：UUID（仅用于 RLS 会话作用域 GUC 与路由，不承载信任）
    - token：高熵随机（secrets.token_urlsafe(32)），服务端只存 SHA-256 摘要

旧 HMAC 签名 cookie（`frontend/lib/session-token.ts` / `security.py` 历史格式）
已退出授权路径：`verify_session` 保留仅用于**识别旧格式并拒绝**（引导重新登录），
不再授予任何访问。迁移工具用 `legacy_session_account` 读取旧身份做预检。

运行票据（admin API → LangGraph，服务端到服务端）：紧凑 HMAC JSON，含
kid/aud/iat/exp 与会话上下文（sid/uid/tid/mid + 三个修订号），使用独立密钥
（RUNTIME_TICKET_KEYS，支持当前/上一双密钥轮换），绝不复用浏览器会话密钥。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass

import bcrypt

SESSION_COOKIE = "air_agent_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 天，与既有体验一致
BCRYPT_ROUNDS = 10
RUNTIME_TICKET_TTL_SECONDS = 60


def _b64url(raw: bytes) -> str:
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


# --------------------------------------------------------------------------- 统一会话


def new_session_token() -> tuple[str, str, str]:
    """生成 (session_id, raw_token, token_hash)。原始 token 只出现在 cookie 一次。"""
    session_id = str(uuid.uuid4())
    raw_token = secrets.token_urlsafe(32)
    return session_id, raw_token, token_hash_of(raw_token)


def token_hash_of(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def compose_session_cookie(session_id: str, raw_token: str) -> str:
    return f"{session_id}.{raw_token}"


def parse_session_cookie(value: str | None) -> tuple[str | None, str | None]:
    """拆 cookie 值；旧 HMAC 格式（两段但非 UUID）返回 (None, None) 由上层拒绝。

    第二段仅用于摘要查库；第一段 session_id 只作为 RLS 作用域提示，
    不携带任何授权语义。
    """
    if not value:
        return None, None
    parts = value.split(".", 1)
    if len(parts) != 2:
        return None, None
    session_id, token = parts
    try:
        uuid.UUID(session_id)
    except ValueError:
        return None, None
    if not token:
        return None, None
    return session_id, token


# --------------------------------------------------------------------------- 旧 HMAC（仅拒绝/迁移）


def legacy_session_account(token: str | None, secret: str) -> str | None:
    """识别旧 HMAC 会话并返回其账号（仅迁移/预检工具使用），不授予任何访问。"""
    payload = _legacy_verify(token, secret)
    return payload.account if payload else None


@dataclass(frozen=True)
class LegacySessionPayload:
    user_id: str
    account: str
    exp: int


def _legacy_verify(token: str | None, secret: str) -> LegacySessionPayload | None:
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
    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    if exp < time.time():
        return None
    return LegacySessionPayload(user_id=user_id, account=account, exp=exp)


# --------------------------------------------------------------------------- 运行票据


@dataclass(frozen=True)
class RuntimeTicket:
    """运行票据 claims（非秘密；含标识与修订号，不含凭据）。"""

    session_id: str
    user_id: str
    tenant_id: str
    membership_id: str
    audience: str
    issued_at: int
    expires_at: int
    session_ctx_version: int
    user_auth_revision: int
    tenant_auth_revision: int
    membership_revision: int
    kid: str

    def to_payload(self) -> dict[str, object]:
        return {
            "typ": "rt-v1",
            "sid": self.session_id,
            "uid": self.user_id,
            "tid": self.tenant_id,
            "mid": self.membership_id,
            "aud": self.audience,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "crev": self.session_ctx_version,
            "urev": self.user_auth_revision,
            "trev": self.tenant_auth_revision,
            "mrev": self.membership_revision,
        }


def _ticket_body(payload: dict[str, object], kid: str) -> str:
    body = {"kid": kid, "p": payload}
    return _b64url(json.dumps(body, separators=(",", ":")).encode("utf-8"))


def sign_migration_ticket(
    thread_id: str,
    tenant_id: str,
    user_id: str,
    keys: list[tuple[str, str]],
    *,
    audience: str = "langgraph-migration",
    ttl_seconds: int = 60,
) -> str:
    """R06：历史线程 metadata 迁移的受限票据（mt-v1）。

    单目的（仅线程 metadata 更新）、绑定 thread、短时效；与 Agent 侧
    `common.identity.ticket.verify_migration_ticket` 字节兼容。
    """
    if not keys:
        raise RuntimeError("RUNTIME_TICKET_KEYS 未配置")
    kid, secret = keys[0]
    now = int(time.time())
    payload = {
        "typ": "mt-v1",
        "thread": thread_id,
        "tenant": tenant_id,
        "user": user_id,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    body = _b64url(json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def sign_reconcile_ticket(
    tenant_id: str,
    keys: list[tuple[str, str]],
    *,
    actor: str = "service:ledger-reconcile",
    audience: str = "ledger-reconcile",
    ttl_seconds: int = 600,
    thread_ids: list[str] | None = None,
) -> str:
    """F05：签发**服务级只读校准票据**（rs-v1），与用户运行票据密钥/受众完全分离.

    能力范围（最小授权）：按 `tenant`（可再以 `thread_ids` 收窄）**只读**运行状态。
    不含 user/membership/permissions——原成员被撤销后校准仍可用，但不能借此创建运行、
    读取消息或执行业务操作。与 Agent 侧
    `common.identity.ticket.verify_reconcile_ticket` 字节兼容。
    """
    if not keys:
        raise RuntimeError("LEDGER_RECONCILE_KEYS 未配置")
    if not tenant_id:
        raise RuntimeError("服务校准票据必须绑定租户")
    if ttl_seconds < 1 or ttl_seconds > 3600:
        raise RuntimeError("服务校准票据有效期必须在 1..3600 秒")
    kid, secret = keys[0]
    now = int(time.time())
    payload: dict[str, object] = {
        "typ": "rs-v1",
        "scope": "ledger:reconcile",
        "tenant": tenant_id,
        "actor": actor,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if thread_ids:
        payload["threads"] = [str(item) for item in thread_ids]
    body = _b64url(
        json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def sign_runtime_ticket(ticket: RuntimeTicket, keys: list[tuple[str, str]]) -> str:
    """keys: [(kid, secret), ...]，用第一条密钥签名，前缀 kid. 便于验签选钥。"""
    if not keys:
        raise RuntimeError("RUNTIME_TICKET_KEYS 未配置")
    kid, secret = keys[0]
    body = _ticket_body(ticket.to_payload(), kid)
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def verify_runtime_ticket(token: str, keys: list[tuple[str, str]]) -> dict | None:
    """验签 + 解码 claims（时效/受众由调用方结合实时状态复查）；失败返回 None。"""
    if not token or not keys:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    body, sig_b64 = parts
    sig_bytes = _unb64url(sig_b64)
    body_bytes = _unb64url(body)
    if sig_bytes is None or body_bytes is None:
        return None
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict) or not isinstance(decoded.get("kid"), str):
        return None
    kid = decoded["kid"]
    for candidate_kid, secret in keys:
        if candidate_kid != kid:
            continue
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig_bytes):
            return None
        payload = decoded.get("p")
        if not isinstance(payload, dict) or payload.get("typ") != "rt-v1":
            return None
        return payload
    return None


def verify_migration_ticket(
    token: str,
    keys: list[tuple[str, str]],
    *,
    expected_audience: str = "langgraph-migration",
) -> dict | None:
    """校验 mt-v1 迁移票据（与 Agent 侧 verify_migration_ticket 字节兼容）。

    用于测试替身与本地验收工具复现平台侧行为；失败返回 None（401 语义由调用方映射）。
    """
    if not token or not keys:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    body, sig_b64 = parts
    sig_bytes = _unb64url(sig_b64)
    body_bytes = _unb64url(body)
    if sig_bytes is None or body_bytes is None:
        return None
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict) or not isinstance(decoded.get("kid"), str):
        return None
    secret = dict(keys).get(decoded["kid"])
    if secret is None:
        return None
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig_bytes):
        return None
    payload = decoded.get("p")
    if not isinstance(payload, dict) or payload.get("typ") != "mt-v1":
        return None
    if payload.get("aud") != expected_audience:
        return None
    try:
        if int(payload["exp"]) <= time.time() or int(payload["iat"]) > time.time() + 60:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return payload


# --------------------------------------------------------------------------- CSRF / Origin


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """cookie 写接口来源校验。

    无 Origin 头的非浏览器请求放行（同 fetch 默认 same-origin 语义由浏览器保证）；
    有 Origin 时**必须**精确命中允许列表——跨站写一律拒绝。
    """
    if origin is None or origin == "":
        return True
    return origin in allowed
