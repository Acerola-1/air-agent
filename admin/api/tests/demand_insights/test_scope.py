"""窗口、游标与 meta 契约（纯函数）."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.demand_insights.config import get_insights_settings
from app.demand_insights.scope import (
    InsightsBadCursor,
    InsightsWindowError,
    build_meta,
    cursor_window,
    decode_cursor,
    encode_cursor,
    filter_fingerprint,
    parse_window,
    previous_window,
    window_key,
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-insights-cursor-secret")
    get_insights_settings.cache_clear()
    yield
    get_insights_settings.cache_clear()


def _as_of() -> dt.datetime:
    # 游标校验以真实 now 为基准；测试用一小时前，避免固定时间在未来时被拒绝。
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)


def test_window_presets_and_custom() -> None:
    today = parse_window(window="today", start=None, end=None, as_of=_as_of())
    assert today.kind == "today" and today.partial_day is True
    week = parse_window(window="last_7_days", start=None, end=None, as_of=_as_of())
    assert week.start < week.end
    month = parse_window(window="last_30_days", start=None, end=None, as_of=_as_of())
    assert month.start < week.start
    custom = parse_window(
        window="custom",
        start="2026-10-01T00:00:00+08:00",
        end="2026-10-08T00:00:00+08:00",
        as_of=_as_of(),
    )
    assert custom.kind == "custom"


def test_window_rejects_invalid() -> None:
    with pytest.raises(InsightsWindowError):
        parse_window(window="yesterday", start=None, end=None, as_of=_as_of())
    with pytest.raises(InsightsWindowError):
        parse_window(window="custom", start=None, end=None, as_of=_as_of())
    with pytest.raises(InsightsWindowError):
        parse_window(
            window="custom",
            start="2026-10-08T00:00:00+08:00",
            end="2026-10-01T00:00:00+08:00",
            as_of=_as_of(),
        )


def test_previous_window_offsets() -> None:
    today = parse_window(window="today", start=None, end=None, as_of=_as_of())
    previous = previous_window(today)
    assert previous is not None
    assert previous.start == today.start - dt.timedelta(days=1)
    assert previous.end == today.end - dt.timedelta(days=1)
    week = parse_window(window="last_7_days", start=None, end=None, as_of=_as_of())
    previous_week = previous_window(week)
    assert previous_week is not None
    assert previous_week.end == week.end - dt.timedelta(days=7)
    custom = parse_window(
        window="custom",
        start="2026-10-01T00:00:00+08:00",
        end="2026-10-08T00:00:00+08:00",
        as_of=_as_of(),
    )
    assert previous_window(custom) is None


def test_cursor_roundtrip_and_binding() -> None:
    tenant = str(uuid.uuid4())
    window = parse_window(window="last_7_days", start=None, end=None, as_of=_as_of())
    filter_key = filter_fingerprint({"rank": "intents"}, window_key=window_key(None, None, None))
    token = encode_cursor(
        tenant_id=tenant,
        context_version=3,
        filter_key=filter_key,
        rank="intents",
        analysis_version="qdi-v1",
        assignment_revision=7,
        window=window,
        key="12|cluster-a",
    )
    decoded = decode_cursor(
        token,
        tenant_id=tenant,
        context_version=3,
        filter_key=filter_key,
        rank="intents",
        analysis_version="qdi-v1",
        assignment_revision=7,
    )
    assert decoded.key == "12|cluster-a"
    assert cursor_window(decoded, "last_7_days").as_of == window.as_of
    with pytest.raises(InsightsBadCursor):
        decode_cursor(
            token,
            tenant_id=tenant,
            context_version=3,
            filter_key="other",
            rank="intents",
            analysis_version="qdi-v1",
            assignment_revision=7,
        )
    with pytest.raises(InsightsBadCursor):
        decode_cursor(
            token,
            tenant_id=str(uuid.uuid4()),
            context_version=3,
            filter_key=filter_key,
            rank="intents",
            analysis_version="qdi-v1",
            assignment_revision=7,
        )


def test_build_meta_exposes_required_fields() -> None:
    class Identity:
        tenant = type("Tenant", (), {"id": uuid.uuid4()})()
        context_version = 2

    window = parse_window(window="today", start=None, end=None, as_of=_as_of())
    meta = build_meta(
        identity=Identity(),
        window=window,
        filters={"rank": "intents"},
        analysis_version="qdi-v1",
        assignment_revision=4,
        embedding_profile="embed-v1",
        profile_id=uuid.uuid4(),
        profile_state_revision=2,
        source_cutoff=_as_of(),
        counts={
            "eligible_requests": 10,
            "processed_requests": 8,
            "classified_intents": 9,
            "unclassified_intents": 1,
            "coverage": 0.8,
        },
        comparable=False,
        limitations=["demo"],
    )
    assert meta["schema_version"] == "demand-insights-v1"
    assert meta["analysis_version"] == "qdi-v1"
    assert meta["assignment_revision"] == 4
    assert meta["coverage"] == 0.8
    assert meta["comparable"] is False
    assert meta["window"]["timezone"] == "Asia/Shanghai"
