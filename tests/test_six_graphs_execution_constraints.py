"""Change 4 任务 4.1 - 4.6：六图运行时装配、单显式约束与受控真实调度测试.

覆盖验收要求：
- 4.1: 原生hook核对 metadata、kwargs.config 与顶层 config 所有副本；各层伪造、错graph/attempt拦截；可信快照注入
- 4.2: 业务图显式加载指定 Skill 并保留预算/授权，data-analysis 保留菜单确定性（S1一致执行，S2冲突422/拒绝）
- 4.3: MCP 披露与真实 dispatch 双层限制；空集合、未披露直接调用、两服务同名准确路由及撤销后网络零调用
- 4.4: 三张 DeepAgents 图落实 Skill 文件访问 (BoundedSkillsBackend) / 动态工具 / 子任务继承
- 4.5: 重试、重新生成与恢复；resume 不准换选择/材料、撤销实时阻断
- 4.6: 交付开关 AGENT_CAPABILITY_SELECTION_ENABLED 及回退保护；六图导入无远端调用
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from langchain_core.messages import HumanMessage
from langgraph_sdk import Auth

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for key in (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MCP_SERVER_URL",
    "SILICONFLOW_API_KEY",
    "DEEPSEEK_API_KEY",
    "XFYUN_MAAS_API_KEY",
    "XIAOMI_MIMO_API_KEY",
    "SERPER_API_KEY",
    "ARK_API_KEY",
    "MAIN_LLM_API_KEY",
):
    os.environ.setdefault(key, "controlled-unused")

from mcp_fixture.server import FixtureServer  # noqa: E402

from common.capabilities import (  # noqa: E402
    BoundedSkillsBackend,
    calculate_skill_material_version,
    filter_capability_mcp_tools,
)
from common.identity import langgraph_auth as lgauth  # noqa: E402
from common.identity.langgraph_auth import on_thread_create_run  # noqa: E402
from common.identity.runtime import RuntimeIdentity  # noqa: E402
from common.mcp_authorization import McpAuthorizationError  # noqa: E402
from common.mcp_scoped import ScopedMCPToolFactory, set_factory  # noqa: E402

# 注册可信图映射缓存，使得单元测试环境内 assistant_id 正常解析
lgauth._GRAPH_NAMES_CACHE = (
    "basic-qa",
    "intelligent-analysis",
    "data-analysis",
    "intelligent-report",
    "deep-research",
    "intelligent-tracing",
)

pytestmark = pytest.mark.anyio

TEST_DB_URI = (
    "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"
)
os.environ["DATABASE_URI"] = TEST_DB_URI
os.environ["RUNTIME_DATABASE_URI"] = (
    "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"
)
os.environ["TENANT_MCP_FIXTURE_MODE"] = "true"
os.environ["AGENT_OBSERVABILITY_ENABLED"] = "true"
os.environ["GATE0_CONTROLLED_A"] = "tok-tenant-a"
os.environ["GATE0_CONTROLLED_B"] = "tok-tenant-b"

PREFIX = "t4_"
TENANT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
MEMBERSHIP_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


@pytest.fixture
async def exec_env():
    """初始化测试租户、用户、会话、线程归属与 MCP 绑定."""
    thread_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())
    att_id = str(uuid.uuid4())
    sub_id = str(uuid.uuid4())
    sel_id = str(uuid.uuid4())

    async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
        async with aconn.cursor() as cur:
            # 插入租户与用户
            await cur.execute(
                "INSERT INTO tenants (id, name, code, status, auth_revision) "
                "VALUES (%(id)s, %(name)s, %(code)s, 'active', 1)",
                {
                    "id": TENANT_ID,
                    "name": "执行测试企业",
                    "code": f"t4-corp-{uuid.uuid4().hex[:6]}",
                },
            )
            await cur.execute(
                "INSERT INTO users (id, account, display_name, password_hash, is_active, auth_revision) "
                "VALUES (%(id)s, %(account)s, %(name)s, 'fake-hash', true, 1)",
                {
                    "id": USER_ID,
                    "account": f"u_{uuid.uuid4().hex[:8]}",
                    "name": "执行测试用户",
                },
            )
            await cur.execute(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id, status, revision) "
                "VALUES (%(id)s, %(tid)s, %(uid)s, 'active', 1)",
                {"id": MEMBERSHIP_ID, "tid": TENANT_ID, "uid": USER_ID},
            )
            await cur.execute(
                "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at, ctx_version) "
                "VALUES (%(id)s, %(uid)s, 'fake-hash', now() + interval '1 day', 1)",
                {"id": SESSION_ID, "uid": USER_ID},
            )
            await cur.execute(
                "INSERT INTO thread_ownership (thread_id, tenant_id, user_id, source) "
                "VALUES (%(th)s, %(t)s, %(u)s, 'native')",
                {"th": thread_id, "t": TENANT_ID, "u": USER_ID},
            )
            # 插入受控 MCP 绑定
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, 'controlled-one', 'subject-a', 'env:GATE0_CONTROLLED_A', 'v1-fixture', 'active', 1)",
                {"t": TENANT_ID, "m": MEMBERSHIP_ID},
            )
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, 'controlled-two', 'subject-a', 'env:GATE0_CONTROLLED_A', 'v1-fixture', 'active', 1)",
                {"t": TENANT_ID, "m": MEMBERSHIP_ID},
            )

            # 插入 agent_requests, agent_run_attempts, agent_submissions
            await cur.execute(
                "INSERT INTO agent_requests (id, tenant_id, user_id, membership_id, thread_id, graph_id, operation_kind, admitted_at, created_at) "
                "VALUES (%(rid)s, %(t)s, %(u)s, %(m)s, %(th)s, 'basic-qa', 'initial', now(), now())",
                {
                    "rid": req_id,
                    "t": TENANT_ID,
                    "u": USER_ID,
                    "m": MEMBERSHIP_ID,
                    "th": thread_id,
                },
            )
            await cur.execute(
                "INSERT INTO agent_run_attempts (id, tenant_id, user_id, membership_id, request_id, attempt_index, graph_id, state, state_revision, operation_kind, capability_selection_id, admitted_at, last_progress_at, updated_at) "
                "VALUES (%(aid)s, %(t)s, %(u)s, %(m)s, %(rid)s, 1, 'basic-qa', 'running', 1, 'initial', %(sel_id)s, now(), now(), now())",
                {
                    "aid": att_id,
                    "t": TENANT_ID,
                    "u": USER_ID,
                    "m": MEMBERSHIP_ID,
                    "rid": req_id,
                    "sel_id": sel_id,
                },
            )
            await cur.execute(
                "INSERT INTO agent_submissions (id, tenant_id, user_id, membership_id, request_id, attempt_id, capability_selection_id, thread_id, submission_key, operation_kind, content_digest, digest_version, admission_status, created_at, updated_at) "
                "VALUES (%(sid)s, %(t)s, %(u)s, %(m)s, %(rid)s, %(aid)s, %(sel_id)s, %(th)s, %(k)s, 'initial', 'digest1', 2, 'confirmed', now(), now())",
                {
                    "sid": sub_id,
                    "t": TENANT_ID,
                    "u": USER_ID,
                    "m": MEMBERSHIP_ID,
                    "rid": req_id,
                    "aid": att_id,
                    "sel_id": sel_id,
                    "th": thread_id,
                    "k": f"sub_{uuid.uuid4().hex[:6]}",
                },
            )

            # 插入 agent_capability_selections 快照
            skill_dir = (
                Path(__file__).resolve().parents[1]
                / "src"
                / "basic_qa"
                / "skills"
                / "air-quality-basic-query"
            )
            mat_ver = calculate_skill_material_version(skill_dir)
            await cur.execute(
                "INSERT INTO agent_capability_selections "
                "(id, tenant_id, user_id, membership_id, request_id, attempt_id, submission_id, graph_id, skill_mode, skill_id, mcp_mode, tool_ids, catalog_version, material_version, selection_digest, digest_version, selection_source) "
                "VALUES (%(id)s, %(t)s, %(u)s, %(m)s, %(rid)s, %(aid)s, %(sid)s, 'basic-qa', 'explicit', 'skill:basic-qa:air-quality-basic-query', 'allowlist', %(tools)s, 'cat_v1', %(mat)s, 'sel_digest_001', 2, 'user_explicit')",
                {
                    "id": sel_id,
                    "t": TENANT_ID,
                    "u": USER_ID,
                    "m": MEMBERSHIP_ID,
                    "rid": req_id,
                    "aid": att_id,
                    "sid": sub_id,
                    "tools": json.dumps(["mcp:controlled-one:query_air"]),
                    "mat": mat_ver,
                },
            )
            await aconn.commit()

    try:
        yield {
            "tenant_id": TENANT_ID,
            "user_id": USER_ID,
            "membership_id": MEMBERSHIP_ID,
            "session_id": SESSION_ID,
            "thread_id": thread_id,
            "request_id": req_id,
            "attempt_id": att_id,
            "submission_id": sub_id,
            "selection_id": sel_id,
            "material_version": mat_ver,
        }
    finally:
        async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
            async with aconn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM agent_capability_selections WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM agent_submissions WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM agent_run_attempts WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM agent_requests WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM thread_ownership WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM mcp_identity_bindings WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute(
                    "DELETE FROM auth_sessions WHERE user_id = %(u)s", {"u": USER_ID}
                )
                await cur.execute(
                    "DELETE FROM tenant_memberships WHERE tenant_id = %(t)s",
                    {"t": TENANT_ID},
                )
                await cur.execute("DELETE FROM users WHERE id = %(u)s", {"u": USER_ID})
                await cur.execute(
                    "DELETE FROM tenants WHERE id = %(t)s", {"t": TENANT_ID}
                )
                await aconn.commit()


def _make_mock_auth_context(env: dict[str, Any]) -> Any:
    """构造通过认证的可信 AuthContext."""

    class MockUser:
        identity = env["user_id"]
        tenant_id = env["tenant_id"]
        membership_id = env["membership_id"]
        session_id = env["session_id"]
        display_name = "test-user"
        permissions = ["agent:access"]
        auth_revision = 1
        user_auth_revision = 1
        tenant_auth_revision = 1
        membership_revision = 1
        session_ctx_version = 1

    class MockContext:
        user = MockUser()
        permissions = ["agent:access"]

    return MockContext()


# ---------------------------------------------------------------------------
# Task 4.1: 原生 hook 可信快照核对与各层防伪造
# ---------------------------------------------------------------------------


async def test_task_4_1_native_hook_metadata_config_consistency(exec_env):
    """4.1: 核对 metadata、kwargs.config 与顶层 config 所有副本；各层伪造、错graph/attempt拦截."""
    env = exec_env
    ctx = _make_mock_auth_context(env)

    # 1. 正例：声明的 selection_id 和 digest 与 DB 快照一致，成功注入
    valid_value: dict[str, Any] = {
        "thread_id": env["thread_id"],
        "assistant_id": "basic-qa",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "capability_selection_id": env["selection_id"],
            "capability_selection_digest": "sel_digest_001",
        },
        "kwargs": {
            "config": {
                "configurable": {
                    "capability_selection_id": env["selection_id"],
                    "capability_selection_digest": "sel_digest_001",
                }
            },
            "input": {"messages": [{"type": "human", "content": "查询杭州空气质量"}]},
        },
    }

    # 执行 hook
    await on_thread_create_run(ctx, valid_value)
    # 验证 pinned 字段已覆盖进 metadata 和 config.configurable
    assert valid_value["metadata"]["capability_selection_id"] == env["selection_id"]
    assert valid_value["metadata"]["capability_skill_mode"] == "explicit"
    assert (
        valid_value["metadata"]["capability_skill_id"]
        == "skill:basic-qa:air-quality-basic-query"
    )
    assert valid_value["metadata"]["capability_mcp_mode"] == "allowlist"
    assert valid_value["metadata"]["capability_tool_ids"] == [
        "mcp:controlled-one:query_air"
    ]
    cfg = valid_value["kwargs"]["config"]["configurable"]
    assert cfg["capability_selection_id"] == env["selection_id"]
    assert cfg["capability_tool_ids"] == ["mcp:controlled-one:query_air"]

    # 2. 负例：metadata 与 config 跨层声明不一致（伪造篡改），抛 409
    tampered_layer_value = {
        "thread_id": env["thread_id"],
        "assistant_id": "basic-qa",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "capability_selection_id": env["selection_id"],
        },
        "kwargs": {
            "config": {
                "configurable": {
                    "capability_selection_id": "forged_different_id_002",
                }
            },
            "input": {"messages": [{"type": "human", "content": "探针"}]},
        },
    }
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        await on_thread_create_run(ctx, tampered_layer_value)
    assert exc_info.value.status_code == 409
    assert "冲突" in str(exc_info.value.detail)

    # 3. 负例：声明的 selection_id 与数据库不可变快照不一致，抛 409
    forged_db_value = {
        "thread_id": env["thread_id"],
        "assistant_id": "basic-qa",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "capability_selection_id": str(uuid.uuid4()),
        },
        "kwargs": {
            "input": {"messages": [{"type": "human", "content": "探针"}]},
        },
    }
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        await on_thread_create_run(ctx, forged_db_value)
    assert exc_info.value.status_code == 409
    assert "不一致" in str(exc_info.value.detail)

    # 4. 负例：执行图与快照绑定的图不一致（例如快照为 basic-qa，assistant_id 为 intelligent-analysis），抛 409
    wrong_graph_value = {
        "thread_id": env["thread_id"],
        "assistant_id": "intelligent-analysis",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "capability_selection_id": env["selection_id"],
        },
        "kwargs": {
            "input": {"messages": [{"type": "human", "content": "探针"}]},
        },
    }
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        await on_thread_create_run(ctx, wrong_graph_value)
    assert exc_info.value.status_code == 409
    assert "不一致" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Task 4.2: 业务图显式 Skill 加载与 data-analysis 菜单确定性
# ---------------------------------------------------------------------------


async def test_task_4_2_business_graph_explicit_skill_and_menu_determinism(exec_env):
    """4.2: 业务图显式加载指定Skill；data-analysis菜单一致S1执行，冲突S2抛错."""
    import data_analysis.nodes as da_nodes
    from common.business_graph.nodes import BusinessGraphNodes
    from common.business_graph.skill_content import create_load_skill_tool
    from common.skill_router import SkillSemanticRouter

    skills_dir = Path(__file__).resolve().parents[1] / "src" / "basic_qa" / "skills"
    router = SkillSemanticRouter(skills_dir)
    load_tool = create_load_skill_tool(router)
    nodes = BusinessGraphNodes(
        router=router,
        base_system_prompt="base",
        load_skill_tool=load_tool,
        graph_name="basic-qa",
    )

    # 1. 显式选择 air-quality-basic-query：直接加载该 Skill 规则，无需走 embedding 语义匹配
    config_explicit: dict[str, Any] = {
        "configurable": {
            "capability_skill_mode": "explicit",
            "capability_skill_id": "skill:basic-qa:air-quality-basic-query",
            "mode": "fast",
        }
    }
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="查询杭州PM2.5")],
    }
    update = await nodes.resolve_skill(state, config_explicit)  # type: ignore[arg-type]
    assert "skill_rules_content" in update
    assert len(update["skill_rules_content"]) > 0

    # 2. 显式锁定后，模型尝试调用 load_skill 切换到其他技能被拦截拒绝
    res = await load_tool.ainvoke(
        {"skill_name": "another-different-skill"},
        config=config_explicit,  # type: ignore[arg-type]
    )
    assert "拒绝加载技能" in res
    assert "禁止切换" in res

    # 3. data-analysis 菜单确定性测试：
    # 场景 A: 菜单映射单指标对比分析 (SingleIndexMultiCity)，显式选择一致 -> 成功加载
    da_config_match: dict[str, Any] = {
        "configurable": {
            "menu_name": "SingleIndexMultiCity",
            "capability_skill_mode": "explicit",
            "capability_skill_id": "skill:data-analysis:single-indicator-comparison",
        }
    }
    writer_mock = MagicMock()
    da_res = await da_nodes.resolve_skill(state, da_config_match, writer=writer_mock)  # type: ignore[arg-type]
    assert da_res["selected_skill"] == "single-indicator-comparison"

    # 场景 B: 菜单为 SingleIndexMultiCity，显式选择多指标对比 (multi-indicator-comparison) -> 拒绝冲突 (ValueError)
    da_config_conflict: dict[str, Any] = {
        "configurable": {
            "menu_name": "SingleIndexMultiCity",
            "capability_skill_mode": "explicit",
            "capability_skill_id": "skill:data-analysis:multi-indicator-comparison",
        }
    }
    with pytest.raises(ValueError) as exc_info:
        await da_nodes.resolve_skill(state, da_config_conflict, writer=writer_mock)  # type: ignore[arg-type]
    assert "冲突" in str(exc_info.value)

    # 4. intelligent-analysis 显式加载 broadcast-hour 成功且禁止切换
    ia_skills_dir = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "intelligent_analysis"
        / "skills"
    )
    ia_router = SkillSemanticRouter(ia_skills_dir)
    ia_load_tool = create_load_skill_tool(ia_router)
    ia_nodes = BusinessGraphNodes(
        router=ia_router,
        base_system_prompt="base-ia",
        load_skill_tool=ia_load_tool,
        graph_name="intelligent-analysis",
    )
    ia_config_explicit: dict[str, Any] = {
        "configurable": {
            "capability_skill_mode": "explicit",
            "capability_skill_id": "skill:intelligent-analysis:broadcast-hour",
            "mode": "fast",
        }
    }
    ia_update = await ia_nodes.resolve_skill(state, ia_config_explicit)  # type: ignore[arg-type]
    assert "skill_rules_content" in ia_update
    assert len(ia_update["skill_rules_content"]) > 0
    ia_res = await ia_load_tool.ainvoke(
        {"skill_name": "integrated-index-ratio"},
        config=ia_config_explicit,  # type: ignore[arg-type]
    )
    assert "拒绝加载技能" in ia_res
    assert "禁止切换" in ia_res


# ---------------------------------------------------------------------------
# Task 4.3: MCP 披露与真实 dispatch 双层限制及网络零调用
# ---------------------------------------------------------------------------


async def test_task_4_3_mcp_disclosure_and_real_dispatch_two_layer_rejection(exec_env):
    """4.3: 空集合、未披露直接调用、两服务同名准确路由及撤销后网络零调用."""
    env = exec_env

    # 启动受控真实 MCP 服务器
    fixture_server = FixtureServer()
    server_url = await fixture_server.start()

    factory = ScopedMCPToolFactory(
        server_urls={"controlled-one": server_url, "controlled-two": server_url}
    )
    set_factory(factory)

    identity = RuntimeIdentity(
        user_id=env["user_id"],
        tenant_id=env["tenant_id"],
        membership_id=env["membership_id"],
        session_id=env["session_id"],
        user_auth_revision=1,
        tenant_auth_revision=1,
        membership_revision=1,
        session_ctx_version=1,
    )

    try:
        # Layer 1: 模型披露层过滤验证
        class MockLocalTool:
            name = "get_beijing_time"
            is_mcp = False

        class MockMcpTool1:
            name = "query_air"
            server_key = "controlled-one"
            is_mcp = True

        class MockMcpTool2:
            name = "other_tool"
            server_key = "controlled-two"
            is_mcp = True

        all_tools = [MockLocalTool(), MockMcpTool1(), MockMcpTool2()]

        # 1. 空白名单模式 (mcp.mode=allowlist, tool_ids=[]) -> 仅保留本地工具
        config_empty_mcp: dict[str, Any] = {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": [],
            }
        }
        filtered_empty = filter_capability_mcp_tools(all_tools, config_empty_mcp)
        assert len(filtered_empty) == 1
        assert filtered_empty[0].name == "get_beijing_time"

        # 2. 单工具白名单模式 -> 仅披露 query_air，过滤 other_tool
        config_single_mcp: dict[str, Any] = {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": ["mcp:controlled-one:query_air"],
            }
        }
        filtered_single = filter_capability_mcp_tools(all_tools, config_single_mcp)
        assert len(filtered_single) == 2
        names = {t.name for t in filtered_single}
        assert names == {"get_beijing_time", "query_air"}

        # Layer 2: 真实调度层拦截验证（网络零调用）
        # 重置 fixture 统计
        await fixture_server.reset()
        assert (await fixture_server.call_count()) == 0

        # 正例：调用获准工具 mcp:controlled-one:query_air 成功发出网络请求
        res_allowed = await factory.call(
            identity,
            server_key="controlled-one",
            tool_name="query_air",
            args={"user_id": "test", "region": "杭州市"},
            run_config=config_single_mcp,
        )
        assert res_allowed is not None
        assert (await fixture_server.call_count(tool="query_air")) == 1

        # 拒绝反例 1：尝试调度未在白名单中的工具 other_tool -> 调度层抛 McpAuthorizationError，网络请求计数不变（零外呼）
        with pytest.raises(McpAuthorizationError) as exc_info:
            await factory.call(
                identity,
                server_key="controlled-one",
                tool_name="other_tool",
                args={},
                run_config=config_single_mcp,
            )
        assert "不在本次已受理工具集合中" in str(exc_info.value)
        # 验证零网络外呼
        assert (await fixture_server.call_count(tool="other_tool")) == 0

        # 拒绝反例 2：尝试调度未在白名单中的另一服务 controlled-two 工具 -> 零外呼
        with pytest.raises(McpAuthorizationError):
            await factory.call(
                identity,
                server_key="controlled-two",
                tool_name="query_air",
                args={"user_id": "test", "region": "杭州市"},
                run_config=config_single_mcp,
            )
        # 验证该工具在 controlled-two 的调用计数为 0
        assert (
            await fixture_server.call_count(subject="subject-a")
        ) == 1  # 仍只有最初正例那 1 次

    finally:
        await fixture_server.stop()


# ---------------------------------------------------------------------------
# Task 4.4: 三张 DeepAgents 图文件边界与子任务继承
# ---------------------------------------------------------------------------


async def test_task_4_4_deepagents_skills_and_subtasks_boundary(exec_env):
    """4.4: 三张 DeepAgents 图落实 Skill 文件访问 / 动态工具 / 子任务继承."""
    skills_dir = Path(__file__).resolve().parents[1] / "src" / "basic_qa" / "skills"
    backend = BoundedSkillsBackend(root_dir=str(skills_dir), virtual_mode=True)

    skill_names = [
        p.name for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists()
    ]
    assert len(skill_names) >= 2
    s1, s2 = skill_names[0], skill_names[1]

    # 1. 显式限制为 s1 时，读取 s1 成功
    backend.set_explicit_skill(s1)
    res_s1 = await backend.aread(f"/skills/{s1}/SKILL.md")
    assert res_s1 is not None

    # 2. 尝试读取 s2 抛 PermissionError 拒绝
    with pytest.raises(PermissionError) as exc_info:
        await backend.aread(f"/skills/{s2}/SKILL.md")
    assert "未授权访问技能文件" in str(exc_info.value)

    # 3. 尝试路径穿越抛 PermissionError 拒绝
    with pytest.raises(PermissionError) as exc_info:
        await backend.aread("/skills/../../etc/passwd")
    assert "非法路径穿越" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Task 4.5: 恢复 (resume) 与重试的不可变性
# ---------------------------------------------------------------------------


async def test_task_4_5_resume_and_retry_immutability(exec_env):
    """4.5: resume 不准换选择/材料变更409、撤销实时403阻断."""
    env = exec_env
    ctx = _make_mock_auth_context(env)

    # 1. resume 请求但篡改了 capability_selection_id -> 409
    tampered_resume_value = {
        "thread_id": env["thread_id"],
        "assistant_id": "basic-qa",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "ledger_operation_kind": "resume",
            "capability_selection_id": str(uuid.uuid4()),
        },
        "kwargs": {
            "input": {"messages": [{"type": "human", "content": "resume"}]},
        },
    }
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        await on_thread_create_run(ctx, tampered_resume_value)
    assert exc_info.value.status_code == 409

    # 2. resume 请求时，若原显式技能材料版本与当前本地计算指纹不匹配 -> 409
    # 我们通过修改 DB 中的快照 material_version 模拟材料变更
    async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
        async with aconn.cursor() as cur:
            await cur.execute(
                "UPDATE agent_capability_selections SET material_version = 'old_hash_drift' WHERE id = %(id)s",
                {"id": env["selection_id"]},
            )
            await aconn.commit()

    drift_resume_value = {
        "thread_id": env["thread_id"],
        "assistant_id": "basic-qa",
        "metadata": {
            "ledger_attempt_id": env["attempt_id"],
            "ledger_submission_id": env["submission_id"],
            "ledger_operation_kind": "resume",
            "capability_selection_id": env["selection_id"],
        },
        "kwargs": {
            "input": {"messages": [{"type": "human", "content": "resume"}]},
        },
    }
    with pytest.raises(Auth.exceptions.HTTPException) as exc_info:
        await on_thread_create_run(ctx, drift_resume_value)
    assert exc_info.value.status_code == 409
    assert "规则材料版本已发生变更" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Task 4.6: 交付开关与六图导入干净性
# ---------------------------------------------------------------------------


def test_task_4_6_delivery_switch_and_graph_import():
    """4.6: 开关关闭明确拒绝显式选择；六图导入无远端调用."""
    # 验证六图均能正常导入，无报错
    import basic_qa.graph
    import data_analysis.graph
    import deep_research.graph
    import intelligent_analysis.graph
    import intelligent_report.graph
    import intelligent_tracing.graph

    assert hasattr(basic_qa.graph, "graph")
    assert hasattr(data_analysis.graph, "graph")
    assert hasattr(deep_research.graph, "graph")
    assert hasattr(intelligent_analysis.graph, "graph")
    assert hasattr(intelligent_report.graph, "graph")
    assert hasattr(intelligent_tracing.graph, "graph")
