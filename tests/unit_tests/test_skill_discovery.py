"""渐进式 Skill 发现工具测试."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.skill_discovery import create_find_skill_tool
from common.skill_router import SkillRouteCandidate

pytestmark = pytest.mark.anyio


class StubRouter:
    """find_skill 测试用的轻量路由替身."""

    def __init__(
        self,
        candidates: list[SkillRouteCandidate],
        skill_files: dict[str, Path] | None = None,
    ) -> None:
        self.candidates = candidates
        self.questions: list[str] = []
        self.skill_files = skill_files or {}

    def match(self, question: str) -> list[SkillRouteCandidate]:
        self.questions.append(question)
        return self.candidates

    async def amatch(self, question: str) -> list[SkillRouteCandidate]:
        self.questions.append(question)
        return self.candidates

    def resolve_skill_file(self, name: str) -> Path | None:
        return self.skill_files.get(name)


async def _invoke_tool(tool, question: str):
    return await tool.ainvoke(
        {
            "type": "tool_call",
            "name": "find_skill",
            "id": "call-1",
            "tool_call_id": "call-1",
            "args": {"question": question},
        }
    )


async def test_find_skill_updates_state_on_match() -> None:
    router = StubRouter(
        [
            SkillRouteCandidate(
                name="air-quality-basic-query",
                description="查询空气质量",
                path="skills/air-quality-basic-query/SKILL.md",
                allowed_tools=(
                    "helper_get_latest_time",
                    "mcp_city_common_get_air_quality_realtime_stat",
                ),
                similarity_score=0.91,
            )
        ]
    )
    find_skill = create_find_skill_tool(router)  # type: ignore[arg-type]

    command = await _invoke_tool(find_skill, "今天洛阳市空气质量怎么样？")

    assert router.questions == ["今天洛阳市空气质量怎么样？"]
    assert command.update["skill_search_attempted"] is True
    assert command.update["skill_search_question"] == "今天洛阳市空气质量怎么样？"
    assert command.update["selected_skill"] == "air-quality-basic-query"
    assert command.update["selected_skill_allowed_tools"] == [
        "helper_get_latest_time",
        "mcp_city_common_get_air_quality_realtime_stat",
    ]
    assert command.update["selected_skill_score"] == 0.91


async def test_find_skill_returns_top_n_union_and_summaries() -> None:
    router = StubRouter(
        [
            SkillRouteCandidate(
                name="broadcast-hour",
                description="小时播报",
                path="/skills/broadcast-hour/SKILL.md",
                allowed_tools=("broadcastHour", "helper_get_latest_time"),
                similarity_score=0.72,
            ),
            SkillRouteCandidate(
                name="monitoring-data",
                description="监测数据",
                path="/skills/monitoring-data/SKILL.md",
                allowed_tools=("cityMonitoringData", "helper_get_latest_time"),
                similarity_score=0.61,
            ),
        ]
    )
    find_skill = create_find_skill_tool(router)  # type: ignore[arg-type]

    command = await _invoke_tool(find_skill, "小时播报：帮我看看今天的情况")

    # 多候选：allowed-tools 取并集（去重、保序），支持跨 skill 组合
    assert command.update["selected_skill"] == "broadcast-hour"
    assert command.update["selected_skill_allowed_tools"] == [
        "broadcastHour",
        "helper_get_latest_time",
        "cityMonitoringData",
    ]

    payload = json.loads(command.update["messages"][0].content)
    assert payload["candidate_count"] == 2
    assert [c["skill_name"] for c in payload["candidates"]] == [
        "broadcast-hour",
        "monitoring-data",
    ]
    assert (
        payload["candidates"][0]["references_path"]
        == "/skills/broadcast-hour/references/fast.md"
    )


async def test_find_skill_clears_state_on_no_match() -> None:
    find_skill = create_find_skill_tool(StubRouter([]))  # type: ignore[arg-type]

    command = await _invoke_tool(find_skill, "讲个笑话")

    assert command.update["skill_search_attempted"] is True
    assert command.update["skill_search_question"] == "讲个笑话"
    assert command.update["selected_skill"] is None
    assert command.update["selected_skill_allowed_tools"] == []
    assert command.update["selected_skill_score"] is None
