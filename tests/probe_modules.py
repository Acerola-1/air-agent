"""独立复测探针：模块级安全与边界保护（R05 / R06 / R09）."""

from __future__ import annotations

import json
import os
from pathlib import Path

import dotenv

dotenv.load_dotenv()
os.environ.setdefault("MCP_SERVER_URL", "http://127.0.0.1:8000/sse")
os.environ.setdefault("MCP_SERVER_NAME", "ipp")

from common.capabilities import BoundedSkillsBackend, filter_capability_mcp_tools
from common.mcp_scoped import ScopedMCPToolFactory
from common.skill_router import SkillSemanticRouter
from langchain_core.tools import StructuredTool


def test_probe_modules():
    repo_root = Path(__file__).resolve().parent.parent

    # 1. R05: Router 路径逃逸检测
    skills_dir = repo_root / "src" / "basic_qa" / "skills"
    router = SkillSemanticRouter(skills_dir)
    outside_candidate = router.resolve_skill_file("../../common/capabilities.py")
    router_res = {"outside_path_resolved": outside_candidate is not None}

    # 2. R05: BoundedSkillsBackend 边界隔离
    backend = BoundedSkillsBackend(root_dir=str(skills_dir))
    backend.set_explicit_skill("air-quality-basic-query")

    try:
        backend.read("../single-index-eval/SKILL.md")
        read_other = False
    except PermissionError:
        read_other = True

    download_other = backend.download_files(["../single-index-eval/SKILL.md"])
    grep_res = backend.grep("", path="")
    backend_res = {
        "read_other_skill_blocked": read_other,
        "download_other_skill_visible": any(getattr(d, "content", None) is not None for d in download_other),
        "grep_root_non_selected_visible": any(
            "single-index-eval" in str(m) for m in (getattr(grep_res, "matches", []) or [])
        ),
    }

    # 3. R06: 别名碰撞与稳定 ID 代理
    factory = ScopedMCPToolFactory(server_urls={})

    def dummy_fn():
        return "ok"

    t1 = StructuredTool.from_function(
        func=dummy_fn, name="query", description="t1"
    )
    t2 = StructuredTool.from_function(
        func=dummy_fn, name="query", description="t2"
    )

    factory._index_tools_locked([t1], server_key="service-a")
    factory._index_tools_locked([t2], server_key="service_a")
    server_of_collision = factory.server_of("query")
    alias_collision_res = {
        "resolved_server_on_collision": server_of_collision,
        "colliding_aliases": sorted(list(factory._colliding_aliases)),
    }

    from common.mcp_scoped import build_proxy_tools, get_factory
    factory = get_factory()
    factory._index_tools_locked([t1], server_key="controlled-one")
    factory._index_tools_locked([t2], server_key="controlled-two")
    all_proxies = build_proxy_tools()

    cfg_sel_one = {
        "configurable": {
            "capability_mcp_mode": "allowlist",
            "capability_tool_ids": ["mcp:controlled-one:query"],
        }
    }
    filtered = filter_capability_mcp_tools(all_proxies, cfg_sel_one)
    r06_res = {
        "alias_collision": alias_collision_res,
        "normal_two_service_disclosure": {
            "all_proxy_names": [t.name for t in all_proxies],
            "selected_first_service_disclosed_names": [t.name for t in filtered],
        },
    }

    # 4. R09: 游标编码与解码校验
    from common.capabilities import decode_cursor, encode_cursor

    c1 = encode_cursor(
        tenant_id="t1",
        user_id="u1",
        membership_id="m1",
        graph_id="basic-qa",
        catalog_version="cat_v1",
        offset=3,
    )
    decoded_off = decode_cursor(
        c1,
        expected_tenant_id="t1",
        expected_user_id="u1",
        expected_membership_id="m1",
        expected_graph_id="basic-qa",
        expected_catalog_version="cat_v1",
    )

    try:
        # 篡改 graph_id
        decode_cursor(
            c1,
            expected_tenant_id="t1",
            expected_user_id="u1",
            expected_membership_id="m1",
            expected_graph_id="other-graph",
            expected_catalog_version="cat_v1",
        )
        tamper_detected = False
    except Exception:
        tamper_detected = True

    pagination_res = {
        "cursor_encoded": c1 is not None,
        "offset_decoded_matches": decoded_off == 3,
        "tampered_cursor_rejected": tamper_detected,
    }

    report = {
        "router_path_escape": router_res,
        "bounded_backend_isolation": backend_res,
        "mcp_collision_and_proxies": r06_res,
        "cursor_pagination": pagination_res,
        "all_passed": (
            router_res["outside_path_resolved"] is False
            and backend_res["read_other_skill_blocked"] is True
            and backend_res["download_other_skill_visible"] is False
            and backend_res["grep_root_non_selected_visible"] is False
            and alias_collision_res["resolved_server_on_collision"] is None
            and pagination_res["offset_decoded_matches"] is True
            and pagination_res["tampered_cursor_rejected"] is True
        ),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    test_probe_modules()
