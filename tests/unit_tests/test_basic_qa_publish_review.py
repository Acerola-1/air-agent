"""Basic QA 终答按句审核：不因问题命中而整篇拒答。"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

from common.output_streaming.answer import stream_final_answer
from common.output_streaming.contract import EVENT_DELTA, EVENT_DONE
from common.output_streaming.publisher import OutputPublisher
from common.output_streaming.review import (
    ABSTAIN_FAILED,
    ABSTAIN_MISMATCH,
    ABSTAIN_NO_MATCH,
    ABSTAIN_UNKNOWN_CITE,
    AnswerReview,
)


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def of(self, event_type: str) -> list[dict]:
        return [event for event in self.events if event.get("type") == event_type]


class _Model:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def astream(self, messages, config=None):  # noqa: ANN001
        for text in self.chunks:
            yield AIMessageChunk(content=text)


def _publisher(recorder: _Recorder) -> OutputPublisher:
    return OutputPublisher(
        {"configurable": {"thread_id": "thread-1"}},
        graph_id="basic-qa",
        writer=recorder,
    )


def _knowledge(outcome: str, excerpt: str = "", citation_id: str = "") -> ToolMessage:
    references = []
    if citation_id:
        references.append(
            {
                "citation_id": citation_id,
                "verified": True,
                "doc_name": "受控标准",
                "excerpt": excerpt,
            }
        )
    status = "retrieved" if outcome == "ok" else None
    return ToolMessage(
        name="knowledge_search",
        tool_call_id="knowledge",
        content=json.dumps(
            {
                "outcome": outcome,
                "retrieval_status": status,
                "references": references,
            },
            ensure_ascii=False,
        ),
    )


def _web(*snippets: str) -> ToolMessage:
    return ToolMessage(
        name="web_search",
        tool_call_id="web",
        content=json.dumps(
            {
                "status": "ok",
                "results": [
                    {"n": index, "summary": text, "title": "网页"}
                    for index, text in enumerate(snippets, start=1)
                ],
            },
            ensure_ascii=False,
        ),
    )


async def _answer(chunks: list[str], context: list) -> tuple[str, str]:
    recorder = _Recorder()
    result = await stream_final_answer(
        _Model(chunks),
        context,
        {},
        publisher=_publisher(recorder),
        policy_messages=context,
        question=context[0].content,
        answer_review=True,
    )
    assert result.delivery_mode == "token"
    assert recorder.of(EVENT_DONE)
    published = "".join(event["message"] for event in recorder.of(EVENT_DELTA))
    assert published == result.text
    return result.text, published


@pytest.mark.anyio
async def test_question_hit_does_not_block_background_answer():
    text, _published = await _answer(
        ["这是背景说明，不给出数值。"],
        [HumanMessage(content="PM2.5 的标准限值是多少？")],
    )
    assert text == "这是背景说明，不给出数值。"
    assert "未取得可核验" not in text
    assert "未核到" not in text


@pytest.mark.anyio
async def test_web_measurement_survives_when_knowledge_misses():
    context = [
        HumanMessage(content="标准限值查不到的话，网上的浓度是多少？"),
        _knowledge("no_match"),
        _web("当天浓度为 42 微克/立方米"),
    ]
    text, _published = await _answer(
        ["当天浓度为 42 微克/立方米 [web:1]。"],
        context,
    )
    assert "42" in text
    assert "[web:1]" in text
    assert "未核到" not in text


@pytest.mark.anyio
async def test_web_limit_is_rewritten_and_other_sentences_remain():
    context = [
        HumanMessage(content="限值是多少？"),
        _knowledge("no_match"),
        _web("PM2.5 日均 75 微克/立方米"),
    ]
    text, _published = await _answer(
        ["标准限值为 75 微克/立方米。", "其余背景保留。"],
        context,
    )
    assert "标准限值为 75" not in text
    assert "网页记载为 75微克/立方米 [web:1]" in text
    assert "未经知识库原文核验" in text
    assert "其余背景保留。" in text


def test_quote_keeps_one_plain_sentence_and_drops_retrieval_noise():
    first = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    noisy = "\n".join(
        [
            '{"doc_id": "doc-1", "doc_name": "air-02_标准.pdf"}',
            "## 4 环境空气功能区分类",
            "测试指标的示例限值为 35 微克/立方米。",
            '<table id="tb-1"><tr><td rowspan="2">序号</td><td>75</td></tr>',
            "二氧化硫( $SO_2$ )",
        ]
    )
    name = "air-02_生态环境部_环境空气质量标准_GB3095-2012.pdf"
    payload = {
        "outcome": "ok",
        "retrieval_status": "retrieved",
        "references": [
            {
                "citation_id": citation_id,
                "verified": True,
                "doc_name": name,
                "excerpt": noisy,
            }
            for citation_id in (first, second)
        ],
    }
    review = AnswerReview.from_messages(
        [
            HumanMessage(content="限值"),
            ToolMessage(
                name="knowledge_search",
                tool_call_id="knowledge",
                content=json.dumps(payload, ensure_ascii=False),
            ),
        ]
    )
    text = review.review_unit("模型改写的限值为 35 微克/立方米。")
    assert text == (f"原文：测试指标的示例限值为 35 微克/立方米。 [[cite:{first}]]")
    assert "doc_id" not in text
    assert "<table" not in text
    assert "##" not in text
    assert "> " not in text
    assert "air-02_" not in text
    assert second not in text


def test_loose_citation_marker_becomes_source_button_form():
    citation = "11111111-1111-4111-8111-111111111111"
    review = AnswerReview.from_messages(
        [
            HumanMessage(content="介绍"),
            _knowledge("ok", "背景原文。", citation),
        ]
    )
    text = review.review_unit(f"适用规则见 [citation:{citation}]。")
    assert f"[[cite:{citation}]]" in text
    assert "[citation:" not in text


@pytest.mark.anyio
async def test_knowledge_support_quotes_only_that_sentence():
    citation = "11111111-1111-4111-8111-111111111111"
    context = [
        HumanMessage(content="限值是多少？"),
        _knowledge("ok", "测试指标的示例限值为 35 微克/立方米。", citation),
    ]
    text, _published = await _answer(
        ["模型改写的限值为 35 微克/立方米。", "以上是背景。"],
        context,
    )
    assert "模型改写" not in text
    assert "测试指标的示例限值为 35 微克/立方米。" in text
    assert f"[[cite:{citation}]]" in text
    assert "以上是背景。" in text


@pytest.mark.anyio
async def test_repeated_number_without_evidence_stays_hidden():
    context = [
        HumanMessage(content="限值是多少？"),
        _knowledge("no_match"),
        _web("别处提到 80。"),
    ]
    text, _published = await _answer(
        ["标准限值为 35。", "所以就是 35。", "共有 35 个站点。"],
        context,
    )
    assert "35。" not in text
    assert "所以就是 35" not in text
    assert "共有 35 个站点。" in text
    assert ABSTAIN_NO_MATCH in text


@pytest.mark.anyio
async def test_later_sentence_with_its_own_web_evidence_is_published():
    context = [
        HumanMessage(content="限值是多少？"),
        _knowledge("no_match"),
        _web("标准相关记载为 35。"),
    ]
    text, _published = await _answer(
        ["标准限值为 35。", "补充：标准限值为 35 [web:1]。"],
        context,
    )
    assert text.count("网页记载为 35") == 2
    assert "[web:1]" in text
    assert "标准限值为 35" not in text


@pytest.mark.anyio
async def test_unknown_citation_replaces_only_that_sentence():
    text, _published = await _answer(
        [
            "见 [[cite:00000000-0000-4000-8000-000000000099]]。",
            "后面的背景还在。",
        ],
        [HumanMessage(content="介绍一下")],
    )
    assert ABSTAIN_UNKNOWN_CITE in text
    assert "00000000" not in text
    assert "后面的背景还在。" in text


@pytest.mark.anyio
async def test_conflicting_knowledge_limit_is_not_replaced_by_web():
    citation = "11111111-1111-4111-8111-111111111111"
    context = [
        HumanMessage(content="GB 3095 的限值？"),
        _knowledge("ok", "GB 3095-2012 的限值为 35。", citation),
        _web("有文章写成 GB 3095-2012 限值为 75。"),
    ]
    text, _published = await _answer(["GB 3095-2012 的限值为 75。"], context)
    assert ABSTAIN_MISMATCH in text
    assert "75" not in text
    assert "网页记载" not in text


def test_failed_retrieval_uses_a_different_sentence_from_no_match():
    failed = AnswerReview.from_messages(
        [
            HumanMessage(content="限值"),
            _knowledge("timeout"),
        ]
    )
    missed = AnswerReview.from_messages(
        [
            HumanMessage(content="限值"),
            _knowledge("no_match"),
        ]
    )
    unit = "标准限值为 35。"
    assert failed.review_unit(unit) == ABSTAIN_FAILED
    assert missed.review_unit(unit) == ABSTAIN_NO_MATCH
    assert "审批" not in failed.review_unit("标准限值为 9。")


@pytest.mark.anyio
async def test_review_exception_abstains_the_regulation_sentence(monkeypatch):
    def _boom(self, unit: str) -> str:  # noqa: ANN001
        if "限值" in unit:
            raise RuntimeError("review failed")
        return unit

    monkeypatch.setattr(AnswerReview, "review_unit", _boom)
    text, _published = await _answer(
        ["标准限值为 35。", "背景还在。"],
        [HumanMessage(content="限值是多少？")],
    )
    assert ABSTAIN_FAILED in text
    assert "35" not in text
    assert "背景还在。" in text
