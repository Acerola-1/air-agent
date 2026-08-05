"""PermissionEngine 确定性规则引擎测试."""

from __future__ import annotations

import pytest

from common.permission.engine import (
    RULE_VERSION,
    check_permission,
    is_region_level_allowed,
)
from common.permission.facts import PermissionFacts

BEIJING_TIME = "2026-05-28 15:00:00"


@pytest.fixture
def province_user() -> dict:
    """广东省省级用户."""
    return {
        "found": True,
        "bound_region": {"name": "广东省", "level": "province"},
        "province": {"name": "广东省"},
        "city": None,
        "frequent_regions": [],
    }


@pytest.fixture
def city_user() -> dict:
    """宁波市地市用户."""
    return {
        "found": True,
        "bound_region": {"name": "宁波市", "level": "city"},
        "province": {"name": "浙江省"},
        "city": {"name": "宁波市"},
        "frequent_regions": [{"name": "鄞州区", "level": "district"}],
    }


@pytest.fixture
def district_user() -> dict:
    """杭州市西湖区区县用户."""
    return {
        "found": True,
        "bound_region": {"name": "西湖区", "level": "district"},
        "province": {"name": "浙江省"},
        "city": {"name": "杭州市"},
        "frequent_regions": [],
    }


def region_result(
    *,
    name: str,
    level: str,
    province_name: str,
    city_name: str | None = None,
) -> dict:
    """构造符合 resolve_region_scope MCP 合约的嵌套返回."""
    return {
        "found": True,
        "ambiguous": False,
        "region": {"name": name, "level": level},
        "province": {"name": province_name},
        "city": {"name": city_name} if city_name else None,
    }


@pytest.fixture
def zhejiang_province() -> dict:
    return region_result(
        name="浙江省",
        level="province",
        province_name="浙江省",
    )


@pytest.fixture
def guangzhou_city() -> dict:
    return region_result(
        name="广州市",
        level="city",
        province_name="广东省",
        city_name="广州市",
    )


@pytest.fixture
def tianhe_district() -> dict:
    return region_result(
        name="天河区",
        level="district",
        province_name="广东省",
        city_name="广州市",
    )


@pytest.fixture
def yinzhou_district() -> dict:
    return region_result(
        name="鄞州区",
        level="district",
        province_name="浙江省",
        city_name="宁波市",
    )


@pytest.fixture
def xihu_district() -> dict:
    return region_result(
        name="西湖区",
        level="district",
        province_name="浙江省",
        city_name="杭州市",
    )


@pytest.fixture
def shandong_city() -> dict:
    return region_result(
        name="淄博市",
        level="city",
        province_name="山东省",
        city_name="淄博市",
    )


@pytest.fixture
def boshan_district() -> dict:
    return region_result(
        name="博山区",
        level="district",
        province_name="山东省",
        city_name="淄博市",
    )


class TestIsRegionLevelAllowed:
    """层级白名单基础测试."""

    def test_province_can_access_province_and_city(self) -> None:
        assert is_region_level_allowed("province", "province") is True
        assert is_region_level_allowed("province", "city") is True
        assert is_region_level_allowed("province", "district") is False

    def test_city_can_access_all_levels(self) -> None:
        assert is_region_level_allowed("city", "province") is True
        assert is_region_level_allowed("city", "city") is True
        assert is_region_level_allowed("city", "district") is True

    def test_district_can_access_city_and_district(self) -> None:
        assert is_region_level_allowed("district", "province") is False
        assert is_region_level_allowed("district", "city") is True
        assert is_region_level_allowed("district", "district") is True


class TestTimeExemption:
    """时间豁免测试."""

    def test_full_window_7_days(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="最近7天浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2026-05-22", "2026-05-28"],
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "full_window"
        assert result.fix_strategy == ""
        assert result.rule_version == RULE_VERSION

    def test_partial_truncate(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="近30天浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2026-04-28", "2026-05-28"],
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "truncate_time"
        assert result.legal_time_span == ["2026-05-22", "2026-05-28"]
        assert "部分超出豁免窗口" in result.correction_text
        assert "2026-05-22 至 2026-05-28" in result.correction_text

    def test_month_exemption(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="本月浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2026-05-01", "2026-05-28"],
            time_granularity="month",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "full_window"

    def test_year_exemption(self, district_user: dict, zhejiang_province: dict) -> None:
        facts = PermissionFacts(
            question="今年浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2026-01-01", "2026-05-28"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "full_window"

    def test_last_year_full_window_exemption(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        """去年全年（year 粒度）在近两年豁免窗口内，应完全豁免.

        回归测试：修复前 is_scope_excluded 用 span_start < one_year_ago
        判断，导致"去年全年"被错误排除，近两年豁免失效。
        保定市用户查广东省去年PM10就是此场景。
        """
        facts = PermissionFacts(
            question="去年浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2025-01-01", "2025-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "full_window"
        assert result.fix_strategy == ""

    def test_city_user_last_year_foreign_province_full_window(
        self,
    ) -> None:
        """地市用户查外省去年全年数据，year 粒度在近两年窗口内，应完全豁免.

        这正是"保定市用户问广东省去年PM10"的场景。
        """
        baoding_user = {
            "found": True,
            "bound_region": {"name": "保定市", "level": "city"},
            "province": {"name": "河北省"},
            "city": {"name": "保定市"},
            "frequent_regions": [],
        }
        guangdong = region_result(
            name="广东省",
            level="province",
            province_name="广东省",
        )
        facts = PermissionFacts(
            question="去年广东省PM10平均浓度",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=baoding_user,
            requested_region=guangdong,
            original_time_span=["2025-01-01", "2025-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "full_window"
        assert result.fix_strategy == ""


class TestRegionPermission:
    """基础行政区权限测试."""

    def test_province_user_same_province_city_allowed(
        self, province_user: dict, guangzhou_city: dict
    ) -> None:
        facts = PermissionFacts(
            question="广州市PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=province_user,
            requested_region=guangzhou_city,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "normal"

    def test_province_user_same_province_district_replaced_to_city(
        self, province_user: dict, tianhe_district: dict
    ) -> None:
        facts = PermissionFacts(
            question="天河区2024年PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=province_user,
            requested_region=tianhe_district,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"
        assert result.allowed_region is not None
        assert result.allowed_region.level == "city"
        assert result.allowed_region.name == "广州市"
        assert "月/年统计数据不支持跨行政区查看" in result.correction_text

    def test_province_user_same_province_district_daily_uses_matrix_text(
        self, province_user: dict, tianhe_district: dict
    ) -> None:
        facts = PermissionFacts(
            question="天河区PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=province_user,
            requested_region=tianhe_district,
            original_time_span=["2024-01-01", "2024-01-02"],
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.fix_strategy == "replace_region"
        assert "省级用户无法查看区县明细数据" in result.correction_text
        assert "天河区所属广州市" in result.correction_text

    def test_city_user_same_city_district_allowed(
        self, city_user: dict, yinzhou_district: dict
    ) -> None:
        facts = PermissionFacts(
            question="鄞州区PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            requested_region=yinzhou_district,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "normal"

    def test_city_user_foreign_city_replaced(
        self, city_user: dict, shandong_city: dict
    ) -> None:
        facts = PermissionFacts(
            question="淄博市2024年PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            requested_region=shandong_city,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"
        assert result.allowed_region is not None
        assert result.allowed_region.name == "宁波市"
        assert "月/年统计数据不支持跨行政区查看" in result.correction_text

    def test_city_user_foreign_city_daily_uses_matrix_text(
        self, city_user: dict, shandong_city: dict
    ) -> None:
        facts = PermissionFacts(
            question="淄博市PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            requested_region=shandong_city,
            original_time_span=["2024-01-01", "2024-01-02"],
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.fix_strategy == "replace_region"
        assert "无法查看外省数据" in result.correction_text
        assert "宁波市" in result.correction_text

    def test_city_user_foreign_district_replaced(
        self, city_user: dict, boshan_district: dict
    ) -> None:
        facts = PermissionFacts(
            question="博山区2024年PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            requested_region=boshan_district,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"
        assert result.allowed_region is not None
        assert result.allowed_region.name == "宁波市"

    def test_district_user_same_city_other_district_allowed(
        self, district_user: dict
    ) -> None:
        gongshu = region_result(
            name="拱墅区",
            level="district",
            province_name="浙江省",
            city_name="杭州市",
        )
        facts = PermissionFacts(
            question="拱墅区PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=gongshu,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.exemption == "normal"

    def test_district_user_province_replaced(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="浙江省2024年PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"
        assert result.allowed_region is not None
        assert result.allowed_region.level == "district"
        assert result.allowed_region.name == "西湖区"
        assert "月/年统计数据不支持跨行政区查看" in result.correction_text

    def test_district_user_province_daily_uses_matrix_text(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            original_time_span=["2024-01-01", "2024-01-02"],
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.fix_strategy == "replace_region"
        assert "区县用户无法查看省级汇总数据" in result.correction_text
        assert "最近7天" in result.correction_text

    def test_district_user_foreign_district_replaced(
        self, district_user: dict, boshan_district: dict
    ) -> None:
        facts = PermissionFacts(
            question="博山区2024年PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=boshan_district,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"
        assert result.allowed_region is not None
        assert result.allowed_region.name == "西湖区"


def station_candidate(
    *,
    name: str,
    region_name: str = "鄞州区",
    level: str = "district",
    city_name: str = "宁波市",
    province_name: str = "浙江省",
) -> dict:
    return {
        "station_name": name,
        "station_type": "国控站",
        "region": {
            "name": region_name,
            "level": level,
            "city": city_name,
            "province": province_name,
        },
        "source_region": {"name": city_name, "level": "city"},
    }


class TestStationPermission:
    """站点继承所属行政区权限测试."""

    def test_station_full_window_keeps_candidates(self, district_user: dict) -> None:
        facts = PermissionFacts(
            question="最近7天山东站点",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            data_scope="station",
            station_candidates=[
                station_candidate(
                    name="山东站点",
                    region_name="博山区",
                    city_name="淄博市",
                    province_name="山东省",
                )
            ],
            original_time_span=["2026-05-22", "2026-05-28"],
            time_granularity="daily",
        )

        result = check_permission(facts)

        assert result.permitted is True
        assert result.exemption == "full_window"
        assert result.station_overrides[0].station_name == "山东站点"

    def test_station_partial_time_truncation_keeps_station_overrides(
        self, district_user: dict
    ) -> None:
        facts = PermissionFacts(
            question="近30天山东站点",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            data_scope="station",
            station_candidates=[
                station_candidate(
                    name="山东站点",
                    region_name="博山区",
                    city_name="淄博市",
                    province_name="山东省",
                )
            ],
            original_time_span=["2026-04-28", "2026-05-28"],
            time_granularity="daily",
        )

        result = check_permission(facts)

        assert result.fix_strategy == "truncate_time"
        assert result.legal_time_span == ["2026-05-22", "2026-05-28"]
        assert result.station_overrides[0].station_name == "山东站点"
        assert "部分超出豁免窗口" in result.correction_text

    def test_station_all_accessible(self, city_user: dict) -> None:
        facts = PermissionFacts(
            question="宁波市国控站排名",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            data_scope="station",
            station_candidates=[station_candidate(name="鄞州站点")],
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )

        result = check_permission(facts)

        assert result.permitted is True
        assert result.exemption == "normal"
        assert result.accessible_station_count == 1

    def test_station_partial_accessible(self, city_user: dict) -> None:
        facts = PermissionFacts(
            question="多区域国控站排名",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            data_scope="station",
            station_candidates=[
                station_candidate(name="鄞州站点"),
                station_candidate(
                    name="山东站点",
                    region_name="博山区",
                    city_name="淄博市",
                    province_name="山东省",
                ),
            ],
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )

        result = check_permission(facts)

        assert result.permitted is False
        assert result.fix_strategy == "filter_stations"
        assert result.station_overrides[0].station_name == "鄞州站点"
        assert result.accessible_station_count == 1
        assert "仅展示您可访问的" in result.correction_text

    def test_station_missing_region_is_unavailable(self, city_user: dict) -> None:
        facts = PermissionFacts(
            question="未知站点空气质量",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=city_user,
            data_scope="station",
            unavailable_stations=[{"station_name": "未知站点"}],
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )

        result = check_permission(facts)

        assert result.permitted is False
        assert result.fix_strategy == "reject"
        assert result.correction_text == "该数据暂不可用。"


class TestDegradation:
    """降级与异常测试."""

    def test_user_profile_missing(self) -> None:
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile={},
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "reject"

    def test_requested_region_not_found(self, district_user: dict) -> None:
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region={"found": False, "ambiguous": False},
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "reject"

    def test_requested_region_ambiguous(self, district_user: dict) -> None:
        facts = PermissionFacts(
            question="西湖区PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region={"found": False, "ambiguous": True, "candidates": []},
            time_granularity="daily",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "reject"

    def test_no_time_span_enters_region_check(
        self, district_user: dict, zhejiang_province: dict
    ) -> None:
        facts = PermissionFacts(
            question="浙江省PM2.5",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            requested_region=zhejiang_province,
            time_granularity="other",
        )
        result = check_permission(facts)
        assert result.permitted is False
        assert result.fix_strategy == "replace_region"

    def test_no_requested_region_uses_default_region(self, district_user: dict) -> None:
        facts = PermissionFacts(
            question="昨天PM2.5是多少",
            user_id="test",
            beijing_time=BEIJING_TIME,
            user_profile=district_user,
            original_time_span=["2024-01-01", "2024-12-31"],
            time_granularity="year",
        )
        result = check_permission(facts)
        assert result.permitted is True
        assert result.fix_strategy == ""
        assert result.auto_filled is True
        assert result.allowed_region is not None
        assert result.allowed_region.name == "西湖区"
        assert "已自动补全查询区域" in result.correction_text
