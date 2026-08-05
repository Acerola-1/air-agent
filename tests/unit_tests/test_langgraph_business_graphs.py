import json
from pathlib import Path


EXPECTED_GRAPHS = {
    "basic-qa": "./src/basic_qa/graph.py:graph",
    "intelligent-analysis": "./src/intelligent_analysis/graph.py:graph",
    "data-analysis": "./src/data_analysis/graph.py:graph",
    "intelligent-report": "./src/intelligent_report/graph.py:graph",
    "deep-research": "./src/deep_research/graph.py:graph",
    "intelligent-tracing": "./src/intelligent_tracing/graph.py:graph",
}


def test_langgraph_json_registers_business_graphs() -> None:
    config = json.loads(Path("langgraph.json").read_text(encoding="utf-8"))
    graphs = config["graphs"]

    assert graphs == EXPECTED_GRAPHS
    assert "data_analysis" not in graphs
    assert "agent" not in graphs


def test_docker_compose_langserve_graphs_match_langgraph_json() -> None:
    compose = Path("docker-compose.yaml").read_text(encoding="utf-8")
    expected_graphs_json = json.dumps(EXPECTED_GRAPHS, ensure_ascii=False)

    assert f"LANGSERVE_GRAPHS: '{expected_graphs_json}'" in compose
    assert "src/data_analysis_assistant/graph.py" not in compose
