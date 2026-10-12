import json
from pathlib import Path

# langgraph.json 的 graphs 路径直接指向各图模块, 无中间包装层
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


def test_container_graph_and_http_registration_match_langgraph_json() -> None:
    compose = Path("docker-compose.yaml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    expected_graphs_json = json.dumps(
        {
            name: path.replace("./", "/deps/air_agent/", 1)
            for name, path in EXPECTED_GRAPHS.items()
        },
        ensure_ascii=False,
    )

    assert f"ENV LANGSERVE_GRAPHS='{expected_graphs_json}'" in dockerfile
    configuration = json.loads(Path("langgraph.json").read_text(encoding="utf-8"))
    assert configuration["http"]["app"] == "./src/common/artifact_http.py:app"
    assert "/deps/air_agent/src/common/artifact_http.py:app" in compose
    assert "/deps/air_agent/src/common/artifact_http.py:app" in dockerfile
    assert "src/data_analysis_assistant/graph.py" not in compose
