"""联网审批：工具只登记、gate 才外呼，预算与恢复行为固定."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from basic_qa.capabilities import websearch


def _state(**overrides):
    state = {
        "bqa_turn_key": "attempt-1",
        "websearch_turn_key": "attempt-1",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "web_search", "args": {}}],
            )
        ],
    }
    state.update(overrides)
    return state


def _pending(query="空气质量新规"):
    return {"tool_call_id": "call-1", "query": query, "reason": "政策公告"}


def _result(url="https://example.com/a", title="新规", **fields):
    data = {
        "url": url,
        "title": title,
        "site": "生态环境部",
        "date": "2026-10-01",
        "snippet": "摘要",
    }
    data.update(fields)
    return SimpleNamespace(**data)


def _run_tool(state, query="空气质量新规", reason="政策公告"):
    return asyncio.run(
        websearch.web_search.coroutine(
            query=query,
            reason=reason,
            runtime=SimpleNamespace(state=state, tool_call_id="call-1"),
            config={},
        )
    )


def _run_gate(state):
    return asyncio.run(websearch.websearch_gate(state, {}))


def test_tool_registers_pending_without_outbound(monkeypatch):
    monkeypatch.setattr(websearch.app_config, "DEEPSEEK_API_KEY", "sk-test")
    called = []
    monkeypatch.setattr(websearch, "search_web", lambda *a, **k: called.append(1))

    output = _run_tool(_state())

    assert called == []
    assert output.update["pending_websearch"] == _pending()
    message = output.update["messages"][0]
    assert message.tool_call_id == "call-1"
    assert message.id == "websearch_pending_call-1"
    assert json.loads(message.content)["status"] == "pending"


def test_tool_fails_closed_without_key(monkeypatch):
    monkeypatch.setattr(websearch.app_config, "DEEPSEEK_API_KEY", "")
    output = _run_tool(_state())
    assert output.status != "error"
    assert json.loads(output.content)["message"] == websearch.GENERIC_UNAVAILABLE


def test_tool_rejects_multiple_requests_in_one_batch(monkeypatch):
    monkeypatch.setattr(websearch.app_config, "DEEPSEEK_API_KEY", "sk-test")
    state = _state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call-1", "name": "web_search", "args": {}},
                    {"id": "call-2", "name": "web_search", "args": {}},
                ],
            )
        ]
    )
    output = _run_tool(state)
    assert output.status == "error"
    assert json.loads(output.content)["error_code"] == "websearch_batch_conflict"


def test_tool_rejects_invalid_arguments(monkeypatch):
    monkeypatch.setattr(websearch.app_config, "DEEPSEEK_API_KEY", "sk-test")
    output = _run_tool(_state(), query="   ", reason="")
    assert output.status == "error"
    assert json.loads(output.content)["error_code"] == "websearch_invalid_query"


def test_tool_rejects_query_over_official_limit_before_pending(monkeypatch):
    monkeypatch.setattr(websearch.app_config, "DEEPSEEK_API_KEY", "sk-test")
    output = _run_tool(_state(), query="空" * 129, reason="政策公告")
    assert output.status == "error"
    assert json.loads(output.content)["error_code"] == "websearch_invalid_query"


def test_gate_denied_never_calls_search(monkeypatch):
    called = []
    monkeypatch.setattr(websearch, "search_web", lambda *a, **k: called.append(1))
    output = _run_gate(_state(pending_websearch=_pending(), websearch_denied=True))
    assert called == []
    assert output["pending_websearch"] is None
    assert json.loads(output["messages"][0].content)["status"] == "unavailable"


def test_gate_budget_exhausted_never_calls_search(monkeypatch):
    called = []
    monkeypatch.setattr(websearch, "search_web", lambda *a, **k: called.append(1))
    output = _run_gate(
        _state(pending_websearch=_pending(), websearch_granted=True, websearch_used=3)
    )
    assert called == []
    assert output["websearch_used"] == 3
    assert output["pending_websearch"] is None


def test_gate_approval_runs_one_search_and_assigns_numbers(monkeypatch):
    calls = []

    async def fake_search(query, *, count):
        calls.append((query, count))
        return [_result()]

    monkeypatch.setattr(websearch, "search_web", fake_search)
    prompts = []

    def fake_interrupt(payload):
        prompts.append(payload)
        return {"approved": True}

    monkeypatch.setattr(websearch, "interrupt", fake_interrupt)

    output = _run_gate(_state(pending_websearch=_pending()))

    assert prompts == [
        {
            "type": "websearch_approval",
            "provider": "DeepSeek",
            "query": "空气质量新规",
            "reason": "政策公告",
            "remaining": 3,
            "used": 0,
        }
    ]
    assert calls == [("空气质量新规", 5)]
    assert output["websearch_granted"] is True
    assert output["websearch_used"] == 1
    assert output["websearch_denied"] is False
    assert output["websearch_sources"][0]["n"] == 1
    assert "snippet" not in output["websearch_sources"][0]
    assert "summary" not in output["websearch_sources"][0]
    assert output["pending_websearch"] is None
    message = output["messages"][0]
    assert message.id == "websearch_pending_call-1"
    payload = json.loads(message.content)
    assert payload["results"][0]["n"] == 1
    assert payload["remaining"] == 2


def test_gate_denial_locks_the_turn(monkeypatch):
    called = []
    monkeypatch.setattr(websearch, "search_web", lambda *a, **k: called.append(1))
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": False})
    output = _run_gate(_state(pending_websearch=_pending()))
    assert called == []
    assert output["websearch_denied"] is True
    assert output["websearch_granted"] is False
    assert output["websearch_used"] == 0


def test_gate_failure_counts_against_budget(monkeypatch):
    async def failing(query, *, count):
        raise websearch.DeepSeekSearchError("timeout")

    monkeypatch.setattr(websearch, "search_web", failing)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})
    output = _run_gate(_state(pending_websearch=_pending()))
    assert output["websearch_used"] == 1
    assert output["websearch_granted"] is True
    assert output["websearch_sources"] == []
    assert (
        json.loads(output["messages"][0].content)["message"]
        == websearch.GENERIC_UNAVAILABLE
    )


def test_gate_granted_skips_interrupt_and_dedupes_urls(monkeypatch):
    prompts = []
    monkeypatch.setattr(
        websearch,
        "interrupt",
        lambda payload: prompts.append(payload) or {"approved": True},
    )

    async def fake_search(query, *, count):
        return [_result(), _result(url="https://example.com/b", title="另一条")]

    monkeypatch.setattr(websearch, "search_web", fake_search)
    existing = [
        {
            "n": 1,
            "title": "旧",
            "url": "https://example.com/a",
            "site": "",
            "date": "",
            "snippet": "",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        }
    ]
    output = _run_gate(
        _state(
            pending_websearch=_pending("第二个问题"),
            websearch_granted=True,
            websearch_used=1,
            websearch_sources=existing,
        )
    )
    assert prompts == []
    assert output["websearch_used"] == 2
    numbers = [item["n"] for item in output["websearch_sources"]]
    assert numbers == [1, 2]
    assert json.loads(output["messages"][0].content)["remaining"] == 1


def test_gate_resets_budget_when_turn_key_changes(monkeypatch):
    calls = []

    async def fake_search(query, *, count):
        calls.append(query)
        return [_result()]

    monkeypatch.setattr(websearch, "search_web", fake_search)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})
    output = _run_gate(
        _state(
            bqa_turn_key="attempt-2",
            pending_websearch=_pending("新问题"),
            websearch_turn_key="attempt-1",
            websearch_granted=True,
            websearch_used=3,
            websearch_denied=True,
        )
    )
    assert calls == ["新问题"]
    assert output["websearch_turn_key"] == "attempt-2"
    assert output["websearch_used"] == 1
    assert output["websearch_denied"] is False


def test_gate_without_pending_is_a_noop():
    assert _run_gate({"bqa_turn_key": "attempt-1"}) == {}


def test_serper_tool_is_retired_and_web_search_is_the_only_entry():
    import common.tools as common_tools
    from basic_qa.capabilities.projection import disclosed_tools

    assert not hasattr(common_tools, "websearch_tool")
    assert "websearch_tool" not in common_tools.__all__
    assert [tool.name for tool in disclosed_tools([websearch.web_search], None)] == [
        "web_search"
    ]


def test_gate_emits_audit_and_external_cost_without_query(monkeypatch):
    events = []

    def capture(config, event_type, **values):
        events.append((event_type, values))
        return {}

    async def fake_search(query, *, count):
        return [_result()]

    monkeypatch.setattr(websearch, "emit_event", capture)
    monkeypatch.setattr(websearch, "search_web", fake_search)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})

    _run_gate(_state(pending_websearch=_pending("敏感查询词-不记录")))

    types = [item[0] for item in events]
    assert types == [
        "websearch_approval_required",
        "websearch_approval",
        "websearch_call",
        "websearch_external_cost",
        "websearch_call",
    ]
    approval = next(values for name, values in events if name == "websearch_approval")
    assert approval["decision"] == "approved"
    call = next(
        values
        for name, values in events
        if name == "websearch_call" and values.get("status") == "ok"
    )
    assert call["used"] == 1
    assert call["remaining"] == 2
    assert call["results"] == 1
    cost = next(values for name, values in events if name == "websearch_external_cost")
    assert cost["amount"] == "0.005"
    assert cost["source"] == "deepseek-websearch"
    assert cost["currency"] == "CNY"
    assert "tokens" not in cost
    assert "敏感查询词-不记录" not in json.dumps(events, ensure_ascii=False)


def test_gate_records_denial_without_outbound_cost(monkeypatch):
    events = []

    def capture(config, event_type, **values):
        events.append((event_type, values))
        return {}

    monkeypatch.setattr(websearch, "emit_event", capture)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": False})
    _run_gate(_state(pending_websearch=_pending()))

    types = [item[0] for item in events]
    assert "websearch_external_cost" not in types
    approval = next(values for name, values in events if name == "websearch_approval")
    assert approval["decision"] == "denied"


def test_three_outbounds_are_allowed_and_the_fourth_is_unavailable(monkeypatch):
    calls = []

    async def fake_search(query, *, count):
        calls.append(query)
        return [_result(url=f"https://example.com/{len(calls)}")]

    monkeypatch.setattr(websearch, "search_web", fake_search)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})

    state = _state(pending_websearch=_pending("第一次"))
    first = _run_gate(state)
    assert first["websearch_used"] == 1

    state = _state(
        pending_websearch=_pending("第二次"),
        websearch_granted=True,
        websearch_used=1,
        websearch_sources=first["websearch_sources"],
    )
    second = _run_gate(state)
    assert second["websearch_used"] == 2

    state = _state(
        pending_websearch=_pending("第三次"),
        websearch_granted=True,
        websearch_used=2,
        websearch_sources=second["websearch_sources"],
    )
    third = _run_gate(state)
    assert third["websearch_used"] == 3

    state = _state(
        pending_websearch=_pending("第四次"),
        websearch_granted=True,
        websearch_used=3,
        websearch_sources=third["websearch_sources"],
    )
    fourth = _run_gate(state)
    assert calls == ["第一次", "第二次", "第三次"]
    assert fourth["websearch_used"] == 3
    assert json.loads(fourth["messages"][0].content)["status"] == "unavailable"


def test_gate_empty_search_keeps_budget_and_records_zero_cost(monkeypatch):
    events = []

    def capture(config, event_type, **values):
        events.append((event_type, values))
        return {}

    async def empty(query, *, count):
        return []

    monkeypatch.setattr(websearch, "emit_event", capture)
    monkeypatch.setattr(websearch, "search_web", empty)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})

    output = _run_gate(_state(pending_websearch=_pending()))

    assert output["websearch_used"] == 1
    cost = next(values for name, values in events if name == "websearch_external_cost")
    assert cost["amount"] == "0"
    assert cost["measurement_status"] == "measured"
    call = next(
        values
        for name, values in events
        if name == "websearch_call" and values.get("status") == "empty"
    )
    assert call["results"] == 0


def test_gate_gives_model_summary_and_top_excerpts_only(monkeypatch):
    padding = "后续" * 5000
    links = "\n".join(f"[栏目](https://example.com/{index})" for index in range(4))
    content = f"导航\n{links}\n政策正文。PM2.5 年均浓度为 30 微克。{padding}"

    async def fake_search(query, *, count):
        return [
            _result(
                url="https://example.com/low",
                title="低分",
                rerank_score=1.2,
                authority=4,
                summary="低分摘要",
                content=content,
            ),
            _result(
                url="https://example.com/best",
                title="高分",
                rerank_score=2.6,
                authority=-1,
                summary="高分摘要",
                content=content,
            ),
            _result(
                url="https://example.com/mid",
                title="次高",
                rerank_score=1.8,
                authority=2,
                summary="次高摘要",
                content="很短的正文",
            ),
            _result(
                url="https://example.com/third",
                title="第三",
                rerank_score=1.6,
                authority=3,
                summary="第三摘要",
                content=content,
            ),
        ]

    monkeypatch.setattr(websearch, "search_web", fake_search)
    monkeypatch.setattr(websearch, "interrupt", lambda payload: {"approved": True})

    output = _run_gate(_state(pending_websearch=_pending("PM2.5 年均")))
    payload = json.loads(output["messages"][0].content)
    by_url = {item["url"]: item for item in payload["results"]}

    assert by_url["https://example.com/low"]["summary"] == "低分摘要"
    assert "content_excerpt" not in by_url["https://example.com/low"]
    best = by_url["https://example.com/best"]
    assert best["authority"] == -1
    assert best["content_truncated"] is True
    assert "PM2.5 年均浓度为 30" in best["content_excerpt"]
    assert "[栏目]" not in best["content_excerpt"]
    assert len(best["content_excerpt"]) <= websearch.CONTENT_EXCERPT_CHARS
    mid = by_url["https://example.com/mid"]
    assert mid["content_excerpt"] == "很短的正文"
    assert mid["content_truncated"] is False
    assert "content_excerpt" not in by_url["https://example.com/third"]
    assert all(
        "snippet" not in item and "summary" not in item
        for item in output["websearch_sources"]
    )
