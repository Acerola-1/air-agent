"""Shared knowledge tool contract, evidence policy and normalization tests."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

import common.knowledge.client as client_module
from common.identity.runtime import RuntimeIdentity
from common.knowledge.client import (
    _normalized_libraries,
    _select_references,
    search_knowledge,
)
from common.knowledge.contracts import Delegation
from common.knowledge.policy import enforce_answer_policy


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="01940000-0000-4000-8000-000000000001",
        tenant_id="01940000-0000-4000-8000-000000000002",
        membership_id="01940000-0000-4000-8000-000000000003",
        session_id="01940000-0000-4000-8000-000000000004",
        session_ctx_version=1,
        user_auth_revision=0,
        tenant_auth_revision=1,
        membership_revision=1,
        verification_ref="01940000-0000-4000-8000-000000000005",
    )


def _digest() -> str:
    return hashlib.sha256(b"source").hexdigest()


def test_library_codes_are_bounded_and_deduplicated() -> None:
    assert _normalized_libraries(["air", "air", "water"]) == ["air", "water"]
    with pytest.raises(ValueError):
        _normalized_libraries([])
    with pytest.raises(ValueError):
        _normalized_libraries(["../etc"])
    with pytest.raises(ValueError):
        _normalized_libraries(["air"] * 11)


def test_reference_selection_uses_composite_identity_and_caps() -> None:
    evidence = [
        {
            "workspace": "air",
            "chunk_id": "same",
            "doc_id": "d1",
            "doc_name": "A",
            "kind": "chunk",
            "content": "A" * 600,
            "verified": True,
            "source_digest": _digest(),
            "source_version": "v1",
            "location": None,
        },
        {
            "workspace": "water",
            "chunk_id": "same",
            "doc_id": "d2",
            "doc_name": "W",
            "kind": "chunk",
            "content": "W",
            "verified": True,
            "source_digest": _digest(),
            "source_version": "v1",
        },
        {
            "workspace": "air",
            "chunk_id": "bad",
            "content": "x",
            "verified": False,
            "source_digest": _digest(),
        },
    ]
    selected = _select_references(evidence, record_id="rec", outcome="ok")
    assert [item["workspace"] for item in selected["items"]] == ["air", "water"]
    assert selected["truncated"] is False
    assert len(selected["items"][0]["excerpt"]) == 500


class _FakeResponse:
    def __init__(self, status: int, payload: dict | None) -> None:
        self.status_code = status
        self._payload = payload
        self.content = json.dumps(payload or {}).encode()

    def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self):
        yield self.content


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return self.response

    def stream(self, *args, **kwargs):
        return self.response


def _patch_search(monkeypatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(client_module, "revalidate", AsyncMock())
    monkeypatch.setattr(
        client_module,
        "issue_delegation",
        AsyncMock(
            return_value=Delegation(
                token="kdl_secret",
                operation="query",
                expires_at=None,
                user_id=_identity().user_id,
                session_id=_identity().session_id,
                tenant_id=_identity().tenant_id,
                membership_id=_identity().membership_id,
                context_version=1,
                revisions={"session_ctx_version": 1},
            )
        ),
    )
    monkeypatch.setattr(
        client_module, "register_citations", AsyncMock(return_value=["cid-1"])
    )
    monkeypatch.setattr(
        client_module.httpx, "AsyncClient", lambda **kwargs: _FakeClient(response)
    )
    monkeypatch.setenv("KNOWLEDGE_API_URL", "http://127.0.0.1:8099")


def _result_payload(status: str = "retrieved", reason: str | None = None) -> dict:
    result = {
        "status": status,
        "evidence": [
            {
                "workspace": "air",
                "chunk_id": "c1",
                "doc_id": "d1",
                "doc_name": "标准A",
                "kind": "chunk",
                "content": "测试限值 35 测试单位",
                "verified": True,
                "source_digest": _digest(),
                "source_version": "v1",
                "location": None,
            }
        ],
        "workspace_snapshots": {},
        "per_workspace": [],
    }
    if reason:
        result["retrieval_reason"] = reason
    return {"record_id": "rec", "workspaces": ["air"], "result": result}


@pytest.mark.anyio
async def test_search_returns_registered_reference(monkeypatch) -> None:
    _patch_search(monkeypatch, _FakeResponse(200, _result_payload()))
    payload = await search_knowledge(
        _identity(), query="标准A限值", library_codes=None, top_k=8
    )
    assert payload["outcome"] == "ok"
    assert payload["retrieval_status"] == "retrieved"
    assert payload["completeness"] == "candidate_only"
    assert payload["references"][0]["citation_id"] == "cid-1"
    assert payload["references"][0]["location_status"] == "not_provided"
    assert "kdl_secret" not in json.dumps(payload)


@pytest.mark.anyio
async def test_empty_discovery_maps_to_no_match(monkeypatch) -> None:
    payload = _result_payload("missing", "empty_discovery")
    payload["result"]["evidence"] = []
    _patch_search(monkeypatch, _FakeResponse(200, payload))
    result = await search_knowledge(
        _identity(), query="不存在的标准", library_codes=None, top_k=8
    )
    assert result["outcome"] == "no_match"
    assert result["retrieval_reason"] == "empty_discovery"


@pytest.mark.anyio
async def test_permission_denied_has_no_reference(monkeypatch) -> None:
    _patch_search(monkeypatch, _FakeResponse(403, {"error": "forbidden"}))
    result = await search_knowledge(
        _identity(), query="标准A限值", library_codes=["water"], top_k=8
    )
    assert result["outcome"] == "permission_denied"
    assert result["references"] == []


def _tool_message(citation_id: str, excerpt: str, number: str = "35") -> ToolMessage:
    return ToolMessage(
        name="knowledge_search",
        tool_call_id="call-1",
        content=json.dumps(
            {
                "outcome": "ok",
                "retrieval_status": "retrieved",
                "references": [
                    {
                        "citation_id": citation_id,
                        "doc_name": "标准A",
                        "excerpt": excerpt,
                        "source_sha256": _digest(),
                        "verified": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )


def test_policy_blocks_uncited_concrete_limit() -> None:
    answer = "依据《测试空气标准A》2026版第5条，示例限值是35测试单位。"
    published, blocked, reason = enforce_answer_policy(
        answer, [HumanMessage(content="q")]
    )
    assert blocked is True
    assert reason == "retrieval_required"
    assert "未取得可核验" in published


def test_policy_accepts_verified_excerpt() -> None:
    citation = "11111111-1111-4111-8111-111111111111"
    answer = (
        f"依据《测试空气标准A》2026版第5条，示例限值是35测试单位 [[cite:{citation}]]。"
    )
    message = _tool_message(
        citation,
        "《测试空气标准A》2026版第5条：测试指标A的示例限值为35测试单位。此条仅供软件测试。",
    )
    published, blocked, _ = enforce_answer_policy(answer, [message])
    assert blocked is False
    assert "测试指标A的示例限值为35测试单位" in published
    assert f"[[cite:{citation}]]" in published


def test_policy_blocks_number_tampering_even_with_real_citation() -> None:
    citation = "11111111-1111-4111-8111-111111111111"
    answer = (
        f"依据《测试空气标准A》2026版第5条，示例限值是350测试单位 [[cite:{citation}]]。"
    )
    message = _tool_message(citation, "测试指标A的示例限值为35测试单位。")
    _, blocked, reason = enforce_answer_policy(answer, [message])
    assert blocked is True
    assert reason in {"unsupported_number_or_unit", "unsupported_version"}


def test_policy_blocks_unknown_citation_id() -> None:
    answer = "依据《标准》第1条 [[cite:99999999-9999-4999-8999-999999999999]]。"
    _, blocked, reason = enforce_answer_policy(answer, [])
    assert blocked is True
    assert reason == "unknown_citation_id"


def test_analysis_without_menu_keeps_only_generic_tools() -> None:
    from common.runtime_tools import get_business_tools
    from data_analysis.prompt_builder import filter_business_tools

    tools = get_business_tools(include_computation=True)
    names = {tool.name for tool in filter_business_tools(tools, [])}
    assert "knowledge_search" in names
    assert "compute_artifact" in names
    # No data-analysis skill may be started without a menu.
    assert "parse_region_tool" not in names
    assert "websearch_tool" not in names
