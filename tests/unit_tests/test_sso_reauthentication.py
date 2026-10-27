"""SSO 过期恢复必须绑定原 state，且不兑换票据或建立身份。"""

from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response


@pytest.fixture
def api(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "admin/api"))
    module = import_module("app.routers.iam_remote")
    monkeypatch.setattr(module, "_iam_config", lambda: {})
    monkeypatch.setattr(module, "_callback_url", lambda *args: "http://test/callback")
    monkeypatch.setattr(
        module,
        "_sso_exchange",
        AsyncMock(return_value={"access_token": "synthetic-token"}),
    )
    monkeypatch.setattr(
        module,
        "_establish_session",
        AsyncMock(return_value=Response(status_code=303, headers={"Location": "/"})),
    )
    return module


def request(api, *, kind="chat", bound="nonce", cookie=True, **params):
    query = {"frontend": kind, "state": "nonce", **params}
    headers = (
        [(b"cookie", f"{api._STATE_COOKIE}_{kind}={kind}:{bound}".encode())]
        if cookie
        else []
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/iam/callback",
            "query_string": urlencode(query).encode(),
            "headers": headers,
        }
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["chat", "admin"])
async def test_expired_sso_returns_to_password_login_without_identity_work(api, kind):
    response = await api.sso_callback(
        request(api, kind=kind, error="login_required"), session=None
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?reason=session-expired"
    assert "nonce" not in response.headers["location"]
    assert response.headers["cache-control"] == "no-store"
    assert f"{api._STATE_COOKIE}_{kind}" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    api._sso_exchange.assert_not_awaited()
    api._establish_session.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", [{"bound": "wrong"}, {"cookie": False}])
async def test_error_callback_still_requires_browser_state(api, mutation):
    response = await api.sso_callback(
        request(api, error="login_required", **mutation), session=None
    )
    assert response.status_code == 400
    api._sso_exchange.assert_not_awaited()
    api._establish_session.assert_not_awaited()


@pytest.mark.anyio
async def test_mixed_success_and_error_cannot_establish_session(api):
    with pytest.raises(HTTPException) as error:
        await api.sso_callback(
            request(api, ticket="synthetic-ticket", error="login_required"),
            session=None,
        )
    assert error.value.status_code == 400
    api._sso_exchange.assert_not_awaited()


@pytest.mark.anyio
async def test_valid_sso_still_exchanges_then_establishes_identity(api):
    response = await api.sso_callback(
        request(api, ticket="synthetic-ticket"), session=None
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    api._sso_exchange.assert_awaited_once_with(
        {}, "synthetic-ticket", "nonce", "http://test/callback"
    )
    api._establish_session.assert_awaited_once_with(None, "synthetic-token", "chat")
