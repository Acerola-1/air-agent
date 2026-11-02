"""终答后处理：未知角标移除、来源列表追加且与流式正文一致."""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessageChunk

from basic_qa.websearch_citations import WebAnswerCitations
from common.output_streaming.answer import stream_final_answer
from common.output_streaming.publisher import OutputPublisher

SOURCES = [
    {
        "n": 1,
        "title": "新规发布",
        "url": "https://example.com/policy",
        "site": "生态环境部",
        "date": "2026-10-01",
    }
]


class _Model:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def astream(self, messages, config=None, **kwargs):
        for text in self._chunks:
            yield AIMessageChunk(content=text)


def _run(chunks: list[str]):
    publisher = OutputPublisher({}, graph_id="basic-qa")
    result = asyncio.run(
        stream_final_answer(
            _Model(chunks),
            [],
            {},
            publisher=publisher,
            citation_filter=WebAnswerCitations(SOURCES),
        )
    )
    return result, publisher


def test_known_marker_and_reference_section_are_published():
    result, publisher = _run(["新规提出了更严格的要求[web:1]。"])
    assert "[web:1]" in result.text
    assert "**互联网来源**" in result.text
    assert "- **[1]** [新规发布](<https://example.com/policy>) — 生态环境部 · 2026-10-01" in result.text
    assert result.text == publisher.emitted_text


def test_unknown_marker_is_removed_before_publication():
    result, publisher = _run(["不存在的来源[web:9]不应出现。"])
    assert "[web:9]" not in result.text
    assert result.text == "不存在的来源不应出现。"
    assert "互联网来源" not in result.text
    assert result.text == publisher.emitted_text


def test_split_marker_across_chunks_is_kept():
    result, _ = _run(["政策要求[web", ":1]。"])
    assert "政策要求[web:1]。" in result.text
    assert "**互联网来源**" in result.text


def test_only_cited_sources_are_listed():
    result, _ = _run(["没有联网引用。"])
    assert "互联网来源" not in result.text
    assert "新规发布" not in result.text


def test_model_written_reference_section_is_replaced_by_server_list():
    result, publisher = _run(
        [
            "新规提出了更严格的要求[web:1]。\n\n---\n\n"
            "**互联网来源**\n\n- **[9]** 模型自造来源 https://fake.example/x\n"
        ]
    )
    assert "模型自造来源" not in result.text
    assert "[web:1]" in result.text
    assert "- **[1]** [新规发布](<https://example.com/policy>)" in result.text
    assert result.text == publisher.emitted_text


def test_model_written_list_without_markers_is_not_published():
    result, _ = _run(
        ["正文没有联网角标。\n\n**互联网来源**\n\n- **[2]** 模型自造来源\n"]
    )
    assert "模型自造来源" not in result.text
    assert "互联网来源" not in result.text
    assert result.text == "正文没有联网角标。"
