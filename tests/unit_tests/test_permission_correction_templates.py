"""权限修正文案模板测试."""

from __future__ import annotations

from common.permission.correction_templates import (
    build_region_replacement_correction_text,
    build_time_truncation_correction_text,
)


def _context(
    *,
    user_level: str,
    user_name: str,
    requested_level: str,
    requested_name: str,
    same_province: bool,
    same_city: bool,
) -> dict:
    return {
        "user_region": {"name": user_name, "level": user_level},
        "requested_region": {"name": requested_name, "level": requested_level},
        "same_province": same_province,
        "same_city": same_city,
    }


def test_province_user_same_province_district_rollup_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="province",
            user_name="广东省",
            requested_level="district",
            requested_name="天河区",
            same_province=True,
            same_city=False,
        ),
        replacement={"name": "广州市", "level": "city", "data_type": "summary"},
    )

    assert result.template_id == "province_same_province_district_rollup"
    assert "省级用户无法查看区县明细数据" in result.text
    assert "天河区所属广州市" in result.text


def test_province_user_external_rollup_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="province",
            user_name="广东省",
            requested_level="city",
            requested_name="杭州市",
            same_province=False,
            same_city=False,
        ),
        replacement={"name": "浙江省", "level": "province", "data_type": "summary"},
    )

    assert result.template_id == "province_external_rollup"
    assert "省级用户无法查看外省地市/区县数据" in result.text
    assert "最近7天" in result.text


def test_city_user_same_province_other_city_district_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="city",
            user_name="宁波市",
            requested_level="district",
            requested_name="西湖区",
            same_province=True,
            same_city=False,
        ),
        replacement={"name": "鄞州区", "level": "district", "data_type": "detail"},
    )

    assert result.template_id == "city_same_province_other_city_district"
    assert "地市用户无法查看其他市区县数据" in result.text
    assert "鄞州区" in result.text


def test_city_user_external_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="city",
            user_name="宁波市",
            requested_level="city",
            requested_name="淄博市",
            same_province=False,
            same_city=False,
        ),
        replacement={"name": "宁波市", "level": "city", "data_type": "summary"},
    )

    assert result.template_id == "city_external_replacement"
    assert "无法查看外省数据" in result.text
    assert "宁波市" in result.text


def test_district_user_province_summary_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="district",
            user_name="西湖区",
            requested_level="province",
            requested_name="浙江省",
            same_province=True,
            same_city=False,
        ),
        replacement={"name": "西湖区", "level": "district", "data_type": "detail"},
    )

    assert result.template_id == "district_province_summary_replacement"
    assert "区县用户无法查看省级汇总数据" in result.text
    assert "最近7天" in result.text


def test_district_user_external_city_template() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="district",
            user_name="西湖区",
            requested_level="district",
            requested_name="博山区",
            same_province=False,
            same_city=False,
        ),
        replacement={"name": "西湖区", "level": "district", "data_type": "detail"},
    )

    assert result.template_id == "district_external_city_replacement"
    assert "无法查看外市数据" in result.text
    assert "西湖区" in result.text


def test_month_year_region_replacement_template_has_priority() -> None:
    result = build_region_replacement_correction_text(
        region_context=_context(
            user_level="district",
            user_name="西湖区",
            requested_level="province",
            requested_name="浙江省",
            same_province=True,
            same_city=False,
        ),
        replacement={"name": "西湖区", "level": "district", "data_type": "detail"},
        time_granularity="year_count",
    )

    assert result.template_id == "month_year_region_replacement"
    assert "月/年统计数据不支持跨行政区查看" in result.text


def test_time_truncation_template_includes_legal_span() -> None:
    text = build_time_truncation_correction_text(
        legal_span=["2026-05-22", "2026-05-28"],
        requested_region={"name": "浙江省", "level": "province"},
        metric_names=["PM2.5"],
    )

    assert "部分超出豁免窗口" in text
    assert "2026-05-22 至 2026-05-28" in text
    assert "浙江省PM2.5" in text
