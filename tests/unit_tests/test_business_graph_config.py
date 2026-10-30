from pathlib import Path

# 使用 create_deep_agent 的 DeepAgent 图
DEEP_AGENT_GRAPHS = {
    "intelligent-tracing": Path("src/intelligent_tracing/graph.py"),
}

# 使用 StateGraph 节点流的图
STATE_GRAPHS = {
    "data-analysis": Path("src/data_analysis/graph.py"),
    "basic-qa": Path("src/basic_qa/graph.py"),
}

# Basic QA 自有实现
BUSINESS_GRAPH_BUILDER = Path("src/basic_qa/builder.py")
BUSINESS_GRAPH_NODES = Path("src/basic_qa/nodes.py")
BUSINESS_GRAPH_PROMPTING = Path("src/basic_qa/prompts.py")
BUSINESS_GRAPH_TOOL_WRAPPERS = Path("src/basic_qa/tool_runtime.py")

BUSINESS_GRAPH_FILES = {**DEEP_AGENT_GRAPHS, **STATE_GRAPHS}


def name_marker(graph_name: str) -> str:
    """Return the structural graph-name marker used by each graph type."""
    if graph_name == "intelligent-tracing":
        return f'name: str = "{graph_name}"'
    return f'name="{graph_name}"'


def test_business_graph_registry_contains_public_graphs() -> None:
    assert set(BUSINESS_GRAPH_FILES) == {
        "basic-qa",
        "data-analysis",
        "intelligent-tracing",
    }


def test_business_graph_configs_have_graph_level_skills_and_prompts() -> None:
    # intelligent-tracing 已改为四专员溯源图：lead 不持有 MCP/文件/执行工具。
    tracing_graph = DEEP_AGENT_GRAPHS["intelligent-tracing"]
    source = tracing_graph.read_text(encoding="utf-8")
    assert "build_tracing_graph" in source
    assert "create_deep_agent" in source
    assert name_marker("intelligent-tracing") in source
    # langgraph-api 兼容: 编译时 checkpointer 必须为 None, 不能用 get_checkpointer()
    assert "checkpointer=None" in source
    assert "get_checkpointer" not in source
    assert "CodeInterpreterMiddleware" not in source
    assert "get_business_mcp_tools" not in source
    assert "build_create_artifact_tool" not in source
    assert "find_skill" not in source
    assert "TenantRuntimeGuardMiddleware(inject_mcp_tools=False)" in source
    assert "NoFilesystemToolsMiddleware" in source
    for role in ("station-data", "grid-data", "meteo-analyst", "evidence-reviewer"):
        assert role in source
    assert "mcp_station_analysis_trajectory" not in source
    assert "mcp_station_analysis_single_pscf" not in source
    assert "trajectory-analyst" not in source

    bg_builder = BUSINESS_GRAPH_BUILDER.read_text(encoding="utf-8")
    bg_nodes = BUSINESS_GRAPH_NODES.read_text(encoding="utf-8")
    bg_prompting = BUSINESS_GRAPH_PROMPTING.read_text(encoding="utf-8")
    assert "validate_input" in Path("src/basic_qa/input_policy.py").read_text()
    from basic_qa.graph import graph

    topology = graph({}).get_graph()
    assert "resolve_skill" not in topology.nodes
    assert {
        (edge.target, edge.conditional)
        for edge in topology.edges
        if edge.source == "prepare_model"
    } == {("call_model", False)}
    assert "_route_after_tools" in bg_builder
    assert "prepare_projection" in bg_nodes
    assert "with_main_agent_tool_use_output_guard" in bg_prompting
    assert "air-agent/basic-qa/conversation-system-v2" in bg_prompting

    # data-analysis 使用 StateGraph 节点流
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    da_prompt_builder = Path("src/data_analysis/prompts.py").read_text(encoding="utf-8")
    assert 'SKILLS_DIR = Path(__file__).parent / "skills"' in da_graph
    # skill 发现逻辑在 nodes.py 中
    assert "resolve_skill" in da_graph
    assert "match_menu_skill" in da_nodes
    assert "with_data_analysis_output_guard" in da_prompt_builder
    assert "with_main_agent_tool_use_output_guard" not in da_graph
    assert "increment_iteration" not in da_graph
    # data-analysis 使用 StateGraph 节点流，支持工具执行后回到 call_model 生成答案
    assert "route_after_tools" in da_graph


def test_business_graph_configs_keep_local_graph_names() -> None:
    named_graphs = {
        **DEEP_AGENT_GRAPHS,
        "basic-qa": STATE_GRAPHS["basic-qa"],
    }
    for graph_name, graph_file in named_graphs.items():
        source = graph_file.read_text(encoding="utf-8")
        assert name_marker(graph_name) in source


def test_basic_qa_uses_rich_output_pipeline() -> None:
    # basic-qa 迁移节点流后，富输出经共享 composed_tool_wrapper 处理
    source = STATE_GRAPHS["basic-qa"].read_text(encoding="utf-8")
    assert "LegacyChartDataMiddleware()" not in source

    wrappers = BUSINESS_GRAPH_TOOL_WRAPPERS.read_text(encoding="utf-8")
    assert "RichOutputMiddleware" not in wrappers
    # 工具执行组合包装器保留：绑定实例 + MCP 重试 + 异常兜底
    assert "composed_tool_wrapper" in wrappers
    assert "_handle_artifact" not in wrappers
    bg_builder = BUSINESS_GRAPH_BUILDER.read_text(encoding="utf-8")
    assert "basic_qa_tool_wrapper" in bg_builder


def test_business_graphs_node_flow_finalize_output() -> None:
    # 中间件已移除，节点流不再引用 ArtifactMiddleware / scan_text
    bg_nodes = BUSINESS_GRAPH_NODES.read_text(encoding="utf-8")
    assert "ArtifactMiddleware" not in bg_nodes
    assert "scan_text" not in bg_nodes

    # 清洗草稿经共享发布器逐段发布；节点不自行拼装 final_output_* 事件。
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    assert "finalize_output" in da_graph
    assert "_stream_cleaned_answer" not in da_nodes
    assert "stream_final_answer" in da_nodes
    assert "publish_control_message" in da_nodes
    assert "final_output_delta" not in da_nodes
    assert "final_output_done" not in da_nodes


def test_mcp_backed_graphs_do_not_initialize_mcp_at_import() -> None:
    """在全新进程编译公开工厂，捕获实际连接尝试。"""
    import os
    import subprocess
    import sys

    script = """
import socket
attempts = []
def blocked(self, address):
    attempts.append(address)
    raise AssertionError('graph import must not connect')
socket.socket.connect = blocked
from basic_qa.graph import graph as qa
from data_analysis.graph import graph as data
for factory in (qa, data):
    compiled = factory({})
    assert compiled.checkpointer is None
    assert compiled.get_graph().nodes
assert not attempts, attempts
"""
    env = dict(
        os.environ, PYTHONPATH=str(Path("src").resolve()), MAIN_LLM_API_KEY="synthetic"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
