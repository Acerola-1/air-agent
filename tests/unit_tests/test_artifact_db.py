"""专属数据库载荷表存储单元测试."""

from __future__ import annotations

import asyncio

from common.storage.artifact_db import (
    get_tool_artifact,
    init_artifact_storage_table,
    save_tool_artifact,
)


def test_artifact_db_lifecycle(tmp_path, monkeypatch):
    """测试载荷表初始化、写入与读取生命周期."""
    async def _run():
        test_db = tmp_path / "test_artifact.db"
        monkeypatch.setattr("common.storage.artifact_db._get_sqlite_path", lambda: str(test_db))
        monkeypatch.setattr("common.storage.artifact_db._is_pg_configured", lambda: False)
        monkeypatch.setattr("common.storage.artifact_db._initialized", False)

        # 1. 初始化表
        await init_artifact_storage_table()

        # 2. 写入超大文本
        test_payload = "AIR_QUALITY_DATA_CHUNK_" * 500
        art_id = await save_tool_artifact(
            tool_call_id="call_unit_test_001",
            tool_name="mcp_city_common_get_air_quality",
            raw_payload=test_payload,
            summary="测试摘要结论",
        )
        assert art_id.startswith("art_")

        # 3. 读取记录
        record = await get_tool_artifact(art_id)
        assert record is not None
        assert record["artifact_id"] == art_id
        assert record["tool_call_id"] == "call_unit_test_001"
        assert record["tool_name"] == "mcp_city_common_get_air_quality"
        assert record["char_length"] == len(test_payload)
        assert record["raw_payload"] == test_payload
        assert record["summary"] == "测试摘要结论"

        # 4. 读取不存在的记录
        non_existent = await get_tool_artifact("art_not_found_999")
        assert non_existent is None

    asyncio.run(_run())
