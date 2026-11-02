"""真实 DeepSeek 联网搜索（仅在提供真实密钥时运行）.

默认测试环境把 DEEPSEEK_API_KEY 设为占位值；本用例只在它是真实密钥时执行，
避免把 401 当成产品失败，也不在普通回归里产生外部费用。
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from common.websearch import search_web

_KEY = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
_REAL_KEY = (
    bool(_KEY)
    and _KEY not in {"test"}
    and not _KEY.startswith(("controlled", "synthetic"))
)

pytestmark = pytest.mark.skipif(
    not _REAL_KEY, reason="需要真实 DEEPSEEK_API_KEY 才执行外部搜索"
)


def test_real_deepseek_search_returns_bounded_structured_results():
    started = time.perf_counter()
    results = asyncio.run(search_web("生态环境部 空气质量 政策 2026", count=5))
    elapsed = time.perf_counter() - started

    assert isinstance(results, list)
    assert len(results) <= 5
    assert elapsed < 25
    for item in results:
        assert item.url.startswith(("http://", "https://"))
        assert item.title or item.snippet or item.summary or item.content
        assert len(item.snippet) <= 500
        assert len(item.summary) <= 2000
        assert len(item.content) <= 20000
        if item.rerank_score is not None:
            assert 0 <= item.rerank_score <= 3
        if item.authority is not None:
            assert -1 <= item.authority <= 4
    # 真实可用时至少应有一条结果；空结果只能作为当日无索引的显式观察。
    assert results, "真实 DeepSeek 搜索返回 0 条结果"
