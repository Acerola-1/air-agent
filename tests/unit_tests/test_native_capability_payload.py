"""原生载荷冲突须拒绝，不能覆盖后继续消耗受理预留."""

from copy import deepcopy

import pytest
from langgraph_sdk import Auth

from common.identity.langgraph_auth import assert_capability_payload_matches

SNAPSHOT = {
    "id": "selection-1",
    "selection_digest": "digest-1",
    "skill_mode": "auto",
    "skill_id": None,
    "mcp_mode": "allowlist",
    "tool_ids": [],
    "catalog_version": "catalog-1",
    "material_version": "auto",
    "selection_source": "user_explicit",
}


@pytest.mark.parametrize("location", ["metadata", "kwargs", "config"])
@pytest.mark.parametrize(
    "declaration",
    [
        {"capability_mcp_mode": "auto"},
        {"capability_tool_ids": ["mcp:other:query"]},
        {"capability_tool_ids": "[]"},
        {"capability_skill_mode": "explicit"},
        {"capability_skill_id": "skill:basic-qa:other"},
        {"capability_material_version": "changed"},
        {"capability_selection_source": "automatic"},
    ],
)
def test_conflicting_declaration_rejected_at_every_payload_location(
    location, declaration
):
    payload = {"metadata": {"capability_mcp_mode": "allowlist"}}
    if location == "metadata":
        payload[location].update(declaration)
    elif location == "kwargs":
        payload[location] = {"config": {"configurable": declaration}}
    else:
        payload[location] = {"configurable": declaration}
    with pytest.raises(Auth.exceptions.HTTPException) as rejected:
        assert_capability_payload_matches(payload, SNAPSHOT)
    assert rejected.value.status_code == 409


def test_matching_and_omitted_fields_remain_compatible():
    assert_capability_payload_matches({}, SNAPSHOT)
    assert_capability_payload_matches(
        {"metadata": {"capability_mcp_mode": "allowlist", "capability_tool_ids": []}},
        SNAPSHOT,
    )
    selected = deepcopy(SNAPSHOT)
    selected["tool_ids"] = ["mcp:s:a", "mcp:s:b"]
    assert_capability_payload_matches(
        {"metadata": {"capability_tool_ids": ["mcp:s:b", "mcp:s:a"]}}, selected
    )
