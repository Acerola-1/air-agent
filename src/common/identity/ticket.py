"""运行票据验签（与 admin API `app/security.py` 的 rt-v1 协议字节兼容）.

两个项目独立 venv/依赖树，不共享 import；协议一致性由 `tests/identity_compat`
生成的跨项目向量保证：admin 侧签发 → 本模块验证必须通过（含错误受众/篡改/
过期/轮换 key 的负例）。票据只含标识与修订号，永不含业务凭据.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


def _unb64url(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


@dataclass(frozen=True)
class TicketClaims:
    """已验签运行票据的非秘密声明集（标识 + 修订号）."""

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

    def as_scope(self) -> dict[str, str]:
        """返回作用域标识字典."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "membership_id": self.membership_id,
        }


class TicketError(Exception):
    """票据非法（签名/时效/受众/字段）。调用方映射为 401，不泄露原因细节."""


@dataclass(frozen=True)
class MigrationTicketClaims:
    """历史线程 metadata 迁移的受限单目的票据（mt-v1）.

    只授权"对指定 thread 更新可信 metadata 键"一次窄操作；不含会话/成员，
    不能读取任何资源。生产签发方为迁移脚本（服务端，持 RUNTIME_TICKET_KEYS）。
    """

    thread_id: str
    tenant_id: str
    user_id: str
    audience: str
    issued_at: int
    expires_at: int


def parse_keys(raw: str) -> list[tuple[str, str]]:
    """`RUNTIME_TICKET_KEYS` → [(kid, secret)]；**kid 与密钥内容绑定，与数组位置无关**.

    推荐形态：JSON 对象 `{"<kid>": "<secret>", ...}`，首个条目为当前签发密钥；
    验签侧按票据内 kid 选钥，新旧密钥交错部署时旧票据仍可验签。
    过渡兼容：纯字符串 JSON 数组按 `v1..vN` 索引编号——仅限未迁移的旧部署，
    重排/前插会使旧票据失效，因此不作为长期形态。
    """
    if not raw:
        return []
    raw = raw.strip()
    # 不同 dotenv 实现对引号的处理不一致（Apple container-compose 保留单引号）；
    # 两侧都容忍外层单引号包裹的 JSON 值.
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TicketError("ticket keys config invalid") from exc
    pairs: list[tuple[str, str]] = []
    if isinstance(parsed, dict):
        pairs = [(str(kid), value) for kid, value in parsed.items()]
    elif isinstance(parsed, list) and all(isinstance(k, str) and k for k in parsed):
        pairs = [(f"v{index + 1}", secret) for index, secret in enumerate(parsed)]
    else:
        raise TicketError("ticket keys config invalid")
    if not pairs or not all(
        isinstance(kid, str) and kid and isinstance(sec, str) and sec
        for kid, sec in pairs
    ):
        raise TicketError("ticket keys config invalid")
    if len({kid for kid, _ in pairs}) != len(pairs):
        raise TicketError("ticket keys config invalid")
    return pairs


def verify_ticket(
    token: str,
    keys: list[tuple[str, str]],
    *,
    expected_audience: str,
    now: float | None = None,
) -> TicketClaims:
    """验签并解码 rt-v1 claims；**不**做 DB 实时状态（由 verifier 负责）."""
    if not token or not keys:
        raise TicketError("missing ticket or keys")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("ticket shape invalid")
    body, sig_b64 = parts
    sig_bytes = _unb64url(sig_b64)
    body_bytes = _unb64url(body)
    if sig_bytes is None or body_bytes is None:
        raise TicketError("ticket encoding invalid")
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("ticket payload invalid") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("kid"), str):
        raise TicketError("ticket header invalid")
    kid = decoded["kid"]
    secret = dict(keys).get(kid)
    if secret is None:
        raise TicketError("ticket kid unknown")
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, sig_bytes):
        raise TicketError("ticket signature invalid")
    payload = decoded.get("p")
    if not isinstance(payload, dict) or payload.get("typ") != "rt-v1":
        raise TicketError("ticket type invalid")
    now_ts = time.time() if now is None else now
    try:
        exp = int(payload["exp"])
        iat = int(payload["iat"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TicketError("ticket time fields invalid") from exc
    if exp <= now_ts or iat > now_ts + 60:
        raise TicketError("ticket expired or not yet valid")
    if payload.get("aud") != expected_audience:
        raise TicketError("ticket audience mismatch")
    try:
        claims = TicketClaims(
            session_id=str(payload["sid"]),
            user_id=str(payload["uid"]),
            tenant_id=str(payload["tid"]),
            membership_id=str(payload["mid"]),
            audience=str(payload["aud"]),
            issued_at=iat,
            expires_at=exp,
            session_ctx_version=int(payload["crev"]),
            user_auth_revision=int(payload["urev"]),
            tenant_auth_revision=int(payload["trev"]),
            membership_revision=int(payload["mrev"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TicketError("ticket claims incomplete") from exc
    return claims


def verify_migration_ticket(
    token: str,
    keys: list[tuple[str, str]],
    *,
    expected_audience: str,
    now: float | None = None,
) -> MigrationTicketClaims:
    """验签 mt-v1 迁移票据；任何不符抛 TicketError（调用方 401）."""
    if not token or not keys:
        raise TicketError("missing ticket or keys")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("ticket shape invalid")
    body, sig_b64 = parts
    sig_bytes = _unb64url(sig_b64)
    body_bytes = _unb64url(body)
    if sig_bytes is None or body_bytes is None:
        raise TicketError("ticket encoding invalid")
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("ticket payload invalid") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("kid"), str):
        raise TicketError("ticket header invalid")
    secret = dict(keys).get(decoded["kid"])
    if secret is None:
        raise TicketError("ticket kid unknown")
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, sig_bytes):
        raise TicketError("ticket signature invalid")
    payload = decoded.get("p")
    if not isinstance(payload, dict) or payload.get("typ") != "mt-v1":
        raise TicketError("ticket type invalid")
    now_ts = time.time() if now is None else now
    try:
        exp = int(payload["exp"])
        iat = int(payload["iat"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TicketError("ticket time fields invalid") from exc
    if exp <= now_ts or iat > now_ts + 60:
        raise TicketError("ticket expired or not yet valid")
    if payload.get("aud") != expected_audience:
        raise TicketError("ticket audience mismatch")
    try:
        return MigrationTicketClaims(
            thread_id=str(payload["thread"]),
            tenant_id=str(payload["tenant"]),
            user_id=str(payload["user"]),
            audience=str(payload["aud"]),
            issued_at=iat,
            expires_at=exp,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TicketError("ticket claims incomplete") from exc


def as_dict_for_admin_compat(claims: TicketClaims) -> dict[str, Any]:
    """返回原始 claims 字典形状，便于跨项目兼容测试比对."""
    return {
        "typ": "rt-v1",
        "sid": claims.session_id,
        "uid": claims.user_id,
        "tid": claims.tenant_id,
        "mid": claims.membership_id,
        "aud": claims.audience,
        "iat": claims.issued_at,
        "exp": claims.expires_at,
        "crev": claims.session_ctx_version,
        "urev": claims.user_auth_revision,
        "trev": claims.tenant_auth_revision,
        "mrev": claims.membership_revision,
    }


@dataclass(frozen=True)
class ReconcileClaims:
    """服务级只读校准票据（rs-v1）：**不绑定任何用户会话**.

    原成员被撤销/会话失效后，校准仍须可用。因此这是**独立服务主体**的窄范围
    只读凭据：只授权"按租户（可选线程清单）读取运行状态"，不含 user/membership，
    不能被用来创建运行、读消息、执行模型或写任何业务数据。
    """

    actor: str
    tenant_id: str
    scope: str
    audience: str
    issued_at: int
    expires_at: int
    jti: str
    thread_ids: tuple[str, ...] = ()

    def allows_thread(self, thread_id: str) -> bool:
        """票据是否覆盖该线程（未列线程 = 全租户只读）."""
        return not self.thread_ids or thread_id in self.thread_ids


def _decode_verified_payload(token: str, keys: list[tuple[str, str]]) -> dict[str, Any]:
    """验签并返回载荷（kid 选钥 + HMAC 比对 + JSON 解析）；失败抛 `TicketError`."""
    if not token or not keys:
        raise TicketError("missing ticket or keys")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("ticket shape invalid")
    body, sig_b64 = parts
    sig_bytes = _unb64url(sig_b64)
    body_bytes = _unb64url(body)
    if sig_bytes is None or body_bytes is None:
        raise TicketError("ticket encoding invalid")
    try:
        decoded = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketError("ticket payload invalid") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("kid"), str):
        raise TicketError("ticket header invalid")
    secret = dict(keys).get(decoded["kid"])
    if secret is None:
        raise TicketError("ticket kid unknown")
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, sig_bytes):
        raise TicketError("ticket signature invalid")
    payload = decoded.get("p")
    if not isinstance(payload, dict):
        raise TicketError("ticket payload invalid")
    return payload


def verify_reconcile_ticket(
    token: str,
    keys: list[tuple[str, str]],
    *,
    expected_audience: str = "ledger-reconcile",
    expected_scope: str = "ledger:reconcile",
    now: float | None = None,
) -> ReconcileClaims:
    """校验 rs-v1 服务校准票据；签名/时效/受众/范围任一不符即抛 `TicketError`.

    该票据**不绑定用户会话**：原成员被撤销后校准仍可用，但能力仅限"按租户
    （可再按线程清单收窄）只读运行状态"，不含任何写权限或业务权限。
    """
    payload = _decode_verified_payload(token, keys)
    if payload.get("typ") != "rs-v1":
        raise TicketError("ticket type invalid")
    if str(payload.get("aud") or "") != expected_audience:
        raise TicketError("ticket audience invalid")
    if str(payload.get("scope") or "") != expected_scope:
        raise TicketError("ticket scope invalid")
    now_ts = time.time() if now is None else now
    try:
        exp = int(payload["exp"])
        iat = int(payload["iat"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TicketError("ticket time fields invalid") from exc
    if exp <= now_ts or iat > now_ts + 60:
        raise TicketError("ticket expired or not yet valid")
    tenant = str(payload.get("tenant") or "")
    if not tenant:
        raise TicketError("ticket tenant missing")
    threads = payload.get("threads")
    return ReconcileClaims(
        actor=str(payload.get("actor") or "service:ledger-reconcile"),
        tenant_id=tenant,
        scope=str(payload.get("scope") or ""),
        audience=str(payload.get("aud") or ""),
        issued_at=iat,
        expires_at=exp,
        jti=str(payload.get("jti") or ""),
        thread_ids=(
            tuple(str(item) for item in threads) if isinstance(threads, list) else ()
        ),
    )
