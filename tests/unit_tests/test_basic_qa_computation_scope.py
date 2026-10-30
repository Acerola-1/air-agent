"""Basic QA 对受限计算继续复用会话归档边界."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from basic_qa.capabilities.projection import _include_tool_schema
from common.artifact_scope import sign_scope
from common.computation.tool import compute_artifact

pytestmark = pytest.mark.anyio


def test_only_registered_evidence_tool_is_permitted():
    assert _include_tool_schema("compute_artifact", is_mcp=False, material=None)
    assert not _include_tool_schema(
        "arbitrary_local_query", is_mcp=False, material=None
    )
    assert not _include_tool_schema("compute_artifact", is_mcp=True, material=None)


def _runtime(
    monkeypatch,
    *,
    scope_tenant="tenant",
    scope_user="user",
    scope_thread="thread",
    tenant="tenant",
    user="user",
    thread="thread",
):
    monkeypatch.setenv("ARCHIVE_SCOPE_KEYS", '["test-key-not-a-real-secret"]')
    token = sign_scope(scope_tenant, scope_user, scope_thread)
    config = {
        "configurable": {
            "thread_id": thread,
            "run_id": "run",
            "__artifact_scope": token,
            "langgraph_auth_user": {"identity": user, "tenant_id": tenant},
        }
    }
    return SimpleNamespace(
        config=config,
        stream_writer=lambda event: None,
        state={},
        tool_call_id="compute",
    )


async def test_wrong_tenant_or_thread_never_executes_code(monkeypatch):
    import common.computation.tool as module

    executor = AsyncMock()
    monkeypatch.setattr(module, "execute_code", executor)
    for runtime in (
        _runtime(monkeypatch, tenant="other"),
        _runtime(monkeypatch, thread="other"),
    ):
        with pytest.raises(PermissionError):
            await compute_artifact.coroutine("artifact", "return {};", runtime)
    executor.assert_not_awaited()


async def test_unknown_artifact_denied_and_owned_raw_positive(monkeypatch):
    import common.computation.tool as module

    runtime = _runtime(monkeypatch)
    fetch = AsyncMock(return_value=None)
    executor = AsyncMock(return_value={"result": 42, "scope": "完整", "coverage": []})
    monkeypatch.setattr(module, "get_tool_artifact", fetch)
    monkeypatch.setattr(module, "execute_code", executor)
    monkeypatch.setattr(
        module,
        "claim_computation",
        AsyncMock(return_value={"attempt": 1, "remaining_seconds": 10}),
    )
    monkeypatch.setattr(module, "save_tool_artifact", AsyncMock(return_value="derived"))
    denied = await compute_artifact.coroutine("unknown", "return {};", runtime)
    assert denied.update["messages"][0].status == "error"
    executor.assert_not_awaited()
    fetch.assert_awaited_with(
        "unknown", tenant_id="tenant", user_id="user", thread_id="thread"
    )
    fetch.return_value = {"kind": "raw", "raw_payload": "[]"}
    allowed = await compute_artifact.coroutine("owned", "return {};", runtime)
    assert allowed.update["messages"][0].status == "success"
    executor.assert_awaited_once()
    module.save_tool_artifact.assert_awaited_once()
