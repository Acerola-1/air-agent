"""PermissionResult 模型测试."""

from __future__ import annotations

from common.permission.result import (
    AllowedRegion,
    PermissionResult,
    RegionPermissionResult,
)


def test_permission_result_round_trips_json() -> None:
    result = PermissionResult(
        permitted=False,
        exemption="",
        exemption_window=["2026-05-21", "2026-05-27"],
        fix_strategy="replace_region",
        allowed_region=AllowedRegion(
            name="杭州市",
            level="city",
            data_type="summary",
        ),
        accessible_station_count=12,
        total_station_count=15,
        unavailable_station_count=1,
        station_overrides=[
            {
                "station_name": "水利监测站",
                "station_type": "国控站",
                "region": {"name": "金水区", "level": "district"},
            }
        ],
        region_mode="multi_explicit",
        requested_regions=[{"name": "郑州市", "level": "city", "data_type": "summary"}],
        allowed_regions=[{"name": "杭州市", "level": "city", "data_type": "summary"}],
        corrected_regions=[{"name": "杭州市", "level": "city", "data_type": "summary"}],
        region_corrections=[
            {
                "type": "replace_region",
                "requested_region": {
                    "name": "郑州市",
                    "level": "city",
                    "data_type": "summary",
                },
                "allowed_region": {
                    "name": "杭州市",
                    "level": "city",
                    "data_type": "summary",
                },
            }
        ],
        region_results=[
            RegionPermissionResult(
                requested={
                    "text": "郑州市",
                    "source": "explicit",
                    "level_hint": "city",
                },
                resolved={"name": "郑州市", "level": "city", "data_type": "summary"},
                status="replaced",
                query_region={
                    "name": "杭州市",
                    "level": "city",
                    "data_type": "summary",
                },
                correction_text="已为您切换至有权限的杭州市数据。",
                permission_result={
                    "permitted": False,
                    "fix_strategy": "replace_region",
                },
            )
        ],
        reason="请求区域超出权限范围",
        correction_text="已为您切换至有权限的杭州市数据。",
        original_time_span=["2026-04-01", "2026-04-30"],
        time_granularity="month",
    )

    restored = PermissionResult.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.rule_version == "V2.3"
    assert restored.allowed_region is not None
    assert restored.allowed_region.data_type == "summary"
    assert restored.station_overrides[0].station_name == "水利监测站"
    assert restored.total_station_count == 15
    assert restored.region_results[0].status == "replaced"
    assert restored.region_results[0].requested["text"] == "郑州市"


def test_permission_result_defaults_are_stable() -> None:
    result = PermissionResult(permitted=True)

    assert result.exemption == ""
    assert result.fix_strategy == ""
    assert result.rule_version == "V2.3"
    assert result.allowed_region is None
    assert result.region_mode == ""
    assert result.requested_regions == []
    assert result.allowed_regions == []
    assert result.corrected_regions == []
    assert result.rejected_regions == []
    assert result.region_corrections == []
    assert result.region_results == []
    assert result.station_overrides == []
    assert result.total_station_count is None
    assert result.unavailable_station_count is None
