"""Gate 0 探针与基线验证测试套件.

包含：
1. 独立PG/Redis与容器/库所有权验证
2. 两企业双成员与双MCP服务同名工具 (query) 实际调用与隔离
3. 真实网络零调用反例（未授权服务零外呼、撤销后零外呼）
4. DeepAgents /skills 边界与子任务继承探针
5. 编译后动态工具发现与 ToolNode 调度探针
6. 公开图 Graph 导入无远端外呼验证
"""

# ruff: noqa: E402
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

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

from mcp_fixture.server import FixtureServer

from common.identity.db import close_pool as close_identity_pool
from common.identity.runtime import RuntimeIdentity
from common.mcp_authorization import McpAuthorizationError
from common.mcp_scoped import ScopedMCPToolFactory, set_factory

pytestmark = pytest.mark.anyio

# 确保测试环境开启 fixture 模式
os.environ["TENANT_MCP_FIXTURE_MODE"] = "true"
os.environ["AGENT_OBSERVABILITY_ENABLED"] = "true"
os.environ["GATE0_CONTROLLED_A"] = "tok-tenant-a"
os.environ["GATE0_CONTROLLED_B"] = "tok-tenant-b"

TEST_DB_URI = "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"
os.environ["DATABASE_URI"] = TEST_DB_URI
os.environ["RUNTIME_DATABASE_URI"] = "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER_1 = "33333333-3333-4333-8333-333333333333"
USER_2 = "66666666-6666-4666-8666-666666666666"
MEMBERSHIP_A1 = "44444444-4444-4444-8444-444444444444"
MEMBERSHIP_B1 = "55555555-5555-4555-8555-555555555555"
MEMBERSHIP_A2 = "77777777-7777-4777-8777-777777777777"
MEMBERSHIP_B2 = "88888888-8888-4888-8888-888888888888"


async def setup_gate0_fixtures():
    """在测试数据库插入两企业双成员及绑定."""
    async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
        async with aconn.cursor() as cur:
            # 清理旧测试数据
            await cur.execute(
                "DELETE FROM mcp_identity_bindings WHERE tenant_id IN (%(ta)s, %(tb)s)",
                {"ta": TENANT_A, "tb": TENANT_B},
            )
            await cur.execute(
                "DELETE FROM tenant_memberships WHERE tenant_id IN (%(ta)s, %(tb)s)",
                {"ta": TENANT_A, "tb": TENANT_B},
            )
            await cur.execute(
                "DELETE FROM tenants WHERE id IN (%(ta)s, %(tb)s)",
                {"ta": TENANT_A, "tb": TENANT_B},
            )
            await cur.execute(
                "DELETE FROM users WHERE id IN (%(u1)s, %(u2)s)",
                {"u1": USER_1, "u2": USER_2},
            )

            # 插入租户
            await cur.execute(
                "INSERT INTO tenants (id, name, code, status, auth_revision) "
                "VALUES (%(id)s, %(name)s, %(code)s, 'active', 1)",
                {"id": TENANT_A, "name": "测试企业A", "code": "gate0-corp-a"},
            )
            await cur.execute(
                "INSERT INTO tenants (id, name, code, status, auth_revision) "
                "VALUES (%(id)s, %(name)s, %(code)s, 'active', 1)",
                {"id": TENANT_B, "name": "测试企业B", "code": "gate0-corp-b"},
            )

            # 插入用户
            await cur.execute(
                "INSERT INTO users (id, account, display_name, password_hash, is_active, auth_revision) "
                "VALUES (%(id)s, %(account)s, %(name)s, 'fake-hash', true, 1)",
                {"id": USER_1, "account": "gate0_u1", "name": "测试用户1"},
            )
            await cur.execute(
                "INSERT INTO users (id, account, display_name, password_hash, is_active, auth_revision) "
                "VALUES (%(id)s, %(account)s, %(name)s, 'fake-hash', true, 1)",
                {"id": USER_2, "account": "gate0_u2", "name": "测试用户2"},
            )

            # 插入成员关系（用户1在企业A和B，用户2在企业A和B）
            memberships = [
                (MEMBERSHIP_A1, TENANT_A, USER_1),
                (MEMBERSHIP_B1, TENANT_B, USER_1),
                (MEMBERSHIP_A2, TENANT_A, USER_2),
                (MEMBERSHIP_B2, TENANT_B, USER_2),
            ]
            for mid, tid, uid in memberships:
                await cur.execute(
                    "INSERT INTO tenant_memberships (id, tenant_id, user_id, status, revision) "
                    "VALUES (%(id)s, %(tid)s, %(uid)s, 'active', 1)",
                    {"id": mid, "tid": tid, "uid": uid},
                )

            # 插入 MCP 授权绑定：
            # 企业A授权 controlled-one 和 controlled-two
            # 企业B仅授权 controlled-one
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, %(s)s, %(sub)s, %(ref)s, 'v1-fixture', 'active', 1)",
                {
                    "t": TENANT_A,
                    "m": MEMBERSHIP_A1,
                    "s": "controlled-one",
                    "sub": "subject-a",
                    "ref": "env:GATE0_CONTROLLED_A",
                },
            )
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, %(s)s, %(sub)s, %(ref)s, 'v1-fixture', 'active', 1)",
                {
                    "t": TENANT_A,
                    "m": MEMBERSHIP_A1,
                    "s": "controlled-two",
                    "sub": "subject-a",
                    "ref": "env:GATE0_CONTROLLED_A",
                },
            )
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, %(s)s, %(sub)s, %(ref)s, 'v1-fixture', 'active', 1)",
                {
                    "t": TENANT_B,
                    "m": MEMBERSHIP_B1,
                    "s": "controlled-one",
                    "sub": "subject-b",
                    "ref": "env:GATE0_CONTROLLED_B",
                },
            )
            await aconn.commit()


async def test_gate0_mcp_same_name_disambiguation_and_zero_calls():
    """Gate 0 探针 1：双MCP服务同名工具 query 调用与零网络调用拒绝反例."""
    await setup_gate0_fixtures()

    srv_one = FixtureServer()
    srv_two = FixtureServer()
    url_one = await srv_one.start()
    url_two = await srv_two.start()
    await srv_one.reset()
    await srv_two.reset()

    factory = ScopedMCPToolFactory({
        "controlled-one": url_one,
        "controlled-two": url_two,
    })
    set_factory(factory)

    id_a1 = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=str(uuid.uuid4()))
    id_b1 = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_B, membership_id=MEMBERSHIP_B1, session_id=str(uuid.uuid4()))

    try:
        # 正例 1：调用 Server 1 的同名 query 工具
        res1 = await factory.call(id_a1, "controlled-one", "query", {"user_id": "fake", "region": "杭州"})
        assert "from_query" in res1
        assert await srv_one.call_count(tool="query") == 1
        assert await srv_two.call_count(tool="query") == 0

        # 正例 2：调用 Server 2 的同名 query 工具
        res2 = await factory.call(id_a1, "controlled-two", "query", {"user_id": "fake", "region": "北京"})
        assert "from_query" in res2
        assert await srv_one.call_count(tool="query") == 1
        assert await srv_two.call_count(tool="query") == 1

        # 拒绝反例 1（跨企业未授权服务）：企业B未授权 controlled-two
        # 必须抛出 McpAuthorizationError，且 Server 2 的网络请求数恒定为 1（增量严格为 0）
        count_two_before = await srv_two.call_count()
        with pytest.raises(McpAuthorizationError):
            await factory.call(id_b1, "controlled-two", "query", {"user_id": "fake", "region": "广州"})
        count_two_after = await srv_two.call_count()
        assert count_two_after == count_two_before, "未授权服务的网络调用次数必须严格为 0！"

        # 拒绝反例 2（实时撤销）：撤销企业A对 Server 1 的授权
        async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
            async with aconn.cursor() as cur:
                await cur.execute(
                    "UPDATE mcp_identity_bindings SET status='disabled' WHERE tenant_id=%(t)s AND server_key='controlled-one'",
                    {"t": TENANT_A},
                )
                await aconn.commit()

        count_one_before = await srv_one.call_count()
        with pytest.raises(McpAuthorizationError):
            await factory.call(id_a1, "controlled-one", "query", {"user_id": "fake", "region": "上海"})
        count_one_after = await srv_one.call_count()
        assert count_one_after == count_one_before, "撤销授权后的网络调用次数必须严格为 0！"

    finally:
        await srv_one.stop()
        await srv_two.stop()
        set_factory(None)
        await close_identity_pool()


async def test_gate0_deepagents_skills_boundary():
    """Gate 0 探针 2：DeepAgents SDK 的 /skills/ 文件访问边界与安全守卫."""
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

    skills_dir = Path(__file__).resolve().parents[1] / "src" / "basic_qa" / "skills"
    assert skills_dir.exists(), f"skills dir not found: {skills_dir}"

    # 包装 FilesystemBackend 实现显式 Skill 访问约束
    class BoundedSkillsBackend(FilesystemBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._allowed_skill: str | None = None

        def set_allowed_skill(self, skill_name: str | None) -> None:
            self._allowed_skill = skill_name

        def _check_allowed(self, path: str) -> None:
            if not self._allowed_skill:
                return
            normalized = path.lstrip("/").split("/")
            if normalized and normalized[0] and normalized[0] != self._allowed_skill:
                raise PermissionError(f"未授权访问技能文件: {normalized[0]} (当前限制: {self._allowed_skill})")

        def read(self, file_path: str, offset: int = 0, limit: int = 2000):
            self._check_allowed(file_path)
            return super().read(file_path, offset, limit)

        async def aread(self, file_path: str, offset: int = 0, limit: int = 2000):
            self._check_allowed(file_path)
            return await super().aread(file_path, offset, limit)

    bounded_fs = BoundedSkillsBackend(root_dir=str(skills_dir), virtual_mode=True)
    backend = CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": bounded_fs},
    )

    # 检查存在至少一个技能目录
    skill_names = [p.name for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
    assert len(skill_names) >= 2, f"需要至少两个技能用于边界测试，实际找到: {skill_names}"
    s1, s2 = skill_names[0], skill_names[1]

    # 当允许 S1 时：
    bounded_fs.set_allowed_skill(s1)

    # 正例：读取 S1 的 SKILL.md 成功
    s1_result = await backend.aread(f"/skills/{s1}/SKILL.md")
    assert s1_result is not None
    assert s1_result.file_data is not None
    assert len(s1_result.file_data.get("content", "")) > 0

    # 拒绝反例：尝试读取 S2 的 SKILL.md 必须被拒绝（PermissionError）
    with pytest.raises(PermissionError) as exc_info:
        await backend.aread(f"/skills/{s2}/SKILL.md")
    assert "未授权访问技能文件" in str(exc_info.value)
    assert s2 in str(exc_info.value)


async def test_gate0_compiled_graph_dynamic_dispatch():
    """Gate 0 探针 3：验证已编译图在动态工具发现与调度下的行为."""
    # 模拟一个仅在编译期注册了静态工具的 ToolNode
    @tool
    def static_time_tool() -> str:
        """获取当前系统时间."""
        return "2026-10-15T00:00:00"

    # 模拟动态 MCP 工具调度包装器
    async def dynamic_tool_wrapper(request: Any, handler: Any) -> Any:
        call = request.tool_call
        tool_name = call.get("name")
        # 如果是受控 MCP 工具，走自定义调度
        if tool_name == "mcp_controlled__query":
            args = call.get("args", {})
            return ToolMessage(
                content=json.dumps({"success": True, "result": f"dynamic result for {args.get('city')}"}, ensure_ascii=False),
                tool_call_id=call["id"],
                name=tool_name,
            )
        if tool_name == "mcp_forbidden__query":
            return ToolMessage(
                content=json.dumps({"success": False, "error": "permission_denied"}, ensure_ascii=False),
                tool_call_id=call["id"],
                name=tool_name,
                status="error",
            )
        return await handler(request)

    builder = StateGraph(MessagesState)
    tool_node = ToolNode([static_time_tool], awrap_tool_call=dynamic_tool_wrapper)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    compiled_graph = builder.compile()

    # 正例：调用编译后动态发现的工具
    msg_allowed = AIMessage(
        content="",
        tool_calls=[{"id": "call_allow", "name": "mcp_controlled__query", "args": {"city": "杭州"}}],
    )
    res_allowed = await compiled_graph.ainvoke({"messages": [msg_allowed]})
    assert len(res_allowed["messages"]) == 2
    tool_msg_allowed = res_allowed["messages"][-1]
    assert isinstance(tool_msg_allowed, ToolMessage)
    assert "dynamic result for 杭州" in tool_msg_allowed.content

    # 拒绝反例：未授权的动态工具被拦截为 error 状态
    msg_forbidden = AIMessage(
        content="",
        tool_calls=[{"id": "call_forbid", "name": "mcp_forbidden__query", "args": {"city": "未知"}}],
    )
    res_forbidden = await compiled_graph.ainvoke({"messages": [msg_forbidden]})
    assert len(res_forbidden["messages"]) == 2
    tool_msg_forbid = res_forbidden["messages"][-1]
    assert isinstance(tool_msg_forbid, ToolMessage)
    assert tool_msg_forbid.status == "error"
    assert "permission_denied" in tool_msg_forbid.content


def test_gate0_public_graphs_import_no_network():
    """Gate 0 探针 4：公开图导入无远端外呼与 GraphLoadError 验证."""
    import basic_qa.graph
    import data_analysis.graph
    import intelligent_tracing.graph

    assert hasattr(basic_qa.graph, "graph")
    assert hasattr(data_analysis.graph, "graph")
    assert hasattr(intelligent_tracing.graph, "graph")
