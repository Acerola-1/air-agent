"""Change 4 Section 2 (目录与稳定ID) 验证测试.

包含：
1. Task 2.1: 本地 Skill 目录、材料指纹 (SHA-256)、稳定 ID 与防路径遍历测试
2. Task 2.2: 当前身份 MCP 授权目录、同名工具消歧与零发现请求验证
3. Task 2.3: 原生与代理接口鉴权、分页游标校验、2s/5s 超时与部分不可用
4. Task 2.4: 元数据与授权隔离，修订与租户上下文隔离 (A->B->A 与实时撤销)
"""

# ruff: noqa: E402
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

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

from common.capabilities import (
    calculate_skill_material_version,
    get_capabilities_catalog,
    list_graph_skills,
    validate_skill_selection,
)
from common.identity.db import close_pool as close_identity_pool
from common.identity.runtime import RuntimeIdentity
from common.mcp_authorization import McpAuthorizationError
from common.mcp_scoped import ScopedMCPToolFactory, set_factory

pytestmark = pytest.mark.anyio

os.environ["TENANT_MCP_FIXTURE_MODE"] = "true"
os.environ["GATE0_CONTROLLED_A"] = "tok-tenant-a"
os.environ["GATE0_CONTROLLED_B"] = "tok-tenant-b"
TEST_DB_URI = "postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"
os.environ["DATABASE_URI"] = TEST_DB_URI
os.environ["RUNTIME_DATABASE_URI"] = "postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"

KID = "k-test"
SECRET = "test-secret-key-32-bytes-long!!"
os.environ["RUNTIME_TICKET_KEYS"] = json.dumps({KID: SECRET})
os.environ["LEDGER_RECONCILE_KEYS"] = json.dumps({KID: SECRET})

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
USER_1 = "33333333-3333-4333-8333-333333333333"
USER_2 = "66666666-6666-4666-8666-666666666666"
MEMBERSHIP_A1 = "44444444-4444-4444-8444-444444444444"
MEMBERSHIP_B1 = "55555555-5555-4555-8555-555555555555"
SESSION_1 = "99999999-9999-4999-8999-999999999999"


def make_user_ticket(
    *,
    user_id: str,
    tenant_id: str,
    membership_id: str,
    session_id: str,
    kid: str = KID,
    secret: str = SECRET,
) -> str:
    """按 rt-v1 规范构造已签名用户运行票据."""
    now = int(time.time())
    payload = {
        "typ": "rt-v1",
        "sid": session_id,
        "uid": user_id,
        "tid": tenant_id,
        "mid": membership_id,
        "aud": "langgraph-runtime",
        "iat": now,
        "exp": now + 3600,
        "crev": 1,
        "urev": 1,
        "trev": 1,
        "mrev": 1,
    }
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"


def make_reconcile_ticket(
    *,
    tenant_id: str,
    kid: str = KID,
    secret: str = SECRET,
) -> str:
    """按 rs-v1 规范构造服务校准票据."""
    now = int(time.time())
    payload = {
        "typ": "rs-v1",
        "actor": "service:ledger-reconcile",
        "tenant": tenant_id,
        "scope": "ledger:reconcile",
        "aud": "ledger-reconcile",
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
    }
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"


async def setup_test_bindings():
    """配置测试企业、会话与绑定数据."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
        async with aconn.cursor() as cur:
            await cur.execute(
                "DELETE FROM mcp_identity_bindings WHERE tenant_id IN (%(ta)s, %(tb)s)",
                {"ta": TENANT_A, "tb": TENANT_B},
            )
            await cur.execute(
                "DELETE FROM auth_sessions WHERE user_id IN (%(u1)s, %(u2)s)",
                {"u1": USER_1, "u2": USER_2},
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

            await cur.execute(
                "INSERT INTO tenants (id, name, code, status, auth_revision) "
                "VALUES (%(id)s, %(name)s, 'corp-a-sel', 'active', 1)",
                {"id": TENANT_A, "name": "企业A"},
            )
            await cur.execute(
                "INSERT INTO tenants (id, name, code, status, auth_revision) "
                "VALUES (%(id)s, %(name)s, 'corp-b-sel', 'active', 1)",
                {"id": TENANT_B, "name": "企业B"},
            )
            await cur.execute(
                "INSERT INTO users (id, account, display_name, password_hash, is_active, auth_revision) "
                "VALUES (%(id)s, 'user1_sel', '用户1', 'fake', true, 1)",
                {"id": USER_1},
            )
            await cur.execute(
                "INSERT INTO users (id, account, display_name, password_hash, is_active, auth_revision) "
                "VALUES (%(id)s, 'user2_sel', '用户2', 'fake', true, 1)",
                {"id": USER_2},
            )

            await cur.execute(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id, status, revision) "
                "VALUES (%(id)s, %(tid)s, %(uid)s, 'active', 1)",
                {"id": MEMBERSHIP_A1, "tid": TENANT_A, "uid": USER_1},
            )
            await cur.execute(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id, status, revision) "
                "VALUES (%(id)s, %(tid)s, %(uid)s, 'active', 1)",
                {"id": MEMBERSHIP_B1, "tid": TENANT_B, "uid": USER_1},
            )

            # 插入有效活跃会话
            await cur.execute(
                "INSERT INTO auth_sessions (id, user_id, token_hash, active_tenant_id, ctx_version, expires_at) "
                "VALUES (%(sid)s, %(uid)s, 'fake-token-hash', %(tid)s, 1, now() + interval '1 day')",
                {"sid": SESSION_1, "uid": USER_1, "tid": TENANT_A},
            )

            # 企业A授权 controlled-one 与 controlled-two
            # 企业B仅授权 controlled-one
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, 'controlled-one', 'sub-a', 'env:GATE0_CONTROLLED_A', 'v1-fixture', 'active', 1)",
                {"t": TENANT_A, "m": MEMBERSHIP_A1},
            )
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, 'controlled-two', 'sub-a', 'env:GATE0_CONTROLLED_A', 'v1-fixture', 'active', 1)",
                {"t": TENANT_A, "m": MEMBERSHIP_A1},
            )
            await cur.execute(
                "INSERT INTO mcp_identity_bindings "
                "(tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status, auth_revision) "
                "VALUES (%(t)s, %(m)s, 'controlled-one', 'sub-b', 'env:GATE0_CONTROLLED_B', 'v1-fixture', 'active', 1)",
                {"t": TENANT_B, "m": MEMBERSHIP_B1},
            )
            await aconn.commit()


def test_task_2_1_skill_catalog_and_material_fingerprint():
    """Task 2.1: 本地 Skill 目录、材料指纹 (SHA-256)、稳定 ID 与防路径遍历测试."""
    # 1. 验证 basic-qa 技能目录发现
    skills = list_graph_skills("basic-qa")
    assert len(skills) >= 7
    slugs = [s.name for s in skills]
    assert "air-quality-basic-query" in slugs
    assert "comparison-composition" in slugs

    # 验证稳定 ID 格式与材料指纹
    s1 = next(s for s in skills if s.name == "air-quality-basic-query")
    assert s1.id == "skill:basic-qa:air-quality-basic-query"
    assert s1.material_version.startswith("sha256:")
    assert len(s1.material_version) == 7 + 64
    assert s1.status == "available"
    assert len(s1.description) <= 500

    # 2. 验证材料指纹的一致性
    skill_dir = Path(__file__).resolve().parents[1] / "src" / "basic_qa" / "skills" / "air-quality-basic-query"
    mv1 = calculate_skill_material_version(skill_dir)
    mv2 = calculate_skill_material_version(skill_dir)
    assert mv1 == mv2

    # 3. 验证未知图拒绝 (422)
    with pytest.raises(HTTPException) as exc:
        list_graph_skills("unknown-graph")
    assert exc.value.status_code == 422

    # 4. 验证防路径遍历与伪造
    with pytest.raises(HTTPException) as exc:
        validate_skill_selection("basic-qa", "skill:basic-qa:../../etc/passwd")
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        validate_skill_selection("basic-qa", "skill:other-graph:air-quality-basic-query")
    assert exc.value.status_code == 422

    # 5. 验证 data-analysis 菜单确定性
    da_skills = list_graph_skills("data-analysis", menu_name="CityHourBroadcast")
    assert len(da_skills) == 1
    assert da_skills[0].name == "broadcast-hour"

    # 若菜单不匹配，选择属于其它菜单的技能抛 422
    with pytest.raises(HTTPException) as exc:
        validate_skill_selection(
            "data-analysis",
            "skill:data-analysis:concentration-ranking",
            menu_name="CityHourBroadcast",
        )
    assert exc.value.status_code == 422
    assert "映射不一致" in str(exc.value.detail)


async def test_task_2_2_mcp_catalog_same_name_disambiguation():
    """Task 2.2: 当前身份 MCP 授权目录、同名工具消歧与零发现请求验证."""
    await setup_test_bindings()

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

    id_a1 = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=SESSION_1)
    id_b1 = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_B, membership_id=MEMBERSHIP_B1, session_id=SESSION_1)

    try:
        from common.capabilities import discover_authorized_mcp_catalog

        # 企业A授权了 controlled-one 和 controlled-two
        services_a, tools_a = await discover_authorized_mcp_catalog(id_a1)
        srv_keys_a = [s.server_key for s in services_a]
        assert "controlled-one" in srv_keys_a
        assert "controlled-two" in srv_keys_a

        tool_ids_a = [t.id for t in tools_a]
        assert "mcp:controlled-one:query" in tool_ids_a
        assert "mcp:controlled-two:query" in tool_ids_a

        # 企业B仅授权 controlled-one
        services_b, tools_b = await discover_authorized_mcp_catalog(id_b1)
        srv_keys_b = [s.server_key for s in services_b]
        assert "controlled-one" in srv_keys_b
        assert "controlled-two" not in srv_keys_b

        # 验证两服务同名 query 工具均能准确调用且不串扰
        res1 = await factory.call(id_a1, "controlled-one", "query", {"user_id": "x", "region": "杭州"})
        assert "from_query" in res1
        assert await srv_one.call_count(tool="query") == 1
        assert await srv_two.call_count(tool="query") == 0

        res2 = await factory.call(id_a1, "controlled-two", "query", {"user_id": "x", "region": "北京"})
        assert "from_query" in res2
        assert await srv_one.call_count(tool="query") == 1
        assert await srv_two.call_count(tool="query") == 1

        # 验证企业B对 controlled-two 未授权，调用直接拒绝且网络增量为 0
        with pytest.raises(McpAuthorizationError):
            await factory.call(id_b1, "controlled-two", "query", {"user_id": "x", "region": "上海"})
        assert await srv_two.call_count(tool="query") == 1
    finally:
        await srv_one.stop()
        await srv_two.stop()
        set_factory(None)


async def test_task_2_3_capabilities_pagination_and_tampered_cursor():
    """Task 2.3: 原生接口鉴权、分页游标校验、版本摘要与防篡改."""
    await setup_test_bindings()

    id_a1 = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=SESSION_1)

    # 测试分页：limit=2
    res_p1 = await get_capabilities_catalog(
        graph_id="basic-qa",
        identity=id_a1,
        limit=2,
    )
    assert res_p1["schema_version"] == "agent.capability-catalog.v1"
    assert len(res_p1["skills"]) == 2
    assert res_p1["next_cursor"] is not None

    # 用游标获取第二页
    res_p2 = await get_capabilities_catalog(
        graph_id="basic-qa",
        identity=id_a1,
        limit=2,
        cursor=res_p1["next_cursor"],
    )
    assert len(res_p2["skills"]) == 2
    # 保证两页无重复
    ids_p1 = {s["id"] for s in res_p1["skills"]}
    ids_p2 = {s["id"] for s in res_p2["skills"]}
    assert len(ids_p1.intersection(ids_p2)) == 0

    # 篡改游标签名测试 (409)
    cur_sig, cur_b64 = res_p1["next_cursor"].split(":", 1)
    tampered = f"bad{cur_sig[3:]}:{cur_b64}"
    with pytest.raises(HTTPException) as exc:
        await get_capabilities_catalog(
            graph_id="basic-qa",
            identity=id_a1,
            limit=2,
            cursor=tampered,
        )
    assert exc.value.status_code == 409
    assert "已被篡改" in str(exc.value.detail)

    # 畸形游标测试 (422)
    with pytest.raises(HTTPException) as exc:
        await get_capabilities_catalog(
            graph_id="basic-qa",
            identity=id_a1,
            limit=2,
            cursor="malformed_cursor_no_colon",
        )
    assert exc.value.status_code == 422

    # 跨用户/企业换 scope 游标测试 (409)
    other_id = RuntimeIdentity(user_id=USER_2, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=SESSION_1)
    with pytest.raises(HTTPException) as exc:
        await get_capabilities_catalog(
            graph_id="basic-qa",
            identity=other_id,
            limit=2,
            cursor=res_p1["next_cursor"],
        )
    assert exc.value.status_code == 409
    assert "不匹配" in str(exc.value.detail)


async def test_task_2_3_artifact_http_capabilities_endpoint():
    """Task 2.3: 测试 artifact_http.py 上的 /capabilities 原生端点集成鉴权."""
    await setup_test_bindings()

    from common.artifact_http import app

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    # 1. 匿名请求 -> 401
    resp_anon = await client.get("/capabilities?graph_id=basic-qa")
    assert resp_anon.status_code == 401

    # 2. 签发有效人类成员运行票据
    valid_ticket = make_user_ticket(
        user_id=USER_1,
        tenant_id=TENANT_A,
        membership_id=MEMBERSHIP_A1,
        session_id=SESSION_1,
    )

    resp_auth = await client.get(
        "/capabilities?graph_id=basic-qa",
        headers={"authorization": f"Bearer {valid_ticket}"},
    )
    assert resp_auth.status_code == 200
    data = resp_auth.json()
    assert data["schema_version"] == "agent.capability-catalog.v1"
    assert data["graph_id"] == "basic-qa"
    assert len(data["skills"]) >= 7

    # 3. 内部校准票据 (rs-v1) 访问 -> 403
    rec_ticket = make_reconcile_ticket(
        tenant_id=TENANT_A,
    )
    resp_rec = await client.get(
        "/capabilities?graph_id=basic-qa",
        headers={"authorization": f"Bearer {rec_ticket}"},
    )
    assert resp_rec.status_code == 403




async def test_task_2_4_metadata_cache_vs_auth_isolation():
    """Task 2.4: 元数据与授权隔离，修订与租户上下文隔离 (A->B->A 与实时撤销)."""
    await setup_test_bindings()

    srv_one = FixtureServer()
    url_one = await srv_one.start()
    factory = ScopedMCPToolFactory({"controlled-one": url_one})
    set_factory(factory)

    try:
        id_a = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=SESSION_1)
        id_b = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_B, membership_id=MEMBERSHIP_B1, session_id=SESSION_1)

        # 访问企业 A 目录
        cat_a1 = await get_capabilities_catalog(graph_id="basic-qa", identity=id_a)
        # 访问企业 B 目录
        cat_b = await get_capabilities_catalog(graph_id="basic-qa", identity=id_b)
        # 切回企业 A 目录 (A->B->A)
        cat_a2 = await get_capabilities_catalog(graph_id="basic-qa", identity=id_a)

        assert cat_a1["catalog_version"] == cat_a2["catalog_version"]
        assert cat_a1["catalog_version"] != cat_b["catalog_version"]

        # 实时撤销企业 A 的 controlled-one 绑定
        import psycopg

        async with await psycopg.AsyncConnection.connect(TEST_DB_URI) as aconn:
            async with aconn.cursor() as cur:
                await cur.execute(
                    "UPDATE mcp_identity_bindings SET status = 'disabled' WHERE tenant_id = %(t)s AND server_key = 'controlled-one'",
                    {"t": TENANT_A},
                )
                await aconn.commit()

        # 再次获取企业 A 目录：无正向 TTL 缓存，立即反映撤销
        cat_a3 = await get_capabilities_catalog(graph_id="basic-qa", identity=id_a)

        srv_keys_a3 = [s["server_key"] for s in cat_a3["mcp_services"]]
        assert "controlled-one" not in srv_keys_a3
        assert cat_a3["catalog_version"] != cat_a1["catalog_version"]
    finally:
        await srv_one.stop()
        set_factory(None)
        await close_identity_pool()






async def test_task_2_3_mcp_partial_failure_and_timeout():
    """Task 2.3: 部分服务不可用/超时测试，整体返回 200 且诚实标记 temporarily_unavailable."""
    await setup_test_bindings()

    srv_one = FixtureServer()
    url_one = await srv_one.start()

    # controlled-two 指向一个未运行/拒绝连接的端口
    factory = ScopedMCPToolFactory({
        "controlled-one": url_one,
        "controlled-two": "http://127.0.0.1:59999/mcp",
    })
    set_factory(factory)

    try:
        id_a = RuntimeIdentity(user_id=USER_1, tenant_id=TENANT_A, membership_id=MEMBERSHIP_A1, session_id=SESSION_1)
        # 单服务 2s / 总 5s 内返回，不抛 500
        cat = await get_capabilities_catalog(graph_id="basic-qa", identity=id_a)

        # 检查返回结构正常 (200)
        assert cat["schema_version"] == "agent.capability-catalog.v1"
        services = {s["server_key"]: s["status"] for s in cat["mcp_services"]}
        assert services["controlled-one"] == "available"
        assert services["controlled-two"] == "temporarily_unavailable"

        # 可用服务的工具正常展示
        tool_ids = [t["id"] for t in cat["tools"]]
        assert "mcp:controlled-one:query" in tool_ids
    finally:
        await srv_one.stop()
        set_factory(None)
        await close_identity_pool()
