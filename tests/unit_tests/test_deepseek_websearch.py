"""DeepSeek 联网搜索客户端：边界、错误类别与日志脱敏."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from common.websearch import deepseek


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeClient:
    last_kwargs: dict[str, object] = {}
    response: object = _FakeResponse(200, {"results": []})
    error: Exception | None = None

    def __init__(self, **kwargs: object) -> None:
        _FakeClient.last_kwargs = kwargs

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        _FakeClient.last_kwargs = {**_FakeClient.last_kwargs, "url": url, **kwargs}
        if _FakeClient.error is not None:
            raise _FakeClient.error
        assert isinstance(_FakeClient.response, _FakeResponse)
        return _FakeClient.response


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch: pytest.MonkeyPatch):
    _FakeClient.last_kwargs = {}
    _FakeClient.error = None
    _FakeClient.response = _FakeResponse(200, {"results": []})
    monkeypatch.setattr(deepseek.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "sk-test-secret-123")
    yield


def test_search_returns_structured_results_and_transport_guard():
    _FakeClient.response = _FakeResponse(
        200,
        {
            "results": [
                {
                    "title": "  政策公告  ",
                    "url": "https://example.com/a",
                    "site_name": "生态环境部",
                    "published_at": "2026-10-01",
                    "snippet": "正文摘要",
                },
                {
                    "title": "重复地址",
                    "url": "https://example.com/a",
                    "snippet": "不应重复",
                },
                {
                    "title": "非法协议",
                    "url": "javascript:alert(1)",
                    "snippet": "丢弃",
                },
            ]
        },
    )

    results = asyncio.run(deepseek.search_web("空气质量 新规", count=5))

    assert len(results) == 1
    assert results[0].title == "政策公告"
    assert results[0].site == "生态环境部"
    assert results[0].date == "2026-10-01"
    assert results[0].summary == ""
    assert results[0].content == ""
    assert results[0].rerank_score is None
    assert results[0].authority is None
    assert _FakeClient.last_kwargs["timeout"] == 20.0
    assert _FakeClient.last_kwargs["follow_redirects"] is False
    assert _FakeClient.last_kwargs["trust_env"] is False
    assert _FakeClient.last_kwargs["url"] == "https://api.deepseek.com/v1/tools/search"
    body = _FakeClient.last_kwargs["json"]
    assert body == {"query": "空气质量 新规", "count": 5, "need_content": True}


def test_empty_results_are_not_an_error():
    _FakeClient.response = _FakeResponse(200, {"results": []})
    assert asyncio.run(deepseek.search_web("空结果")) == []


def test_missing_key_fails_closed_without_request(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deepseek.config, "DEEPSEEK_API_KEY", "")
    with pytest.raises(deepseek.DeepSeekSearchError) as captured:
        asyncio.run(deepseek.search_web("缺密钥"))
    assert captured.value.category == "missing_key"
    assert "url" not in _FakeClient.last_kwargs


def test_timeout_is_classified():
    _FakeClient.error = httpx.TimeoutException("slow")
    with pytest.raises(deepseek.DeepSeekSearchError) as captured:
        asyncio.run(deepseek.search_web("超时"))
    assert captured.value.category == "timeout"


def test_non_200_is_classified():
    _FakeClient.response = _FakeResponse(502, {})
    with pytest.raises(deepseek.DeepSeekSearchError) as captured:
        asyncio.run(deepseek.search_web("服务错误"))
    assert captured.value.category == "http_error"
    assert captured.value.status == 502


def test_malformed_payload_is_classified():
    _FakeClient.response = _FakeResponse(200, {"unexpected": "shape"})
    with pytest.raises(deepseek.DeepSeekSearchError) as captured:
        asyncio.run(deepseek.search_web("畸形响应"))
    assert captured.value.category == "protocol_error"


def test_query_without_key_or_full_query_is_not_logged():
    messages: list[str] = []
    sink_id = deepseek.logger.add(
        lambda message: messages.append(str(message)), level="DEBUG"
    )
    try:
        _FakeClient.response = _FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "标题",
                        "url": "https://example.com/secret-page",
                        "snippet": "x" * 5000,
                    }
                ]
            },
        )
        results = asyncio.run(deepseek.search_web("敏感查询词-不要记录"))
    finally:
        deepseek.logger.remove(sink_id)
    assert len(results) == 1
    assert len(results[0].snippet) == deepseek.MAX_SNIPPET_CHARS
    joined = "\n".join(messages)
    assert "sk-test-secret-123" not in joined
    assert "敏感查询词-不要记录" not in joined
    assert "https://example.com/secret-page" not in joined


def test_invalid_query_is_rejected_before_outbound():
    with pytest.raises(ValueError):
        asyncio.run(deepseek.search_web("   "))
    assert "url" not in _FakeClient.last_kwargs


def test_query_over_official_limit_is_rejected_before_outbound():
    with pytest.raises(ValueError):
        asyncio.run(deepseek.search_web("空" * 129))
    assert "url" not in _FakeClient.last_kwargs


def test_summary_content_and_scores_stay_separate():
    marker = "正文标记"
    _FakeClient.response = _FakeResponse(
        200,
        {
            "results": [
                {
                    "title": "政策",
                    "url": "https://example.com/a",
                    "snippet": "短摘录",
                    "summary": "长" * 1500,
                    "content": marker + ("正" * 30000),
                    "rerank_score": 2.4,
                    "authority": -1,
                    "site_name": "生态环境部",
                    "date": "2026-06-01T00:00:00+00:00",
                },
                {
                    "title": "越界分数",
                    "url": "https://example.com/b",
                    "snippet": "短",
                    "summary": "超" * 5000,
                    "rerank_score": 9,
                    "authority": True,
                },
            ]
        },
    )

    results = asyncio.run(deepseek.search_web("空气质量"))

    assert results[0].snippet == "短摘录"
    assert marker not in results[0].snippet
    assert results[0].summary == "长" * 1500
    assert results[1].summary == "超" * deepseek.MAX_SUMMARY_CHARS
    assert results[0].content.startswith(marker)
    assert len(results[0].content) == deepseek.MAX_CONTENT_CHARS
    assert results[0].rerank_score == 2.4
    assert results[0].authority == -1
    assert results[1].rerank_score is None
    assert results[1].authority is None
