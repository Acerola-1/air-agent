"""目录与受理层使用相同稳定依赖 ID；不访问数据库或服务."""

from common.capabilities import list_graph_skills


def test_required_tool_ids_agree_with_admission_without_database():
    from app.services.ledger import validate_skill_selection

    items = list_graph_skills("basic-qa")
    assert len(items) == 9
    for item in items:
        admitted = validate_skill_selection("basic-qa", item.id)
        assert admitted["required_tool_ids"] == item.required_tool_ids
        assert item.required_tool_ids
        assert all(tool.startswith("mcp:ipp:") for tool in item.required_tool_ids)
        assert all("chart_" not in tool for tool in item.required_tool_ids)
