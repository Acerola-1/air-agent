"""联网来源角标过滤与文末来源列表."""

from __future__ import annotations

from basic_qa.websearch_citations import WebAnswerCitations

SOURCES = [
    {
        "n": 1,
        "title": "新规发布",
        "url": "https://example.com/a",
        "site": "生态环境部",
        "date": "2026-10-01",
    },
    {
        "n": 2,
        "title": "背景解读",
        "url": "https://example.com/b",
        "site": "新闻",
        "date": "",
    },
]


def test_known_numbers_are_kept_and_unknown_are_removed():
    citations = WebAnswerCitations(SOURCES)
    text = citations.render("政策提到新要求[web:1]，另一个不存在[web:9]。")
    assert text == "政策提到新要求[web:1]，另一个不存在。"
    assert citations.used_numbers == (1,)


def test_streaming_split_marker_is_held_until_complete():
    citations = WebAnswerCitations(SOURCES)
    assert citations.push("政策提到[web") == "政策提到"
    assert citations.push(":") == ""
    assert citations.push("2]") == "[web:2]"
    assert citations.flush() == ""
    assert citations.used_numbers == (2,)


def test_unknown_split_marker_is_dropped_after_completion():
    citations = WebAnswerCitations(SOURCES)
    assert citations.push("无效[web") == "无效"
    assert citations.push(":7]结束") == "结束"
    assert citations.used_numbers == ()


def test_references_only_list_used_sources():
    citations = WebAnswerCitations(SOURCES)
    citations.render("只引用第二条[web:2]。")
    references = citations.references()
    assert "**互联网来源**" in references
    assert "- **[2]** [背景解读](<https://example.com/b>) — 新闻" in references
    assert "新规发布" not in references


def test_no_citation_means_no_reference_section():
    citations = WebAnswerCitations(SOURCES)
    citations.render("没有联网内容。")
    assert citations.references() == ""


def test_knowledge_citation_is_untouched():
    citations = WebAnswerCitations(SOURCES)
    text = citations.render("依据标准[[cite:3f3d3f3d-0000-0000-0000-000000000000]][web:1]")
    assert "[[cite:3f3d3f3d-0000-0000-0000-000000000000]]" in text
    assert text.endswith("[web:1]")


def test_render_is_idempotent_and_does_not_duplicate_usage():
    citations = WebAnswerCitations(SOURCES)
    first = citations.render("引用[web:1]")
    second = citations.render(first)
    assert second == first
    assert citations.used_numbers == (1,)
    assert citations.references().count("新规发布") == 1


def test_link_title_brackets_are_escaped():
    citations = WebAnswerCitations(
        [
            {
                "n": 1,
                "title": "关于[新规]的通知",
                "url": "https://example.com/a(1)",
                "site": "",
                "date": "",
            }
        ]
    )
    citations.render("[web:1]")
    references = citations.references()
    assert "关于\\[新规\\]的通知" in references
    assert "(<https://example.com/a(1)>)" in references
