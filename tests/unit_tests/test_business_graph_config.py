from pathlib import Path

# 使用 create_deep_agent 的 DeepAgent 图
DEEP_AGENT_GRAPHS = {
    "intelligent-report": Path("src/intelligent_report/graph.py"),
    "deep-research": Path("src/deep_research/graph.py"),
    "intelligent-tracing": Path("src/intelligent_tracing/graph.py"),
}

# 使用 StateGraph 节点流的图
STATE_GRAPHS = {
    "data-analysis": Path("src/data_analysis/graph.py"),
    "basic-qa": Path("src/basic_qa/graph.py"),
    "intelligent-analysis": Path("src/intelligent_analysis/graph.py"),
}

# basic_qa / intelligent_analysis 共享的业务图节点流模块
BUSINESS_GRAPH_BUILDER = Path("src/common/business_graph/builder.py")
BUSINESS_GRAPH_NODES = Path("src/common/business_graph/nodes.py")
BUSINESS_GRAPH_PROMPTING = Path("src/common/business_graph/prompting.py")
BUSINESS_GRAPH_TOOL_WRAPPERS = Path("src/common/business_graph/tool_wrappers.py")

BUSINESS_GRAPH_FILES = {**DEEP_AGENT_GRAPHS, **STATE_GRAPHS}


def test_business_graph_registry_contains_six_public_graphs() -> None:
    assert set(BUSINESS_GRAPH_FILES) == {
        "basic-qa",
        "intelligent-analysis",
        "data-analysis",
        "intelligent-report",
        "deep-research",
        "intelligent-tracing",
    }


def test_business_graph_configs_have_graph_level_skills_and_prompts() -> None:
    # 3 个 DeepAgents 图（intelligent_report / deep_research / intelligent_tracing）
    # 是空业务占位, 未来各自独立演化. 每个图保留自己的 create_deep_agent 调用,
    # 不抽离共享工厂. 此处仍按"每图自包含"的方式断言源码结构.
    for graph_name, graph_file in DEEP_AGENT_GRAPHS.items():
        source = graph_file.read_text(encoding="utf-8")
        assert 'SKILLS_DIR = Path(__file__).parent / "skills"' in source
        assert "tools=[find_skill]" in source
        assert "with_main_agent_tool_use_output_guard" in source
        assert 'unmatched_policy="native"' in source
        assert "你是中科宇图的空气质量数据查询助手" in source
        assert f'name="{graph_name}"' in source
        # langgraph-api 兼容: 编译时 checkpointer 必须为 None, 不能用 get_checkpointer()
        assert "checkpointer=None" in source
        assert "get_checkpointer" not in source
        assert "rendered_text" not in source
        assert "subagent_type" not in source
        # 方案 A：关闭 deepagents 原生全量 skill 目录注入, 路由统一交给 find_skill
        assert 'skills=["/skills/"]' not in source

    bg_builder = BUSINESS_GRAPH_BUILDER.read_text(encoding="utf-8")
    bg_nodes = BUSINESS_GRAPH_NODES.read_text(encoding="utf-8")
    bg_prompting = BUSINESS_GRAPH_PROMPTING.read_text(encoding="utf-8")
    assert "check_permission" in bg_builder
    assert "resolve_skill" in bg_builder
    assert "_route_after_tools" in bg_builder
    assert "amatch" in bg_nodes
    assert "with_main_agent_tool_use_output_guard" in bg_prompting
    assert "你是中科宇图的空气质量数据查询助手" in bg_prompting

    # data-analysis 使用 StateGraph 节点流
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    da_prompt_builder = Path("src/data_analysis/prompt_builder.py").read_text(
        encoding="utf-8"
    )
    assert 'SKILLS_DIR = Path(__file__).parent / "skills"' in da_graph
    # skill 发现逻辑在 nodes.py 中
    assert "resolve_skill" in da_graph
    assert "match_menu_skill" in da_nodes
    assert "with_data_analysis_output_guard" in da_prompt_builder
    assert "with_main_agent_tool_use_output_guard" not in da_graph
    assert 'unmatched_policy="native"' not in da_graph
    assert "increment_iteration" not in da_graph
    # data-analysis 使用 StateGraph 节点流，支持工具执行后回到 call_model 生成答案
    assert "route_after_tools" in da_graph


def test_business_graph_configs_keep_local_graph_names() -> None:
    named_graphs = {
        **DEEP_AGENT_GRAPHS,
        "basic-qa": STATE_GRAPHS["basic-qa"],
        "intelligent-analysis": STATE_GRAPHS["intelligent-analysis"],
    }
    for graph_name, graph_file in named_graphs.items():
        source = graph_file.read_text(encoding="utf-8")
        assert f'name="{graph_name}"' in source


def test_basic_qa_uses_rich_output_pipeline() -> None:
    # basic-qa 迁移节点流后，富输出经共享 composed_tool_wrapper 处理
    source = STATE_GRAPHS["basic-qa"].read_text(encoding="utf-8")
    assert "LegacyChartDataMiddleware()" not in source

    wrappers = BUSINESS_GRAPH_TOOL_WRAPPERS.read_text(encoding="utf-8")
    # 由 ArtifactMiddleware（fenced code block 自动扫描 + create_artifact 显式工具）替代旧 RichOutputMiddleware
    assert "RichOutputMiddleware" not in wrappers
    assert "_handle_artifact" in wrappers
    bg_builder = BUSINESS_GRAPH_BUILDER.read_text(encoding="utf-8")
    assert "composed_tool_wrapper" in bg_builder


def test_business_graphs_use_final_output_cleanup_middleware() -> None:
    # 每个 DeepAgents 图独立声明自己的 middleware 链 (含 FinalOutputCleanupMiddleware)
    for graph_file in DEEP_AGENT_GRAPHS.values():
        source = graph_file.read_text(encoding="utf-8")
        assert "FinalOutputCleanupMiddleware" in source
        assert "FinalOutputCleanupMiddleware()" in source
    # StateGraph 节点流使用 finalize_output 节点替代
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    assert "finalize_output" in da_graph
    assert "_stream_cleaned_answer" not in da_nodes

    bg_builder = BUSINESS_GRAPH_BUILDER.read_text(encoding="utf-8")
    bg_nodes = BUSINESS_GRAPH_NODES.read_text(encoding="utf-8")
    # basic-qa / intelligent-analysis 图（business_graph）无 finalize_output 节点，
    # 但 call_model 节点中集成了 Artifact 源码扫描与最终清洗提示词注入
    assert "ArtifactMiddleware" in bg_nodes
    assert "scan_text" in bg_nodes
    # data-analysis 图仍然保留 finalize_output + 流式事件
    assert "final_output_delta" in da_nodes
    assert "final_output_done" in da_nodes


def test_final_output_cleanup_runs_before_expand_question_when_present() -> None:
    # 每个 DeepAgents 图独立维护中间件顺序: ExpandQuestionMiddleware 必须在 FinalOutputCleanupMiddleware 之前
    for graph_file in DEEP_AGENT_GRAPHS.values():
        source = graph_file.read_text(encoding="utf-8")
        cleanup_index = source.find("FinalOutputCleanupMiddleware()")
        expand_index = source.find("ExpandQuestionMiddleware()")
        if expand_index != -1 and cleanup_index != -1:
            assert expand_index < cleanup_index

def test_mcp_backed_graphs_do_not_initialize_mcp_at_import() -> None:
    for graph_name in ("basic-qa", "intelligent-analysis"):
        source = STATE_GRAPHS[graph_name].read_text(encoding="utf-8")

        assert "_business_tools = _resolve_tools()" not in source
        assert "ensure_mcp_tools_sync()" not in source

    # 业务图节点流的 MCP 刷新在 prepare_model 节点中
    bg_nodes = BUSINESS_GRAPH_NODES.read_text(encoding="utf-8")
    assert "ensure_mcp_tools" in bg_nodes
    assert "get_business_tools" in bg_nodes

    # data-analysis 使用 StateGraph 节点流，MCP 刷新在 prepare_model 节点中
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    assert "ensure_mcp_tools" in da_nodes
    assert "get_business_tools" in da_nodes
