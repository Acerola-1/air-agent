"""Acquire short-lived, single-operation knowledge delegations from IAM.

The Agent runtime never forwards a browser token to the knowledge service.
Instead it presents its own non-secret execution reference (issued at intake
and bound to the WebSocket/run) and receives an opaque 60-second read-only
delegation whose source identity must match the trusted runtime identity.

No positive delegation is cached across calls: every logical search and every
source read performs one issuance and one live source-context comparison.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from common.identity.remote import IdentityBackendUnavailable, require_remote_config
from common.identity.runtime import RuntimeIdentity
from common.knowledge.contracts import Delegation

ISSUE_PATH = "/v1/internal/knowledge-delegations"
REQUEST_SCHEMA = "iam.knowledge-delegation-request.v1"
OPERATIONS = frozenset({"query", "record:read", "source:read", "document:read"})


class KnowledgeDelegationError(RuntimeError):
    """Delegation issuance or verification failed (code is a public outcome)."""

    def __init__(self, code: str, message: str = "") -> None:
        """Store a public outcome code and a safe message."""
        super().__init__(message or code)
        self.code = code


def _require_identity_fields(identity: RuntimeIdentity) -> None:
    if not identity.verification_ref:
        raise KnowledgeDelegationError("unavailable", "缺少可信执行引用")
    if any(
        type(value) is not int
        for value in (
            identity.session_ctx_version,
            identity.user_auth_revision,
            identity.tenant_auth_revision,
            identity.membership_revision,
        )
    ):
        raise KnowledgeDelegationError("context_changed", "缺少可信修订快照")
    if not identity.tenant_id or not identity.membership_id or not identity.session_id:
        raise KnowledgeDelegationError("context_changed", "缺少企业或会话上下文")


async def issue_delegation(
    identity: RuntimeIdentity,
    operation: str,
    *,
    timeout_seconds: float,
    config: dict[str, str] | None = None,
) -> Delegation:
    """Issue one delegation and verify the returned source identity.

    Raises :class:`KnowledgeDelegationError` with an outcome code; never returns
    a token whose source differs from the trusted runtime identity.
    """
    if operation not in OPERATIONS:
        raise KnowledgeDelegationError("unknown", "不支持的知识操作")
    _require_identity_fields(identity)
    cfg = config or require_remote_config()
    remaining = max(0.05, timeout_seconds)
    body = {
        "schema_version": REQUEST_SCHEMA,
        "execution_context": {
            "verification_ref": identity.verification_ref,
            "expected_revisions": {
                "user_auth_revision": identity.user_auth_revision,
                "tenant_auth_revision": identity.tenant_auth_revision,
                "membership_revision": identity.membership_revision,
                "session_ctx_version": identity.session_ctx_version,
            },
        },
        "source_tenant_id": identity.tenant_id,
        "operation": operation,
    }
    try:
        async with asyncio.timeout(remaining):
            async with httpx.AsyncClient(
                base_url=cfg["IAM_BASE_URL"].rstrip("/"),
                auth=(cfg["IAM_INTERNAL_CLIENT_ID"], cfg["IAM_INTERNAL_CLIENT_SECRET"]),
                timeout=httpx.Timeout(remaining, connect=min(remaining, 1.0)),
                trust_env=False,
                headers={"accept": "application/json"},
            ) as client:
                response = await client.post(ISSUE_PATH, json=body)
    except TimeoutError as exc:
        raise KnowledgeDelegationError("timeout", "知识委托签发超时") from exc
    except (httpx.HTTPError, IdentityBackendUnavailable) as exc:
        raise KnowledgeDelegationError("unavailable", "知识委托签发不可用") from exc
    if response.status_code in (401,):
        raise KnowledgeDelegationError("context_changed", "源身份已失效")
    if response.status_code == 403:
        raise KnowledgeDelegationError("permission_denied", "没有知识库读取权限")
    if response.status_code == 409:
        raise KnowledgeDelegationError("context_changed", "企业或修订上下文已变化")
    if response.status_code == 429:
        raise KnowledgeDelegationError("unavailable", "知识委托签发被限流")
    if response.status_code != 200:
        raise KnowledgeDelegationError(
            "unavailable", f"知识委托签发失败({response.status_code})"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise KnowledgeDelegationError("unavailable", "知识委托响应非法") from exc
    if not isinstance(payload, dict):
        raise KnowledgeDelegationError("unavailable", "知识委托响应非法")
    token = payload.get("delegation_token")
    source = payload.get("source")
    if (
        payload.get("schema_version") != "iam.knowledge-delegation.v1"
        or payload.get("token_type") != "Bearer"
        or payload.get("audience") != "knowledge"
        or payload.get("operation") != operation
        or not isinstance(token, str)
        or not token
        or not isinstance(source, dict)
    ):
        raise KnowledgeDelegationError("unavailable", "知识委托响应不符合契约")
    revisions = source.get("revisions")
    if not isinstance(revisions, dict):
        raise KnowledgeDelegationError("unavailable", "知识委托缺少修订")
    expected = {
        "user_id": identity.user_id,
        "session_id": identity.session_id,
        "tenant_id": identity.tenant_id,
        "membership_id": identity.membership_id,
    }
    for key, value in expected.items():
        if str(source.get(key) or "") != value:
            raise KnowledgeDelegationError(
                "context_changed", "知识委托来源与运行身份不一致"
            )
    if (
        type(source.get("context_version")) is not int
        or source["context_version"] != identity.session_ctx_version
    ):
        raise KnowledgeDelegationError("context_changed", "知识委托上下文版本不一致")
    expected_revisions = {
        "session_ctx_version": identity.session_ctx_version,
        "user_auth_revision": identity.user_auth_revision,
        "tenant_auth_revision": identity.tenant_auth_revision,
        "membership_revision": identity.membership_revision,
    }
    if set(revisions) != set(expected_revisions) or any(
        type(revisions.get(key)) is not int or revisions[key] != value
        for key, value in expected_revisions.items()
    ):
        raise KnowledgeDelegationError("context_changed", "知识委托修订不一致")
    try:
        expires_at = datetime.fromisoformat(
            payload["expires_at"].replace("Z", "+00:00")
        )
        if (
            expires_at.tzinfo is None
            or not 0 < (expires_at - datetime.now(UTC)).total_seconds() <= 60
        ):
            raise ValueError("invalid expiry")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise KnowledgeDelegationError("unavailable", "知识委托期限非法") from exc
    return Delegation(
        token=token,
        operation=operation,
        expires_at=payload.get("expires_at"),
        user_id=str(source["user_id"]),
        session_id=str(source["session_id"]),
        tenant_id=str(source["tenant_id"]),
        membership_id=str(source["membership_id"]),
        context_version=int(source["context_version"]),
        revisions=dict(revisions),
    )
