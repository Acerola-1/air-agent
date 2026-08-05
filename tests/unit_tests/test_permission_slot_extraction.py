"""PermissionExtractedSlots 模型测试."""

from __future__ import annotations

from common.permission.slots import (
    PermissionExtractedSlots,
    PermissionRegionRequest,
    normalize_target_level,
    normalize_time_granularity,
)


class TestNormalizeTimeGranularity:
    def test_valid_values(self):
        assert normalize_time_granularity("hourly") == "hourly"
        assert normalize_time_granularity("daily") == "daily"
        assert normalize_time_granularity("week") == "week"
        assert normalize_time_granularity("month") == "month"
        assert normalize_time_granularity("year") == "year"
        assert normalize_time_granularity("other") == "other"

    def test_case_insensitive(self):
        assert normalize_time_granularity("HOURLY") == "hourly"
        assert normalize_time_granularity("Daily") == "daily"

    def test_aliases(self):
        assert normalize_time_granularity("hour") == "hourly"
        assert normalize_time_granularity("day") == "daily"
        assert normalize_time_granularity("weekly") == "week"
        assert normalize_time_granularity("monthly") == "month"
        assert normalize_time_granularity("yearly") == "year"

    def test_none_and_empty(self):
        assert normalize_time_granularity(None) == "other"
        assert normalize_time_granularity("") == "other"

    def test_invalid_fallback(self):
        assert normalize_time_granularity("invalid") == "other"
        assert normalize_time_granularity("abc123") == "other"


class TestNormalizeTargetLevel:
    def test_valid_values(self):
        assert normalize_target_level("province") == "province"
        assert normalize_target_level("city") == "city"
        assert normalize_target_level("district") == "district"

    def test_chinese_names(self):
        assert normalize_target_level("省") == "province"
        assert normalize_target_level("市") == "city"
        assert normalize_target_level("区县") == "district"
        assert normalize_target_level("区") == "district"

    def test_none_and_empty(self):
        assert normalize_target_level(None) is None
        assert normalize_target_level("") is None

    def test_invalid_fallback(self):
        assert normalize_target_level("invalid") is None


class TestPermissionExtractedSlots:
    def test_default_values(self):
        slots = PermissionExtractedSlots()
        assert slots.time_granularity == "other"
        assert slots.region_mode == "auto_fill"
        assert slots.regions == []
        assert slots.province is None
        assert slots.city is None
        assert slots.district is None
        assert slots.region_entities == []
        assert slots.region_collection_intent is None
        assert slots.target_level is None
        assert slots.original_time_span is None
        assert slots.metric_names == []
        assert slots.data_scope == "region"
        assert slots.station_names == []
        assert slots.station_types == []

    def test_valid_slots(self):
        slots = PermissionExtractedSlots(
            region_mode="multi_explicit",
            regions=[
                PermissionRegionRequest(text="杭州市", level_hint="city"),
                PermissionRegionRequest(text="宁波市", level_hint="city"),
            ],
            time_granularity="daily",
            original_time_span=["2026-05-01", "2026-05-07"],
            metric_names=["PM2.5"],
        )
        assert slots.region_mode == "multi_explicit"
        assert [region.text for region in slots.regions] == ["杭州市", "宁波市"]
        assert slots.regions[0].level_hint == "city"
        assert slots.time_granularity == "daily"
        assert slots.original_time_span == ["2026-05-01", "2026-05-07"]

    def test_collection_parent_region(self):
        slots = PermissionExtractedSlots(
            region_mode="collection",
            regions=[
                {
                    "text": "郑州市",
                    "level_hint": "city",
                    "source": "explicit",
                    "role": "collection_parent",
                    "child_level": "district",
                }
            ],
        )

        assert slots.region_mode == "collection"
        assert slots.regions[0].role == "collection_parent"
        assert slots.regions[0].child_level == "district"

    def test_mixed_level_regions_keep_independent_hints(self):
        slots = PermissionExtractedSlots(
            region_mode="multi_explicit",
            regions=[
                {"text": "郑州市", "level_hint": "city"},
                {"text": "金水区", "level_hint": "district", "city_hint": "郑州市"},
            ],
        )

        assert slots.regions[0].level_hint == "city"
        assert slots.regions[1].level_hint == "district"
        assert slots.regions[1].city_hint == "郑州市"

    def test_granularity_normalization(self):
        slots = PermissionExtractedSlots(time_granularity="HOURLY")
        assert slots.time_granularity == "hourly"

    def test_target_level_normalization(self):
        slots = PermissionExtractedSlots(target_level="市")
        assert slots.target_level == "city"

    def test_invalid_time_span_returns_none(self):
        slots = PermissionExtractedSlots(original_time_span=["2026-05-01"])
        assert slots.original_time_span is None

    def test_json_string_time_span(self):
        """LLM 返回 JSON 字符串形式的时间范围时应正确解析."""
        slots = PermissionExtractedSlots(
            original_time_span='["2026-05-01", "2026-05-07"]',
        )
        assert slots.original_time_span == ["2026-05-01", "2026-05-07"]

    def test_invalid_json_string_time_span(self):
        """无效的 JSON 字符串应返回 None."""
        slots = PermissionExtractedSlots(original_time_span="not-json")
        assert slots.original_time_span is None

    def test_json_string_not_list_time_span(self):
        """JSON 字符串解析后不是列表应返回 None."""
        slots = PermissionExtractedSlots(original_time_span='"2026-05-01"')
        assert slots.original_time_span is None

    def test_ignores_permission_result_fields(self):
        """slots 模型应忽略 PermissionResult 相关字段."""
        # Pydantic extra="ignore" 会忽略未知字段
        # 这里验证 slots 模型不包含判定字段
        slots = PermissionExtractedSlots()
        assert not hasattr(slots, "permitted")
        assert not hasattr(slots, "fix_strategy")
        assert not hasattr(slots, "exemption")

    def test_station_slots_are_cleaned_and_deduplicated(self):
        slots = PermissionExtractedSlots(
            data_scope="station",
            station_names=["水利监测站", "", "None", "水利监测站"],
            station_types=["国控站", "null", "省控站", "国控站"],
        )

        assert slots.data_scope == "station"
        assert slots.station_names == ["水利监测站"]
        assert slots.station_types == ["国控站", "省控站"]

    def test_station_slots_parse_json_string_lists(self):
        slots = PermissionExtractedSlots(
            data_scope="station",
            station_names='["水利监测站", "市监测站"]',
            station_types='["国控站"]',
        )

        assert slots.station_names == ["水利监测站", "市监测站"]
        assert slots.station_types == ["国控站"]
