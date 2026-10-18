"""搜索出口的删除优先回归；实测反例：墓碑后原生搜索仍返回values正文。"""

from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from common import artifact_http
from common.identity import db
from common.identity.db import IdentityBackendUnavailable

pytestmark = pytest.mark.anyio


async def test_native_search_discards_deleted_values_and_extractions(monkeypatch):
    monkeypatch.setattr(
        artifact_http,
        "_authenticate_custom",
        AsyncMock(
            return_value={"identity": "u", "tenant_id": "t", "membership_id": "m"}
        ),
    )
    monkeypatch.setattr(
        db, "fetch_deleted_thread_ids", AsyncMock(return_value={"deleted"})
    )

    async def upstream(request):
        return JSONResponse(
            [
                {
                    "thread_id": "deleted",
                    "values": {"messages": "must never escape"},
                    "extracted": {"body": "secret"},
                },
                {"thread_id": "live", "metadata": {"title": "kept"}},
            ]
        )

    app = Starlette(routes=[Route("/threads/search", upstream, methods=["POST"])])
    app.add_middleware(
        BaseHTTPMiddleware, dispatch=artifact_http.deleted_thread_search_barrier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/threads/search", json={})
    assert result.status_code == 200
    assert result.json() == [{"thread_id": "live", "metadata": {"title": "kept"}}]
    assert "must never escape" not in result.text


async def test_tombstone_backend_failure_does_not_return_unfiltered_search(monkeypatch):
    monkeypatch.setattr(
        artifact_http,
        "_authenticate_custom",
        AsyncMock(
            return_value={"identity": "u", "tenant_id": "t", "membership_id": "m"}
        ),
    )
    monkeypatch.setattr(
        db,
        "fetch_deleted_thread_ids",
        AsyncMock(side_effect=IdentityBackendUnavailable("down")),
    )

    async def upstream(request):
        return JSONResponse(
            [{"thread_id": "deleted", "values": {"messages": "must never escape"}}]
        )

    app = Starlette(routes=[Route("/threads/search", upstream, methods=["POST"])])
    app.add_middleware(
        BaseHTTPMiddleware, dispatch=artifact_http.deleted_thread_search_barrier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/threads/search", json={})
    assert result.status_code == 503
    assert "must never escape" not in result.text
