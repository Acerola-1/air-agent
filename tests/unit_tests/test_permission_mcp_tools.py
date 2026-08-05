"""权限 MCP 工具封装测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from common.permission.mcp_tools import (
    normalize_station_scope_result,
    resolve_station_scope,
)

pytestmark = pytest.mark.anyio


class _MockTool:
    def __init__(self, name: str, result: object) -> None:
        self.name = name
        self.ainvoke = AsyncMock(return_value=result)


def test_normalize_station_scope_result_splits_invalid_regions() -> None:
    result = normalize_station_scope_result(
        {
            "found": True,
            "stations": [
                {
                    "station_name": "水利监测站",
                    "station_type": "国控站",
                    "region": {
                        "name": "金水区",
                        "level": "district",
                        "city": "郑州市",
                        "province": "河南省",
                    },
                },
                {
                    "station_name": "缺归属站点",
                    "station_type": "国控站",
                    "region": {"name": "金水区", "level": "district"},
                },
            ],
        }
    )

    assert result["found"] is True
    assert result["stations"][0]["station_name"] == "水利监测站"
    assert result["unavailable_stations"][0]["station_name"] == "缺归属站点"


async def test_resolve_station_scope_invokes_business_tool() -> None:
    tool = _MockTool(
        "query_station_info",
        {
            "found": True,
            "stations": [
                {
                    "stationName": "水利监测站",
                    "stationType": "国控站",
                    "region": {
                        "name": "金水区",
                        "level": "district",
                        "city": "郑州市",
                        "province": "河南省",
                    },
                }
            ],
        },
    )

    with patch("common.mcp_client.get_business_mcp_tools", return_value=[tool]):
        result = await resolve_station_scope(
            region="郑州市",
            station_names=["水利监测站"],
            station_types=["国控站"],
        )

    tool.ainvoke.assert_awaited_once_with(
        {
            "region": "郑州市",
            "station_names": ["水利监测站"],
            "station_types": ["国控站"],
        }
    )
    assert result["stations"][0]["station_name"] == "水利监测站"
