"""Reference synchronization must not hold identity locks across graph calls."""

import importlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.identity.db import IdentityBackendUnavailable

USER = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
MEMBER = "33333333-3333-4333-8333-333333333333"
FIELDS = dict(
    user_id=USER,
    account="acct",
    display_name="n",
    tenant_id=TENANT,
    tenant_code="c",
    tenant_name="n",
    membership_id=MEMBER,
    user_auth_revision=0,
    tenant_auth_revision=1,
    membership_revision=1,
)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "admin/api"))
    module = importlib.import_module("app.services.remote_identity")
    db = importlib.import_module("app.db")
    monkeypatch.setattr(db, "apply_tenant_context", AsyncMock())
    return module, db


class ProjectionSession:
    def __init__(self, *, unchanged=False, member_count=1):
        self.statements = []
        self.unchanged = unchanged
        self.member_count = member_count

    async def execute(self, sql, params):
        text = str(sql)
        self.statements.append(text)
        values = None
        if self.unchanged and text.startswith("SELECT"):
            if "iam_reference_tenants" in text:
                values = {"code": "c", "name": "n", "auth_revision": 1}
            elif "iam_reference_users" in text:
                values = {"account": "acct", "display_name": "n", "auth_revision": 0}
            elif "iam_reference_memberships" in text:
                values = {
                    "tenant_id": uuid.UUID(TENANT),
                    "user_id": uuid.UUID(USER),
                    "revision": 1,
                }
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(first=lambda: values),
            rowcount=self.member_count,
        )


@pytest.mark.anyio
async def test_current_projection_is_read_only(api):
    module, _ = api
    session = ProjectionSession(unchanged=True)
    await module.upsert_reference_projection(session, **FIELDS)
    assert len(session.statements) == 3
    assert all(s.startswith("SELECT") for s in session.statements)


@pytest.mark.anyio
async def test_changed_projection_uses_native_fk_order_and_rejects_rebinding(api):
    module, _ = api
    session = ProjectionSession()
    await module.upsert_reference_projection(session, **FIELDS)
    writes = [s for s in session.statements if s.startswith("INSERT")]
    assert [s.split()[2] for s in writes] == [
        "iam_reference_tenants",
        "iam_reference_users",
        "iam_reference_memberships",
    ]
    with pytest.raises(IdentityBackendUnavailable):
        await module.upsert_reference_projection(
            ProjectionSession(member_count=0), **FIELDS
        )


@pytest.mark.anyio
async def test_projection_failure_still_fails_closed(api):
    module, _ = api
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("unavailable"))
    )
    with pytest.raises(IdentityBackendUnavailable):
        await module.upsert_reference_projection(session, **FIELDS)


@pytest.mark.anyio
async def test_every_resolution_rechecks_iam_and_commits_before_new_scope(
    api, monkeypatch
):
    from test_remote_identity_client import _identity_payload

    from common.identity import remote

    module, db = api
    payload = _identity_payload()
    payload["execution_context"] = {
        "verification_ref": "55555555-5555-4555-8555-555555555555",
        "revisions": payload["revisions"],
    }
    token = "synthetic-token"
    row = {
        "subject_token": token,
        "token_hash": module.hash_token(token),
        "audience": "langgraph-runtime",
        "user_id": USER,
        "iam_session_id": payload["session"]["id"],
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    monkeypatch.setattr(module, "parse_handle_cookie", lambda _: object())
    monkeypatch.setattr(module, "load_session_token", AsyncMock(return_value=row))
    calls = []
    monkeypatch.setattr(
        remote, "subject_payload", lambda *a, **kw: calls.append("iam") or payload
    )
    monkeypatch.setattr(module, "iam_user_request", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "upsert_reference_projection", AsyncMock())
    order = []

    async def commit():
        order.append("commit")

    async def scope(*args):
        order.append("scope")

    monkeypatch.setattr(db, "apply_tenant_context", scope)
    session = SimpleNamespace(commit=commit)
    for _ in range(2):
        result = await module.resolve_remote_identity(session, "synthetic-cookie")
        assert str(result.user.id) == USER
    assert calls == ["iam", "iam"]
    assert order == ["commit", "scope", "commit", "scope"]

    session = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("commit failed"))
    )
    with pytest.raises(IdentityBackendUnavailable):
        await module.resolve_remote_identity(session, "synthetic-cookie")

    payload["execution_context"] = {}
    module.upsert_reference_projection.reset_mock()
    session = SimpleNamespace(commit=AsyncMock())
    with pytest.raises(IdentityBackendUnavailable):
        await module.resolve_remote_identity(session, "synthetic-cookie")
    module.upsert_reference_projection.assert_not_awaited()
    session.commit.assert_not_awaited()
