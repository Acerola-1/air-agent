"""确定性权限行政区 helper 测试."""

from __future__ import annotations

from datetime import datetime

import pytest

from common.permission.region import (
    build_permission_region_context,
    calculate_exemption_window,
    calculate_time_intersection,
    default_allowed_region,
    get_replacement_region,
    is_region_level_allowed,
    is_scope_excluded,
    truncate_time_span,
)


@pytest.mark.parametrize(
    ("user_level", "requested_level", "allowed"),
    [
        ("province", "province", True),
        ("province", "district", False),
        ("city", "province", True),
        ("city", "district", True),
        ("district", "province", False),
        ("district", "city", True),
    ],
)
def test_region_level_whitelist(
    user_level: str,
    requested_level: str,
    allowed: bool,
) -> None:
    assert is_region_level_allowed(user_level, requested_level) is allowed


def test_default_allowed_region_uses_user_association() -> None:
    assert default_allowed_region(
        {
            "region_level": "district",
            "region_name": "西湖区",
        }
    ) == {
        "name": "西湖区",
        "level": "district",
        "data_type": "detail",
    }


def test_builds_permission_region_context_with_replacement() -> None:
    context = build_permission_region_context(
        {
            "region_level": "province",
            "region_name": "广东省",
            "province": {"name": "广东省"},
        },
        {
            "name": "天河区",
            "level": "district",
            "province_name": "广东省",
            "city_name": "广州市",
        },
    )

    assert context["level_allowed"] is False
    assert context["same_province"] is True
    assert context["replacement_region"] == {
        "name": "广州市",
        "level": "city",
        "data_type": "summary",
    }


def _apply_time_permission(
    user_profile: dict,
    requested_region: dict,
    time_permission: str,
) -> dict | None:
    if time_permission in {"full_window", "truncate_time"}:
        return None
    return get_replacement_region(user_profile, requested_region)


@pytest.mark.parametrize(
    ("case_id", "user_profile", "requested_region", "time_permission", "expected"),
    [
        (
            "case1",
            {
                "region_level": "province",
                "region_name": "广东省",
                "province": {"name": "广东省"},
            },
            {
                "name": "天河区",
                "level": "district",
                "province_name": "广东省",
                "city_name": "广州市",
            },
            "none",
            {"name": "广州市", "level": "city", "data_type": "summary"},
        ),
        (
            "case2",
            {
                "region_level": "province",
                "region_name": "广东省",
                "province": {"name": "广东省"},
            },
            {
                "name": "玄武区",
                "level": "district",
                "province_name": "江苏省",
                "city_name": "南京市",
            },
            "full_window",
            None,
        ),
        (
            "case3",
            {
                "region_level": "city",
                "region_name": "宁波市",
                "province": {"name": "浙江省"},
                "city": {"name": "宁波市"},
                "frequent_regions": [{"name": "鄞州区", "level": "district"}],
            },
            {
                "name": "西湖区",
                "level": "district",
                "province_name": "浙江省",
                "city_name": "杭州市",
            },
            "none",
            {"name": "鄞州区", "level": "district", "data_type": "detail"},
        ),
        (
            "case4",
            {
                "region_level": "city",
                "region_name": "宁波市",
                "province": {"name": "浙江省"},
                "city": {"name": "宁波市"},
            },
            {"name": "全国", "level": "province"},
            "full_window",
            None,
        ),
        (
            "case5",
            {
                "region_level": "district",
                "region_name": "西湖区",
                "province": {"name": "浙江省"},
                "city": {"name": "杭州市"},
            },
            {"name": "浙江省", "level": "province", "province_name": "浙江省"},
            "truncate_time",
            None,
        ),
        (
            "case6",
            {
                "region_level": "city",
                "region_name": "宁波市",
                "province": {"name": "浙江省"},
                "city": {"name": "宁波市"},
            },
            {
                "name": "杭州市",
                "level": "city",
                "province_name": "浙江省",
                "city_name": "杭州市",
            },
            "full_window",
            None,
        ),
        (
            "case7",
            {
                "region_level": "city",
                "region_name": "宁波市",
                "province": {"name": "浙江省"},
                "city": {"name": "宁波市"},
            },
            {
                "name": "西湖区",
                "level": "district",
                "province_name": "浙江省",
                "city_name": "杭州市",
            },
            "none",
            {"name": "宁波市", "level": "city", "data_type": "summary"},
        ),
        (
            "case8",
            {
                "region_level": "district",
                "region_name": "西湖区",
                "province": {"name": "浙江省"},
                "city": {"name": "杭州市"},
            },
            {"name": "全国", "level": "province"},
            "full_window",
            None,
        ),
        (
            "case9",
            {
                "region_level": "district",
                "region_name": "西湖区",
                "province": {"name": "浙江省"},
                "city": {"name": "杭州市"},
            },
            {"name": "全国", "level": "province"},
            "none",
            {"name": "西湖区", "level": "district", "data_type": "detail"},
        ),
    ],
)
def test_design_document_region_outcomes(
    case_id: str,
    user_profile: dict,
    requested_region: dict,
    time_permission: str,
    expected: dict | None,
) -> None:
    replacement = _apply_time_permission(
        user_profile,
        requested_region,
        time_permission,
    )

    if expected is None:
        assert replacement is None, case_id
        return

    assert replacement is not None, case_id
    for key, value in expected.items():
        assert replacement[key] == value, case_id


# ── 时间豁免窗口确定性计算测试 ──────────────────────────────

BEIJING_TIME = datetime(2026, 5, 28, 15, 0, 0)


class TestCalculateExemptionWindow:
    def test_hourly_window(self) -> None:
        window = calculate_exemption_window("hourly", BEIJING_TIME)
        assert window[0] == "2026-05-22 00:00:00"
        assert window[1] == "2026-05-28 23:59:59"

    def test_daily_count_window(self) -> None:
        window = calculate_exemption_window("daily_count", BEIJING_TIME)
        assert window[0] == "2026-05-22 00:00:00"
        assert window[1] == "2026-05-28 23:59:59"

    def test_daily_window(self) -> None:
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert window[0] == "2026-05-22"
        assert window[1] == "2026-05-28"

    def test_week_window(self) -> None:
        window = calculate_exemption_window("week", BEIJING_TIME)
        assert window[0] == "2026-05-22"
        assert window[1] == "2026-05-28"

    def test_month_window(self) -> None:
        window = calculate_exemption_window("month", BEIJING_TIME)
        assert window[0] == "2026-04-01"
        assert window[1].startswith("2026-05-")

    def test_month_count_window(self) -> None:
        window = calculate_exemption_window("month_count", BEIJING_TIME)
        assert window[0] == "2026-04-01"

    def test_year_window(self) -> None:
        window = calculate_exemption_window("year", BEIJING_TIME)
        assert window[0] == "2025-01-01"
        assert window[1] == "2026-12-31"

    def test_year_count_window(self) -> None:
        window = calculate_exemption_window("year_count", BEIJING_TIME)
        assert window[0] == "2025-01-01"
        assert window[1] == "2026-12-31"

    def test_unknown_granularity_fallback(self) -> None:
        window = calculate_exemption_window("unknown", BEIJING_TIME)
        assert window[0] == "2026-05-22"
        assert window[1] == "2026-05-28"


class TestCalculateTimeIntersection:
    def test_full_window_single_point(self) -> None:
        span = ["2026-05-25", "2026-05-25"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert calculate_time_intersection(span, window, "daily") == "full_window"

    def test_full_window_range(self) -> None:
        span = ["2026-05-22", "2026-05-28"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert calculate_time_intersection(span, window, "daily") == "full_window"

    def test_partial_intersection(self) -> None:
        span = ["2026-05-18", "2026-05-25"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert calculate_time_intersection(span, window, "daily") == "partial"

    def test_no_intersection(self) -> None:
        span = ["2026-04-01", "2026-04-15"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert calculate_time_intersection(span, window, "daily") == "none"

    def test_empty_span(self) -> None:
        window = calculate_exemption_window("daily", BEIJING_TIME)
        assert calculate_time_intersection([], window, "daily") == "none"

    def test_month_full_window(self) -> None:
        span = ["2026-05-01", "2026-05-28"]
        window = calculate_exemption_window("month", BEIJING_TIME)
        assert calculate_time_intersection(span, window, "month") == "full_window"


class TestTruncateTimeSpan:
    def test_truncate_partial(self) -> None:
        original = ["2026-05-18", "2026-05-25"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        result = truncate_time_span(original, window)
        assert result[0] == "2026-05-22"
        assert result[1] == "2026-05-25"

    def test_no_intersection_returns_original(self) -> None:
        original = ["2026-04-01", "2026-04-15"]
        window = calculate_exemption_window("daily", BEIJING_TIME)
        result = truncate_time_span(original, window)
        assert result == original

    def test_hourly_truncate(self) -> None:
        original = ["2026-05-18 00:00:00", "2026-05-25 23:59:59"]
        window = calculate_exemption_window("hourly", BEIJING_TIME)
        result = truncate_time_span(original, window)
        assert result[0] == "2026-05-22 00:00:00"
        assert result[1] == "2026-05-25 23:59:59"


class TestIsScopeExcluded:
    def test_historical_query_over_one_year(self) -> None:
        span = ["2025-01-01", "2026-05-28"]
        assert is_scope_excluded(span, "daily", BEIJING_TIME) is True

    def test_recent_query_not_excluded(self) -> None:
        span = ["2026-05-22", "2026-05-28"]
        assert is_scope_excluded(span, "daily", BEIJING_TIME) is False

    def test_hourly_over_8760_points(self) -> None:
        span = ["2025-01-01", "2026-05-28"]
        assert is_scope_excluded(span, "hourly", BEIJING_TIME) is True

    def test_empty_span_not_excluded(self) -> None:
        assert is_scope_excluded(None, "daily", BEIJING_TIME) is False

    def test_last_year_full_year_not_excluded(self) -> None:
        """去年全年（year 粒度）跨度恰好 365 天，不应被排除.

        回归测试：修复前 is_scope_excluded 用 span_start < one_year_ago
        判断，导致"去年全年"被错误排除，近两年豁免失效。
        """
        # 2025年全年：365天（非闰年），跨度恰好 365 天，不超过 365
        span = ["2025-01-01", "2025-12-31"]
        assert is_scope_excluded(span, "year", BEIJING_TIME) is False

    def test_last_year_leap_not_excluded(self) -> None:
        """闰年全年 366 天，跨度超过 365，应被排除."""
        # 2024年是闰年，1月1日到12月31日 = 366天 > 365
        span = ["2024-01-01", "2024-12-31"]
        assert is_scope_excluded(span, "year", BEIJING_TIME) is True

    def test_exactly_365_days_not_excluded(self) -> None:
        """恰好 365 天跨度，不超限."""
        span = ["2025-06-01", "2026-05-31"]
        assert is_scope_excluded(span, "daily", BEIJING_TIME) is False
