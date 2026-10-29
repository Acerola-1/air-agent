"""Remote 登录把 IAM 的凭据拒绝映射为客户端错误，而不是后端不可用。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from common.identity import remote
from fastapi import HTTPException
from httpx import Response

from app.routers import iam_remote


def _enable_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    monkeypatch.setenv("IAM_BASE_URL", "http://iam.invalid")
    monkeypatch.setenv("IAM_ISSUER", "test-issuer")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_ID", "test-client")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_SECRET", "test-secret")


def _request_iam_returning(status: int):
    seen: list[dict] = []

    def request(method, path, *, config, body=None, bearer=None, internal=True):
        seen.append({"method": method, "path": path, "body": body})
        return Response(status, json={"error": {"code": "bad_request"}})

    return request, seen


@pytest.mark.parametrize(
    ("iam_status", "expected_status"),
    [(400, 401), (403, 403)],
)
def test_iam_credential_rejection_is_not_reported_as_backend_unavailable(
    monkeypatch: pytest.MonkeyPatch, iam_status: int, expected_status: int
) -> None:
    _enable_remote(monkeypatch)
    request, seen = _request_iam_returning(iam_status)
    monkeypatch.setattr(remote, "request_iam", request)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            iam_remote.login_remote(
                {"account": "root", "password": "short"},
                SimpleNamespace(headers={"x-agent-frontend": "chat"}),
                SimpleNamespace(),
            )
        )

    assert caught.value.status_code == expected_status
    assert caught.value.status_code != 503
    # 服务端固定域与受众，不接受浏览器传入。
    assert seen[0]["path"] == "/v1/auth/login"
    assert seen[0]["body"]["realm"] == "workforce"
    assert seen[0]["body"]["audience"] == "air-agent"


def test_iam_outage_still_reports_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from common.identity.db import IdentityBackendUnavailable

    _enable_remote(monkeypatch)

    def request(*_args, **_kwargs):
        raise IdentityBackendUnavailable("IAM 不可达")

    monkeypatch.setattr(remote, "request_iam", request)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            iam_remote.login_remote(
                {"account": "root", "password": "whatever"},
                SimpleNamespace(headers={"x-agent-frontend": "chat"}),
                SimpleNamespace(),
            )
        )

    assert caught.value.status_code == 503
