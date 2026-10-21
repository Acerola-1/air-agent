"""运营后台窗口/游标/正文分页的纯函数契约测试（无数据库）."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.console.scope import (
    ConsoleError,
    content_page,
    cursor_window,
    decode_cursor,
    encode_cursor,
    filter_fingerprint,
    parse_limit,
    parse_window,
    window_key,
)


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.UTC)


def test_today_window_uses_shanghai_business_day() -> None:
    as_of = _utc(2026, 10, 20, 3, 30)  # 11:30 Asia/Shanghai
    window = parse_window(window="today", start=None, end=None, as_of=as_of)
    assert window.start == _utc(2026, 10, 19, 16, 0)
    assert window.end == as_of
    assert window.partial_day is True
    assert window.timezone == "Asia/Shanghai"


def test_last_seven_days_is_inclusive_of_today() -> None:
    as_of = _utc(2026, 10, 20, 3, 30)
    window = parse_window(window=None, start=None, end=None, as_of=as_of)
    assert window.kind == "last_7_days"
    assert window.start == _utc(2026, 10, 13, 16, 0)
    assert window.end == as_of


def test_custom_window_requires_aware_bounds_and_limits() -> None:
    as_of = _utc(2026, 10, 20, 3, 30)
    with pytest.raises(ConsoleError):
        parse_window(window="custom", start="2026-10-01", end="2026-10-02", as_of=as_of)
    with pytest.raises(ConsoleError):
        parse_window(
            window="custom",
            start="2026-10-02T00:00:00+08:00",
            end="2026-10-01T00:00:00+08:00",
            as_of=as_of,
        )
    with pytest.raises(ConsoleError):
        parse_window(
            window="custom",
            start="2026-01-01T00:00:00+08:00",
            end="2026-10-01T00:00:00+08:00",
            as_of=as_of,
        )
    window = parse_window(
        window="custom",
        start="2026-10-01T00:00:00+08:00",
        end="2026-10-02T00:00:00+08:00",
        as_of=as_of,
    )
    assert window.start == _utc(2026, 9, 30, 16, 0)
    assert window.end == _utc(2026, 10, 1, 16, 0)


def test_preset_window_rejects_start_end() -> None:
    with pytest.raises(ConsoleError):
        parse_window(
            window="today",
            start="2026-10-01T00:00:00Z",
            end="2026-10-02T00:00:00Z",
            as_of=_utc(2026, 10, 20),
        )


def test_cursor_roundtrip_binds_tenant_context_filters_and_as_of() -> None:
    as_of = dt.datetime.now(dt.UTC)
    window = parse_window(window="today", start=None, end=None, as_of=as_of)
    key = filter_fingerprint({"state": "failed"}, window_key=window_key("today", None, None))
    cursor = encode_cursor(
        tenant_id="t1",
        context_version=7,
        filter_key=key,
        sort="admitted_at_desc",
        window=window,
        key="2026-10-20T03:00:00+00:00|abc",
    )
    decoded = decode_cursor(
        cursor,
        tenant_id="t1",
        context_version=7,
        filter_key=key,
        sort="admitted_at_desc",
    )
    assert decoded.as_of == as_of
    assert cursor_window(decoded, "today").start == window.start
    with pytest.raises(ConsoleError) as wrong_tenant:
        decode_cursor(
            cursor,
            tenant_id="t2",
            context_version=7,
            filter_key=key,
            sort="admitted_at_desc",
        )
    assert wrong_tenant.value.code == "bad_cursor"
    with pytest.raises(ConsoleError) as wrong_filter:
        decode_cursor(
            cursor,
            tenant_id="t1",
            context_version=7,
            filter_key="different",
            sort="admitted_at_desc",
        )
    assert wrong_filter.value.code == "cursor_filter_mismatch"
    with pytest.raises(ConsoleError) as wrong_context:
        decode_cursor(
            cursor,
            tenant_id="t1",
            context_version=8,
            filter_key=key,
            sort="admitted_at_desc",
        )
    assert wrong_context.value.code == "bad_cursor"


def test_cursor_rejects_tampering_other_user_and_expiry() -> None:
    import base64
    import json

    now = dt.datetime.now(dt.UTC)
    window = parse_window(window="today", start=None, end=None, as_of=now)
    args = dict(tenant_id="tenant", context_version=1, filter_key="filter", sort="sort")
    cursor = encode_cursor(**args, user_id="u1", window=window, key="key")
    assert decode_cursor(cursor, **args, user_id="u1").key == "key"
    with pytest.raises(ConsoleError):
        decode_cursor(cursor, **args, user_id="u2")
    body, signature = cursor.split(".")
    data = json.loads(base64.urlsafe_b64decode(body))
    data["ws"] = "2000-01-01T00:00:00+00:00"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).decode() + "." + signature
    with pytest.raises(ConsoleError):
        decode_cursor(forged, **args, user_id="u1")
    old = parse_window(window="today", start=None, end=None, as_of=now - dt.timedelta(days=2))
    with pytest.raises(ConsoleError):
        decode_cursor(
            encode_cursor(**args, user_id="u1", window=old, key="key"), **args, user_id="u1"
        )


def test_limit_and_content_page_bounds() -> None:
    with pytest.raises(ConsoleError):
        parse_limit(0)
    with pytest.raises(ConsoleError):
        parse_limit(201)
    assert parse_limit(None) == 50
    page = content_page(
        text_value="abcdef", offset=2, limit=3, status="available", content_version=1
    )
    assert page["text"] == "cde"
    assert page["next_offset"] == 5
    assert page["total_chars"] == 6
    assert (
        content_page(text_value=None, offset=0, limit=8000, status="not_collected")["text"] is None
    )
    with pytest.raises(ConsoleError):
        content_page(text_value="x", offset=0, limit=8001, status="available")
    with pytest.raises(ConsoleError):
        content_page(text_value="x", offset=-1, limit=1, status="available")
