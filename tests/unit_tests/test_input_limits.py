"""单次输入字符上限：Unicode 码点、服务端权威与入口拒绝."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from common.config import config
from common.input_limits import (
    count_user_characters,
    current_input_limit,
    input_overflow_message,
)


def test_unicode_code_point_counting():
    assert count_user_characters("中文abc") == 5
    assert count_user_characters("😀") == 1
    # 家庭 emoji 由多个码点与 ZWJ 组成，两端都按码点而不是 UTF-16 单元计数。
    assert count_user_characters("👨‍👩‍👧‍👦") == 7
    assert count_user_characters("第一行\n第二行") == 7


def test_limit_is_independent_from_model_window(monkeypatch):
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 12000)
    monkeypatch.setattr(config, "MODEL_MAX_CONTEXT_TOKENS", 1_000_000)
    limit = current_input_limit()
    assert limit.max_chars == 12000
    assert limit.character_count_mode == "unicode_code_point"


def test_invalid_limit_fails_explicitly(monkeypatch):
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 0)
    with pytest.raises(ValueError, match="MAX_INPUT_CHARS"):
        current_input_limit()


def test_overflow_message_keeps_draft_semantics():
    message = input_overflow_message(9000, 8000)
    assert "9000" in message and "8000" in message
    assert "草稿不会被截断" in message


@pytest.mark.anyio
async def test_business_entry_rejects_without_model(monkeypatch):
    from common.business_graph import nodes as module
    from common.models import ModelRegistry

    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 10)
    model = object()
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    result = await module.BusinessGraphNodes(
        router=None,
        base_system_prompt="",
        load_skill_tool=None,
        graph_name="basic-qa",
    ).classify_intent({"messages": [HumanMessage(content="x" * 11)]}, {})
    assert result["intent"] == "input_overflow"


@pytest.mark.anyio
async def test_data_entry_rejects_before_progress(monkeypatch):
    from data_analysis import nodes as module

    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 10)
    events = []

    async def publish(*args, **kwargs):
        events.append(kwargs)
        return HumanMessage(content="拒绝")

    monkeypatch.setattr(module, "publish_control_message", publish)
    result = await module.validate_input(
        {"messages": [HumanMessage(content="x" * 11)]}, {}, writer=lambda event: None
    )
    assert result["input_overflow"] is True
    assert events and events[0]["control_code"] == "input_overflow"


@pytest.mark.anyio
async def test_chat_config_endpoint_returns_authoritative_limit(monkeypatch):
    import json

    from common import artifact_http

    async def authenticate(request):
        return {"tenant_id": "t", "membership_id": "m", "identity": "u"}

    grants = []
    monkeypatch.setattr(artifact_http, "_authenticate_custom", authenticate)
    monkeypatch.setattr(
        "common.identity.runtime.identity_from_auth_user", lambda actor: object()
    )
    monkeypatch.setattr(
        "common.identity.business_access.grant_for_graph",
        lambda **kwargs: grants.append(kwargs),
    )
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 12000)
    response = await artifact_http.get_chat_config(None, "basic-qa")
    body = json.loads(response.body)
    assert body == {
        "graph_id": "basic-qa",
        "max_input_chars": 12000,
        "character_count_mode": "unicode_code_point",
    }
    assert response.headers["cache-control"] == "no-store"
    assert grants[0]["graph_id"] == "basic-qa"


@pytest.mark.anyio
async def test_chat_config_rejects_internal_and_unauthorized(monkeypatch):
    from fastapi import HTTPException

    from common import artifact_http
    from common.identity.business_access import BusinessAccessError

    async def migration(request):
        return {
            "tenant_id": "t",
            "membership_id": "m",
            "identity": "u",
            "is_migration": True,
        }

    monkeypatch.setattr(artifact_http, "_authenticate_custom", migration)
    with pytest.raises(HTTPException) as denied:
        await artifact_http.get_chat_config(None, "basic-qa")
    assert denied.value.status_code == 403

    async def member(request):
        return {"tenant_id": "t", "membership_id": "m", "identity": "u"}

    def no_business(**kwargs):
        raise BusinessAccessError("未授权业务", status_code=403)

    monkeypatch.setattr(artifact_http, "_authenticate_custom", member)
    monkeypatch.setattr(
        "common.identity.runtime.identity_from_auth_user", lambda actor: object()
    )
    monkeypatch.setattr("common.identity.business_access.grant_for_graph", no_business)
    with pytest.raises(HTTPException) as unauthorized:
        await artifact_http.get_chat_config(None, "basic-qa")
    assert unauthorized.value.status_code == 403
