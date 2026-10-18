"""Regression for SDK authentication errors crossing the official ASGI adapter."""

import asyncio

import pytest
from langgraph_sdk import Auth
from starlette.requests import Request
from starlette.responses import Response

from common.artifact_http import native_identity_error_response


@pytest.mark.parametrize("status", [401, 403, 409, 503])
def test_preserves_identity_denial_status_without_internal_detail(status):
    """Dependency failures remain 503 and never expose adapter internals."""

    async def downstream(request):
        raise Auth.exceptions.HTTPException(status_code=status, detail="private detail")

    request = Request({"type": "http", "method": "GET", "path": "/assistants"})
    response = asyncio.run(native_identity_error_response(request, downstream))
    assert response.status_code == status
    assert b"private detail" not in response.body


def test_preserves_success_and_unexpected_failures():
    """The boundary does not turn unrelated failures into authentication errors."""
    expected = Response(status_code=204)

    async def success(request):
        return expected

    async def failure(request):
        raise RuntimeError("unexpected")

    request = Request({"type": "http", "method": "GET", "path": "/assistants"})
    assert asyncio.run(native_identity_error_response(request, success)) is expected
    with pytest.raises(RuntimeError, match="unexpected"):
        asyncio.run(native_identity_error_response(request, failure))
