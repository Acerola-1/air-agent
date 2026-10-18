from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from common.identity import remote
from common.identity.db import IdentityBackendUnavailable
from fastapi import HTTPException

from app.routers import ledger
from app.schemas_ledger import ReconcileTicketIn

TENANT_ID = UUID("01931000-0000-4100-8100-000000001001")


class _NoBusinessDatabaseSession:
    async def execute(self, statement, params=None):
        raise AssertionError(f"remote reconcile route queried business SQL: {statement}")


def _settings(keys=("kid", "secret")):
    return SimpleNamespace(
        ledger_reconcile_key_pairs=[keys] if keys else [],
        ledger_reconcile_audience="ledger-reconcile-test",
    )


def test_remote_reconcile_ticket_uses_live_iam_without_platform_or_local_tenant_sql(monkeypatch):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    monkeypatch.setattr(ledger, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        ledger,
        "apply_tenant_context",
        lambda *_: (_ for _ in ()).throw(AssertionError("platform context is forbidden")),
    )
    checked = []

    def live_iam_check(tenant_id):
        checked.append(tenant_id)
        return True

    monkeypatch.setattr(ledger, "tenant_is_active", live_iam_check)
    monkeypatch.setattr(ledger, "sign_reconcile_ticket", lambda *args, **kwargs: "signed-ticket")

    result = asyncio.run(
        ledger.issue_reconcile_ticket(
            ReconcileTicketIn(tenant_id=TENANT_ID), _NoBusinessDatabaseSession()
        )
    )

    assert checked == [str(TENANT_ID)]
    assert result.ticket == "signed-ticket"
    assert result.tenant_id == str(TENANT_ID)


@pytest.mark.parametrize(
    ("iam_result", "expected_status"),
    [(False, 403), (IdentityBackendUnavailable("offline"), 503)],
)
def test_remote_reconcile_ticket_fails_closed_for_unavailable_or_inactive_tenant(
    monkeypatch, iam_result, expected_status
):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    monkeypatch.setattr(ledger, "get_settings", lambda: _settings())
    monkeypatch.setattr(ledger, "apply_tenant_context", lambda *_: None)
    monkeypatch.setattr(ledger, "sign_reconcile_ticket", lambda *args, **kwargs: "must-not-sign")

    def live_iam_check(_tenant_id):
        if isinstance(iam_result, Exception):
            raise iam_result
        return iam_result

    monkeypatch.setattr(ledger, "tenant_is_active", live_iam_check)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            ledger.issue_reconcile_ticket(
                ReconcileTicketIn(tenant_id=TENANT_ID), _NoBusinessDatabaseSession()
            )
        )
    assert caught.value.status_code == expected_status


def test_reconcile_ticket_retains_explicit_local_compatibility(monkeypatch):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "local")
    monkeypatch.setattr(ledger, "get_settings", lambda: _settings())
    contexts = []

    async def apply_local_context(_session, ctx):
        contexts.append(ctx)

    monkeypatch.setattr(ledger, "apply_tenant_context", apply_local_context)

    class Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(status="active")

    class LocalSession:
        async def execute(self, _statement):
            return Result()

    monkeypatch.setattr(ledger, "tenant_is_active", lambda *_: pytest.fail("local called IAM"))
    monkeypatch.setattr(ledger, "sign_reconcile_ticket", lambda *args, **kwargs: "local-ticket")
    result = asyncio.run(
        ledger.issue_reconcile_ticket(ReconcileTicketIn(tenant_id=TENANT_ID), LocalSession())
    )

    assert result.ticket == "local-ticket"
    assert len(contexts) == 1 and contexts[0].platform is True


def test_tenant_status_client_accepts_only_exact_live_contract(monkeypatch):
    from httpx import Response

    config = {
        "IAM_BASE_URL": "http://iam.invalid",
        "IAM_ISSUER": "test-issuer",
        "IAM_INTERNAL_CLIENT_ID": "test-client",
        "IAM_INTERNAL_CLIENT_SECRET": "test-secret",
    }
    requests = []

    def request(method, path, *, config, body):
        requests.append((method, path, body))
        return Response(
            200,
            json={
                "schema_version": "iam.tenant-status.v1",
                "tenant_id": str(TENANT_ID),
                "active": True,
            },
        )

    monkeypatch.setattr(remote, "request_iam", request)
    assert remote.tenant_is_active(str(TENANT_ID), config=config) is True
    assert requests == [("POST", "/v1/internal/tenants/status", {"tenant_id": str(TENANT_ID)})]

    def mismatched(_method, _path, *, config, body):
        return Response(
            200,
            json={
                "schema_version": "iam.tenant-status.v1",
                "tenant_id": str(UUID("01931000-0000-4100-8100-000000001002")),
                "active": True,
            },
        )

    monkeypatch.setattr(remote, "request_iam", mismatched)
    with pytest.raises(remote.RemoteProtocolError):
        remote.tenant_is_active(str(TENANT_ID), config=config)
