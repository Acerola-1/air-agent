"""归档 scope 签名不能替代实时会话和线程归属验证。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langgraph_sdk import Auth

from common import artifact_http
from common.identity import db, langgraph_auth

pytestmark = pytest.mark.anyio


async def test_archive_rejects_revoked_identity_before_storage(monkeypatch):
    request = SimpleNamespace(headers={"x-artifact-scope": "synthetic"})
    monkeypatch.setattr(
        artifact_http, "verify_scope", lambda *args: {"tenantId": "t", "userId": "u"}
    )
    denied = AsyncMock(side_effect=Auth.exceptions.HTTPException(401, "revoked"))
    storage = AsyncMock()
    monkeypatch.setattr(langgraph_auth, "authenticate", denied)
    monkeypatch.setattr(artifact_http, "list_tool_artifacts", storage)
    with pytest.raises(HTTPException) as failure:
        await artifact_http.list_archives(request, "thread")
    assert failure.value.status_code == 401
    storage.assert_not_awaited()


async def test_archive_scope_must_match_live_actor_and_confirmed_owner(monkeypatch):
    request = SimpleNamespace(headers={"x-artifact-scope": "synthetic"})
    monkeypatch.setattr(
        artifact_http, "verify_scope", lambda *args: {"tenantId": "t", "userId": "u"}
    )
    identity = AsyncMock(return_value={"tenant_id": "other", "identity": "u"})
    owner = AsyncMock(return_value={"source": "native_pending"})
    monkeypatch.setattr(langgraph_auth, "authenticate", identity)
    monkeypatch.setattr(db, "fetch_thread_owner", owner)
    with pytest.raises(HTTPException) as failure:
        await artifact_http._owners(request, "thread")
    assert failure.value.status_code == 403
    owner.assert_not_awaited()
    identity.return_value = {"tenant_id": "t", "identity": "u"}
    with pytest.raises(HTTPException) as failure:
        await artifact_http._owners(request, "thread")
    assert failure.value.status_code == 404
    owner.return_value = {"source": "native"}
    assert await artifact_http._owners(request, "thread") == ("t", "u")


async def test_migration_ticket_cannot_read_archives(monkeypatch):
    request = SimpleNamespace(headers={"x-artifact-scope": "synthetic"})
    monkeypatch.setattr(
        artifact_http, "verify_scope", lambda *args: {"tenantId": "t", "userId": "u"}
    )
    monkeypatch.setattr(
        langgraph_auth,
        "authenticate",
        AsyncMock(
            return_value={"tenant_id": "t", "identity": "u", "is_migration": True}
        ),
    )
    storage = AsyncMock()
    monkeypatch.setattr(artifact_http, "get_tool_artifact", storage)
    with pytest.raises(HTTPException) as failure:
        await artifact_http.read_archive(request, "thread", "artifact")
    assert failure.value.status_code == 403
    storage.assert_not_awaited()
