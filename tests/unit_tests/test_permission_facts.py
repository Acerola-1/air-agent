"""PermissionFacts 模型测试."""

from __future__ import annotations

from common.permission.facts import PermissionFacts, build_permission_facts


class TestPermissionFacts:
    def test_default_granularity(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
        )
        assert facts.time_granularity == "other"

    def test_normalize_granularity(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            time_granularity="HOURLY",
        )
        assert facts.time_granularity == "hourly"

    def test_invalid_granularity_fallback(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            time_granularity="invalid_value",
        )
        assert facts.time_granularity == "other"

    def test_extra_fields_ignored(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            permitted=True,  # 不应影响
            fix_strategy="reject",  # 不应影响
        )
        assert facts.question == "测试"

    def test_build_permission_facts(self):
        facts = build_permission_facts(
            question="测试问题",
            user_id="user123",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True, "bound_region": {"code": "330100"}},
            requested_region={"found": True, "name": "杭州市"},
            slots={
                "city": "杭州市",
                "target_level": "city",
                "time_granularity": "daily",
            },
        )
        assert facts.question == "测试问题"
        assert facts.user_id == "user123"
        assert facts.time_granularity == "daily"

    def test_build_permission_facts_with_region_slots_no_region_result(self):
        facts = build_permission_facts(
            question="测试问题",
            user_id="user123",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            slots={"city": "杭州市", "target_level": "city"},
        )
        assert facts.requested_region is not None
        assert facts.requested_region["found"] is False
        assert facts.requested_region["query"] == "杭州市"

    def test_build_permission_facts_with_station_candidates(self):
        facts = build_permission_facts(
            question="郑州市国控站排名",
            user_id="user123",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            slots={
                "data_scope": "station",
                "station_names": ["水利监测站", "水利监测站"],
                "station_types": ["国控站"],
            },
            station_candidates=[
                {
                    "station_name": "水利监测站",
                    "station_type": "国控站",
                    "region": {
                        "name": "金水区",
                        "level": "district",
                        "city": "郑州市",
                        "province": "河南省",
                    },
                    "source_region": {"name": "郑州市", "level": "city"},
                }
            ],
            unavailable_stations=[
                {"station_name": "未知站点", "station_type": "国控站"}
            ],
            station_region_mode="single",
        )

        assert facts.data_scope == "station"
        assert facts.station_names == ["水利监测站"]
        assert facts.station_types == ["国控站"]
        assert facts.station_candidates[0].region is not None
        assert facts.unavailable_stations[0].station_name == "未知站点"
        assert facts.station_region_mode == "single"


class TestPermissionFactsValidation:
    def test_time_span_two_elements(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            original_time_span=["2026-05-01", "2026-05-07"],
        )
        assert facts.original_time_span == ["2026-05-01", "2026-05-07"]

    def test_invalid_time_span_becomes_none(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            original_time_span=["2026-05-01"],
        )
        assert facts.original_time_span is None

    def test_time_span_none(self):
        facts = PermissionFacts(
            question="测试",
            user_id="test",
            beijing_time="2026-05-28 15:00:00",
            user_profile={"found": True},
            original_time_span=None,
        )
        assert facts.original_time_span is None
