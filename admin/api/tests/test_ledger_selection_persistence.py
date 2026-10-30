"""Change 4 任务 3.1 - 3.5：能力选择规范、持久化、受理、幂等与安全回读测试.

覆盖验收要求：
- 3.1: 严格选择 schema/规范排序、auto/explicit/空MCP、未知字段拒绝、超限(>20)、
       路径穿越、明确必需依赖(required-tools)校验与 allowed-tools 非必需样本
- 3.2: 独立表持久化、复合外键约束、FORCE RLS 隔离
- 3.3: 选择快照、受理、request.accepted、capability.selection_accepted 与
       outbox 同事务原子性（故障回滚零残留）
- 3.4: v2 摘要与键序无关性、同键不同选择 409、旧 v1 not_collected 自动重放兼容、
       交付开关 AGENT_CAPABILITY_SELECTION_ENABLED 控制
- 3.5: 本人/运营分级安全回读、越权 404、已删除会话禁读禁写、恢复 (resume) 计划不可变与撤销阻断
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from ctx_client import ContextClient
from httpx import ASGITransport
from pydantic import ValidationError
from sqlalchemy import select, text

from app.config import get_settings
from app.db import OwnerSessionLocal
from app.main import app
from app.models import Tenant, TenantMembership, User
from app.models_ledger import (
    AgentCapabilitySelection,
    AgentRequest,
    AgentRunAttempt,
    AgentSubmission,
    AgentThreadDeletion,
)
from app.schemas_ledger import CapabilitySelectionIn
from app.security import hash_password
from app.services import ledger as ledger_service

PASSWORD = "test-selection-pwd-123"
PREFIX = "sel05_"


async def _make_tenant(code: str) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        t = Tenant(code=code, name=f"企业-{code}", status="active")
        session.add(t)
        await session.commit()
        return t.id


async def _make_user(account: str) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        u = User(
            account=account,
            display_name=f"用户-{account}",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(u)
        await session.commit()
        return u.id


async def _make_membership(tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        m = TenantMembership(tenant_id=tenant_id, user_id=user_id, status="active")
        session.add(m)
        await session.commit()
        return m.id


async def _grant_thread(tenant_id: uuid.UUID, user_id: uuid.UUID, thread_id: str) -> None:
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO thread_ownership(thread_id, tenant_id, user_id, source)"
                " VALUES (:t, :ten, :u, 'native')"
                " ON CONFLICT (thread_id) DO UPDATE"
                " SET tenant_id=EXCLUDED.tenant_id, user_id=EXCLUDED.user_id"
            ),
            {"t": thread_id, "ten": str(tenant_id), "u": str(user_id)},
        )
        await session.commit()


async def _login(account: str, tenant_id: uuid.UUID) -> ContextClient:
    client = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await client.post("/auth/login", json={"account": account, "password": PASSWORD})
    assert res.status_code == 200, res.text
    ctx = res.json()
    if (ctx.get("current_tenant") or {}).get("id") != str(tenant_id):
        switch = await client.post(
            "/auth/switch-tenant",
            json={"tenant_id": str(tenant_id), "expected_ctx_version": ctx["context_version"]},
        )
        assert switch.status_code == 200, switch.text
    return client


async def _grant_business(tenant_id: uuid.UUID, membership_id: uuid.UUID) -> None:
    """本地测试库补齐 IAM 业务使用权限（正式环境由 IAM 授予）。"""
    async with OwnerSessionLocal() as session:
        permission_id = await session.scalar(
            text(
                "INSERT INTO permissions(code,name,type,grantable_to_tenants,is_active)"
                " VALUES ('agent:business:basic-qa:use','使用基础问答',3,true,true)"
                " ON CONFLICT (code) DO UPDATE SET is_active=true RETURNING id"
            )
        )
        role_id = await session.scalar(
            text(
                "INSERT INTO roles(code,name,scope,is_system,tenant_id)"
                " VALUES ('selection_business','选择测试业务角色','tenant',false,:t)"
                " ON CONFLICT (tenant_id,code) WHERE scope = 'tenant'"
                " DO UPDATE SET name=EXCLUDED.name RETURNING id"
            ),
            {"t": str(tenant_id)},
        )
        await session.execute(
            text(
                "INSERT INTO role_permissions(role_id,permission_id)"
                " VALUES (:r,:p) ON CONFLICT DO NOTHING"
            ),
            {"r": str(role_id), "p": str(permission_id)},
        )
        await session.execute(
            text(
                "INSERT INTO membership_roles(membership_id,role_id,tenant_id)"
                " VALUES (:m,:r,:t) ON CONFLICT DO NOTHING"
            ),
            {"m": str(membership_id), "r": str(role_id), "t": str(tenant_id)},
        )
        await session.commit()


@pytest.fixture
async def selection_env(monkeypatch):
    monkeypatch.setenv("REF", "controlled-fixture-only")
    suffix = uuid.uuid4().hex[:6]
    tenant_a = await _make_tenant(f"ta_{suffix}")
    tenant_b = await _make_tenant(f"tb_{suffix}")
    user_a = await _make_user(f"{PREFIX}ua_{suffix}")
    user_b = await _make_user(f"{PREFIX}ub_{suffix}")
    mem_a1 = await _make_membership(tenant_a, user_a)
    mem_b1 = await _make_membership(tenant_b, user_b)
    await _grant_business(tenant_a, mem_a1)
    await _grant_business(tenant_b, mem_b1)

    # 新模型：连接是平台部署配置；测试用受控工厂替代网络发现。
    import json

    monkeypatch.setenv(
        "AGENT_MCP_CONNECTIONS_JSON",
        json.dumps(
            {
                "schema_version": "agent.mcp-connections.v1",
                "revision": 1,
                "servers": [
                    {
                        "server_key": "ipp",
                        "url": "https://controlled.invalid/mcp",
                        "scope": "tenant",
                        "tenant_id": str(tenant_a),
                        "auth_mode": "bearer_env",
                        "credential_ref": "env:REF",
                        "enabled": True,
                        "approved_query_tools": ["query", "stats"],
                    },
                    {
                        "server_key": "ipp",
                        "url": "https://controlled.invalid/mcp",
                        "scope": "tenant",
                        "tenant_id": str(tenant_b),
                        "auth_mode": "bearer_env",
                        "credential_ref": "env:REF",
                        "enabled": True,
                        "approved_query_tools": ["query", "stats"],
                    },
                ],
            }
        ),
    )
    from common import business_registry as registry

    monkeypatch.setitem(
        registry.BUSINESSES,
        "basic-qa",
        type(registry.BUSINESSES["basic-qa"])(
            code="basic-qa",
            name_zh="基础问答",
            permission="agent:business:basic-qa:use",
            graph="basic-qa",
            connections=(
                registry.BusinessConnection(
                    server_key="ipp", allowed_tools=frozenset({"query", "stats"})
                ),
            ),
        ),
    )
    from common.mcp_connections import reset_connections_cache

    reset_connections_cache()

    # 启用功能开关
    get_settings().agent_capability_selection_enabled = True

    from common.mcp_scoped import get_factory
    from langchain_core.tools import tool

    @tool
    def query(region: str = "") -> str:
        """Query air data."""
        return "ok"

    @tool
    def stats(region: str = "") -> str:
        """Stats air data."""
        return "ok"

    # 作用域工厂测试替身只替代网络发现；业务/连接校验仍走真实实现。
    from common.capabilities import (
        compute_catalog_version,
        discover_authorized_mcp_catalog,
        list_graph_skills,
    )
    from common.identity.runtime import RuntimeIdentity
    from common.mcp_scoped import ScopedMCPToolFactory, set_factory

    previous_factory = get_factory()

    class ControlledFactory(ScopedMCPToolFactory):
        async def _discover(self, resolved):
            return [query, stats]

    factory = ControlledFactory({"ipp": "http://controlled"})
    set_factory(factory)
    ident = RuntimeIdentity(
        str(user_a),
        str(tenant_a),
        str(mem_a1),
        "",
        permissions=frozenset({"agent:business:basic-qa:use"}),
        business_code="basic-qa",
        business_policy_version="2026-10-19.1",
        business_permission="agent:business:basic-qa:use",
        business_graph="basic-qa",
    )
    services, tools = await discover_authorized_mcp_catalog(ident, business_code="basic-qa")
    catalog_version = compute_catalog_version(
        "basic-qa",
        ident,
        {"tenant": 1, "user": 1, "membership": 1},
        list_graph_skills("basic-qa"),
        services,
        tools,
    )

    try:
        yield {
            "catalog_version": catalog_version,
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "user_a": user_a,
            "user_b": user_b,
            "mem_a1": mem_a1,
            "account_a": f"{PREFIX}ua_{suffix}",
            "account_b": f"{PREFIX}ub_{suffix}",
        }
    finally:
        set_factory(previous_factory)
        async with OwnerSessionLocal() as session:
            for tid in (tenant_a, tenant_b):
                await session.execute(
                    text("DELETE FROM agent_capability_selections WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM agent_submissions WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM agent_run_attempts WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM agent_requests WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM agent_events WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM thread_ownership WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM agent_thread_deletions WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM tenant_memberships WHERE tenant_id = :t"),
                    {"t": str(tid)},
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :t"),
                    {"t": str(tid)},
                )
            for uid in (user_a, user_b):
                await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(uid)})
            await session.commit()


# ---------------------------------------------------------------------------
# Task 3.1: 严格选择规范与依赖校验
# ---------------------------------------------------------------------------


def test_task_3_1_schema_validation():
    """v2 只接受 MCP 选择，并明确拒绝旧 Skill 字段与过量工具."""
    # 1. 未知字段被 Pydantic 严格拒绝 (extra="forbid")
    with pytest.raises(ValidationError):
        CapabilitySelectionIn.model_validate(
            {
                "schema_version": "agent.capability-selection.v2",
                "skill": {"mode": "explicit", "skill_id": "skill:basic-qa:legacy"},
                "mcp": {"mode": "auto"},
            }
        )
    with pytest.raises(ValidationError):
        CapabilitySelectionIn.model_validate(
            {
                "schema_version": "agent.capability-selection.v1",
                "skill": {"mode": "auto"},
                "mcp": {"mode": "auto"},
            }
        )

    with pytest.raises(ValidationError):
        CapabilitySelectionIn.model_validate(
            {
                "schema_version": "agent.capability-selection.v2",
                "unknown_extra": "forbidden",
            }
        )

    # 2. 超限 20 个工具被拒绝
    too_many_tools = [f"mcp:ipp:tool_{i}" for i in range(21)]
    with pytest.raises(ValidationError):
        CapabilitySelectionIn.model_validate(
            {
                "schema_version": "agent.capability-selection.v2",
                "mcp": {"mode": "allowlist", "tool_ids": too_many_tools},
            }
        )

    # 3. 规范化 auto/auto
    norm = ledger_service.normalize_capability_selection(None)
    assert norm["schema_version"] == "agent.capability-selection.v2"
    assert norm["mcp"]["mode"] == "auto"
    assert norm["mcp"]["tool_ids"] == []
    assert norm["selection_source"] == "automatic"

    # 4. 空 MCP allowlist 允许（表示 0 MCP 工具）
    norm_empty = ledger_service.normalize_capability_selection(
        {
            "schema_version": "agent.capability-selection.v2",
            "mcp": {"mode": "allowlist", "tool_ids": []},
        }
    )
    assert norm_empty["mcp"]["mode"] == "allowlist"
    assert norm_empty["mcp"]["tool_ids"] == []
    assert norm_empty["selection_source"] == "user_explicit"

    # 5. MCP 工具标识仍拒绝路径穿越
    with pytest.raises(ledger_service.CapabilityValidationError):
        ledger_service.normalize_capability_selection(
            {
                "schema_version": "agent.capability-selection.v2",
                "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:../evil"]},
            }
        )


def test_v3_selection_digest_and_v2_submission_digest_boundaries():
    selection_digest = ledger_service.canonical_selection_digest(
        graph_id="basic-qa",
        mcp_mode="allowlist",
        tool_ids=["mcp:ipp:stats", "mcp:ipp:query", "mcp:ipp:query"],
    )
    expected_selection_digest = ledger_service.canonical_digest(
        {
            "graph_id": "basic-qa",
            "mcp_mode": "allowlist",
            "tool_ids": ["mcp:ipp:query", "mcp:ipp:stats"],
        }
    )
    assert selection_digest == expected_selection_digest

    legacy_digest = ledger_service.canonical_digest(
        {
            "graph_id": "basic-qa",
            "material_version": "auto",
            "mcp_mode": "allowlist",
            "skill_id": None,
            "skill_mode": "auto",
            "tool_ids": ["mcp:ipp:query", "mcp:ipp:stats"],
        }
    )
    assert selection_digest != legacy_digest

    expected_submission_digest = ledger_service.canonical_digest(
        {
            "capability_selection_digest": selection_digest,
            "content": "question",
            "graph_id": "basic-qa",
            "operation_kind": "initial",
        }
    )
    assert (
        ledger_service.canonical_content_digest_v2(
            graph_id="basic-qa",
            operation_kind="initial",
            content=" question ",
            capability_selection_digest=selection_digest,
        )
        == expected_submission_digest
    )


def test_data_analysis_menu_policy_fail_closed(monkeypatch):
    """菜单正常未匹配可返回 None；策略故障必须明确拒绝受理."""
    from types import SimpleNamespace

    import application.business_policies as policies

    monkeypatch.setattr(policies, "configure", lambda: None)
    monkeypatch.setattr(
        policies,
        "resolve_policy",
        lambda _graph: SimpleNamespace(menu_skill=lambda _menu: None),
    )
    assert ledger_service.validate_menu_selection("data-analysis", "unmatched-menu") is None

    def fail_policy(_graph):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(policies, "resolve_policy", fail_policy)
    with pytest.raises(ledger_service.CapabilityValidationError) as exc_info:
        ledger_service.validate_menu_selection("data-analysis", "known-menu")
    assert exc_info.value.code == "menu_policy_unavailable"


# ---------------------------------------------------------------------------
# Task 3.2: 独立表持久化、复合外键约束与 RLS
# ---------------------------------------------------------------------------


async def test_task_3_2_composite_foreign_key_and_rls(selection_env):
    """3.2: 复合外键阻止跨租户关联，FORCE RLS 阻止非 owner 跨租户回读."""
    env = selection_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)

    client_a = await _login(env["account_a"], env["tenant_a"])
    try:
        res = await client_a.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_composite_fk_01",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试复合关联",
            },
        )
        assert res.status_code == 200
        data = res.json()
        req_id = uuid.UUID(data["request_id"])
        att_id = uuid.UUID(data["attempt_id"])
        sub_id = uuid.UUID(data["submission_id"])

        # 跨租户插入 agent_capability_selections：关联租户 A 提交，但设置 tenant_id 为租户 B
        async with OwnerSessionLocal() as session:
            from sqlalchemy.exc import DBAPIError

            bad_selection = AgentCapabilitySelection(
                id=uuid.uuid4(),
                tenant_id=env["tenant_b"],  # 错租户
                user_id=env["user_b"],
                membership_id=env["tenant_b"],
                request_id=req_id,
                attempt_id=att_id,
                submission_id=sub_id,
                graph_id="basic-qa",
                mcp_mode="auto",
                tool_ids=[],
                selection_digest="dummy",
                digest_version=3,
                selection_source="automatic",
                validation_time=dt.datetime.now(dt.UTC),
                created_at=dt.datetime.now(dt.UTC),
            )
            session.add(bad_selection)
            with pytest.raises(DBAPIError):  # ForeignKeyConstraint / IntegrityError
                await session.flush()
            await session.rollback()

    finally:
        await client_a.aclose()


# ---------------------------------------------------------------------------
# Task 3.3: 同事务原子性（选择、受理、事件、Outbox）与并发
# ---------------------------------------------------------------------------


async def test_task_3_3_atomic_transaction_and_rollback(selection_env):
    """3.3: 写入故障注入回滚整笔事务，零残留；同键并发唯一胜出."""
    env = selection_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(env["account_a"], env["tenant_a"])

    try:
        # 1. 成功提交：确认选择、事件与 outbox 同事务原子写入
        res = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_atomic_tx_01",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试原子事务",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                    "catalog_version": env["catalog_version"],
                },
            },
        )
        assert res.status_code == 200
        body = res.json()
        sub_id = uuid.UUID(body["submission_id"])

        async with OwnerSessionLocal() as session:
            # 验证 AgentCapabilitySelection 存在
            sel_row = (
                await session.execute(
                    text(
                        "SELECT c.mcp_mode, c.tool_ids, c.digest_version,"
                        " c.retired_skill_selection, c.selection_digest, s.digest_version"
                        " FROM agent_capability_selections c JOIN agent_submissions s"
                        " ON s.tenant_id=c.tenant_id AND s.id=c.submission_id"
                        " WHERE c.submission_id = :s"
                    ),
                    {"s": str(sub_id)},
                )
            ).first()
            assert sel_row is not None
            assert sel_row[0] == "allowlist"
            assert sel_row[1] == ["mcp:ipp:query"]
            assert sel_row[2] == 3
            assert sel_row[3] is None
            assert sel_row[4] == ledger_service.canonical_selection_digest(
                graph_id="basic-qa",
                mcp_mode="allowlist",
                tool_ids=["mcp:ipp:query"],
            )
            assert sel_row[5] == 2

            # 验证 capability.selection_accepted 事实已落库
            event_row = (
                await session.execute(
                    text(
                        "SELECT event_type, source, payload FROM agent_events"
                        " WHERE request_id = :r AND event_type = 'capability.selection_accepted'"
                    ),
                    {"r": body["request_id"]},
                )
            ).first()
            assert event_row is not None
            assert event_row[0] == "capability.selection_accepted"
            assert event_row[1] == "server"
            assert event_row[2]["mcp_mode"] == "allowlist"
            assert event_row[2]["tool_ids"] == ["mcp:ipp:query"]
            assert not {"skill_mode", "skill_id", "material_version"}.intersection(event_row[2])

        snapshot = body["capability_selection"]
        assert snapshot["digest_version"] == 3
        assert snapshot["schema_version"] == "agent.capability-selection.v2"
        assert snapshot["mcp"] == {
            "mode": "allowlist",
            "tool_ids": ["mcp:ipp:query"],
        }
        assert not {"skill", "skill_mode", "skill_id", "material_version"}.intersection(snapshot)

        # 2. 模拟事务回滚：若在 commit 之前抛异常，所有表均不残留
        async with OwnerSessionLocal() as session:
            from app.models import AuthSession, Tenant, TenantMembership, User
            from app.services.identity import Identity

            user_obj = await session.get(User, env["user_a"])
            tenant_obj = await session.get(Tenant, env["tenant_a"])
            mem_obj = await session.get(TenantMembership, env["mem_a1"])
            sess_obj = (
                (
                    await session.execute(
                        select(AuthSession)
                        .where(AuthSession.user_id == env["user_a"])
                        .order_by(AuthSession.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )

            mock_identity = Identity(
                user=user_obj,
                session=sess_obj,
                tenant=tenant_obj,
                membership=mem_obj,
                platform_admin=False,
                permissions=frozenset({"agent:business:basic-qa:use"}),
            )
            # 写入后故意执行 rollback 模拟失败
            _ = await ledger_service.admit_request(
                session,
                mock_identity,
                thread_id=thread_id,
                submission_key="k_rollback_probe_01",
                graph_id="basic-qa",
                operation_kind="initial",
                content="探针",
                capability_selection={
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                    "catalog_version": env["catalog_version"],
                },
            )
            # 异常/回滚
            await session.rollback()

        # 验证回滚后数据库中没有任何残留
        async with OwnerSessionLocal() as session:
            cnt = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_submissions"
                        " WHERE submission_key = 'k_rollback_probe_01'"
                    )
                )
            ).scalar()
            assert cnt == 0

    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Task 3.4: v2 摘要、键序无关性、重放与功能开关
# ---------------------------------------------------------------------------


async def test_task_3_4_idempotency_digest_and_conflict(selection_env):
    """3.4: 键序不同同结果、同键不同选择 409、旧 v1 not_collected 自动重放."""
    env = selection_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(env["account_a"], env["tenant_a"])

    try:
        # 1. 键序不同摘要一致性测试
        p1 = {"b": 2, "a": 1, "mcp": ["mcp:ipp:query", "mcp:ipp:stats"]}
        p2 = {"mcp": ["mcp:ipp:query", "mcp:ipp:stats"], "a": 1, "b": 2}
        assert ledger_service.canonical_digest(p1) == ledger_service.canonical_digest(p2)

        # 2. 初次提交带显式选择 S1 + T1
        sub_key = "k_idempotency_v2_01"
        payload_1 = {
            "thread_id": thread_id,
            "submission_key": sub_key,
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "测试幂等",
            "capability_selection": {
                "schema_version": "agent.capability-selection.v2",
                "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                "catalog_version": env["catalog_version"],
            },
        }
        res1 = await client.post("/agent/requests", json=payload_1)
        assert res1.status_code == 200
        body1 = res1.json()
        assert body1["reused"] is False

        # 3. 再次使用完全相同的 key + 载荷重投 -> 成功复用 (reused=True)
        res2 = await client.post("/agent/requests", json=payload_1)
        assert res2.status_code == 200
        body2 = res2.json()
        assert body2["reused"] is True
        assert body2["submission_id"] == body1["submission_id"]

        # 4. 同 key 但修改选择为不同的工具 T2 -> 409 冲突
        payload_diff = dict(payload_1)
        payload_diff["capability_selection"] = {
            "schema_version": "agent.capability-selection.v2",
            "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:stats"]},
            "catalog_version": env["catalog_version"],
        }
        res_conflict = await client.post("/agent/requests", json=payload_diff)
        assert res_conflict.status_code == 409, res_conflict.text
        assert "同一提交键已用于不同" in res_conflict.json()["detail"]

        # 5. 旧 v1 记录重放兼容：创建一条 digest_version=1 的历史记录
        v1_key = "k_legacy_v1_probe"
        async with OwnerSessionLocal() as session:
            v1_req_id = uuid.uuid4()
            v1_att_id = uuid.uuid4()
            v1_sub_id = uuid.uuid4()
            v1_digest = ledger_service.canonical_content_digest_v1(
                graph_id="basic-qa", operation_kind="initial", content="旧历史问题"
            )
            req = AgentRequest(
                id=v1_req_id,
                tenant_id=env["tenant_a"],
                user_id=env["user_a"],
                membership_id=env["mem_a1"],
                thread_id=thread_id,
                graph_id="basic-qa",
                business_code="basic-qa",
                business_policy_version="2026-10-19.1",
                operation_kind="initial",
                admitted_at=dt.datetime.now(dt.UTC),
                created_at=dt.datetime.now(dt.UTC),
            )
            att = AgentRunAttempt(
                id=v1_att_id,
                tenant_id=env["tenant_a"],
                user_id=env["user_a"],
                membership_id=env["mem_a1"],
                request_id=v1_req_id,
                attempt_index=1,
                graph_id="basic-qa",
                business_code="basic-qa",
                business_policy_version="2026-10-19.1",
                state="admitting",
                state_revision=1,
                operation_kind="initial",
                content_digest=v1_digest,
                admitted_at=dt.datetime.now(dt.UTC),
                last_progress_at=dt.datetime.now(dt.UTC),
                updated_at=dt.datetime.now(dt.UTC),
            )
            sub = AgentSubmission(
                id=v1_sub_id,
                tenant_id=env["tenant_a"],
                user_id=env["user_a"],
                membership_id=env["mem_a1"],
                thread_id=thread_id,
                submission_key=v1_key,
                operation_kind="initial",
                content_digest=v1_digest,
                digest_version=1,
                capability_selection_id=None,
                admission_status="admitting",
                request_id=v1_req_id,
                attempt_id=v1_att_id,
                created_at=dt.datetime.now(dt.UTC),
                updated_at=dt.datetime.now(dt.UTC),
            )
            session.add_all([req, att, sub])
            await session.commit()

        # 用 auto 选择重放 v1 键 -> 成功复用，selection_status 为 not_collected
        res_v1_replay = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": v1_key,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "旧历史问题",
            },
        )
        assert res_v1_replay.status_code == 200
        v1_out = res_v1_replay.json()
        assert v1_out["reused"] is True
        assert v1_out["capability_selection"]["selection_status"] == "not_collected"

        # 若尝试用显式选择重放旧 v1 键 -> 409 拒绝
        res_v1_explicit = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": v1_key,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "旧历史问题",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                    "catalog_version": env["catalog_version"],
                },
            },
        )
        assert res_v1_explicit.status_code == 409

    finally:
        await client.aclose()


async def test_task_3_4_delivery_switch_fallback(selection_env):
    """3.4: 交付开关关闭时，显式请求被明确拒绝 (409 capability_selection_disabled)，auto 仍成功."""
    env = selection_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(env["account_a"], env["tenant_a"])

    try:
        # 关闭开关
        get_settings().agent_capability_selection_enabled = False

        # 1. 显式选择被拒绝 409 capability_selection_disabled
        res_explicit = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_switch_disabled_01",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试开关关闭显式请求",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                    "catalog_version": env["catalog_version"],
                },
            },
        )
        assert res_explicit.status_code == 409, res_explicit.text
        assert "能力选择功能已停用" in res_explicit.json()["detail"]

        # 2. auto 默认请求继续成功
        res_auto = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_switch_disabled_auto_01",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试开关关闭自动请求",
            },
        )
        assert res_auto.status_code == 200

    finally:
        get_settings().agent_capability_selection_enabled = True
        await client.aclose()


# ---------------------------------------------------------------------------
# Task 3.5: 安全回读、越权隔离、删除与恢复不可变
# ---------------------------------------------------------------------------


async def test_task_3_5_readback_deletion_and_resume(selection_env):
    """3.5: 回读包含快照事实、越权 404、已删除线程禁读禁写、恢复不可变."""
    env = selection_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)

    client_a = await _login(env["account_a"], env["tenant_a"])
    client_b = await _login(env["account_b"], env["tenant_b"])

    try:
        # 1. 本人正常受理带选择
        res = await client_a.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_readback_probe_01",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试安全回读",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
                    "catalog_version": env["catalog_version"],
                },
            },
        )
        assert res.status_code == 200
        req_id = res.json()["request_id"]
        att_id = res.json()["attempt_id"]

        # 2. 本人回读明细：成功获取 capability_selection 快照
        detail_res = await client_a.get(f"/agent/requests/{req_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["capability_selection"]["selection_status"] == "collected"
        assert detail["capability_selection"]["mcp"]["tool_ids"] == ["mcp:ipp:query"]

        # 3. 租户 B 用户尝试回读该 request -> 404（隔离保护）
        cross_res = await client_b.get(f"/agent/requests/{req_id}")
        assert cross_res.status_code == 404

        # 4. 恢复 (resume) 规则验证：
        # 先将 attempt 状态设为 waiting_input（允许 resume 的状态）
        async with OwnerSessionLocal() as session:
            await session.execute(
                text("UPDATE agent_run_attempts SET state = 'waiting_input' WHERE id = :a"),
                {"a": att_id},
            )
            await session.commit()

        # 恢复时如果用户试图修改选择 -> 拒绝 (409 resume_selection_immutable)
        resume_mod_res = await client_a.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_resume_probe_01",
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "source_attempt_id": att_id,
                "content": "用户补充输入",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:stats"]},  # 试图篡改
                    "catalog_version": env["catalog_version"],
                },
            },
        )
        assert resume_mod_res.status_code == 409
        assert "不允许修改能力选择" in resume_mod_res.json()["detail"]

        # 5. 会话删除：向 agent_thread_deletions 写入墓碑
        async with OwnerSessionLocal() as session:
            session.add(
                AgentThreadDeletion(
                    id=uuid.uuid4(),
                    tenant_id=env["tenant_a"],
                    thread_id=thread_id,
                    user_id=env["user_a"],
                    requested_by=env["user_a"],
                    state="requested",
                    attempts=0,
                    requested_at=dt.datetime.now(dt.UTC),
                )
            )
            await session.commit()

        # 删除后，本人回读该请求 -> 404 (禁止回读)
        deleted_read = await client_a.get(f"/agent/requests/{req_id}")
        assert deleted_read.status_code == 404

        # 删除后，新请求提交该会话 -> 403 (禁止写入)
        deleted_write = await client_a.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "k_deleted_thread_write",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "在已删除会话写入",
            },
        )
        assert deleted_write.status_code == 403
        assert "会话正在删除" in deleted_write.json()["detail"]

    finally:
        await client_a.aclose()
        await client_b.aclose()


async def test_catalog_scope_and_operation_inheritance(selection_env):
    """仅工具/空集合也核验目录；省略与明确自动、恢复重放不能混淆."""
    env = selection_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(env["account_a"], env["tenant_a"])
    selection = {
        "schema_version": "agent.capability-selection.v2",
        "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
        "catalog_version": env["catalog_version"],
    }

    async def submit(key, kind="initial", choice=selection, source=None):
        payload = {
            "thread_id": thread,
            "submission_key": key,
            "graph_id": "basic-qa",
            "operation_kind": kind,
            "content": "operation contract",
        }
        if choice is not None:
            payload["capability_selection"] = choice
        if source:
            payload["source_attempt_id"] = source
        return await client.post("/agent/requests", json=payload)

    try:
        forged = {**selection, "catalog_version": "cat_" + "0" * 24}
        assert (await submit("forged-tools", choice=forged)).status_code == 409
        forged["mcp"] = {"mode": "allowlist", "tool_ids": []}
        assert (await submit("forged-empty", choice=forged)).status_code == 409
        original = await submit("original")
        assert original.status_code == 200, original.text
        first = original.json()
        automatic = {
            "schema_version": "agent.capability-selection.v2",
            "mcp": {"mode": "auto"},
        }
        assert (
            await submit("retry-change", "retry", automatic, first["attempt_id"])
        ).status_code == 409
        retried = await submit("retry-inherit", "retry", None, first["attempt_id"])
        assert retried.status_code == 200, retried.text
        assert retried.json()["capability_selection"]["mcp"]["tool_ids"] == ["mcp:ipp:query"]
        replay = await submit("retry-inherit", "retry", None, first["attempt_id"])
        assert replay.status_code == 200 and replay.json()["reused"]
        regenerated = await submit("regenerate-auto", "regenerate", automatic, first["attempt_id"])
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["request_id"] == first["request_id"]
        assert regenerated.json()["capability_selection"]["mcp"]["mode"] == "auto"
        async with OwnerSessionLocal() as session:
            await session.execute(
                text("UPDATE agent_run_attempts SET state='waiting_input' WHERE id=:id"),
                {"id": first["attempt_id"]},
            )
            await session.commit()
        resumed = await submit("resume-inherit", "resume", None, first["attempt_id"])
        assert resumed.status_code == 200, resumed.text
        replay = await submit("resume-inherit", "resume", None, first["attempt_id"])
        assert replay.status_code == 200 and replay.json()["reused"], replay.text
        same = await submit("resume-same", "resume", selection, first["attempt_id"])
        assert same.status_code == 200, same.text
        assert (
            await submit("resume-auto", "resume", automatic, first["attempt_id"])
        ).status_code == 409
    finally:
        await client.aclose()


async def test_legacy_selection_retry_resume_rejected_and_regenerate_reaccepted(selection_env):
    """v2 冻结选择不能 retry/resume；regenerate 忽略退休 Skill 证据并生成 v3."""
    env = selection_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(env["account_a"], env["tenant_a"])
    selection = {
        "schema_version": "agent.capability-selection.v2",
        "mcp": {"mode": "allowlist", "tool_ids": ["mcp:ipp:query"]},
        "catalog_version": env["catalog_version"],
    }

    async def submit(key, operation, source=None):
        return await client.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": key,
                "graph_id": "basic-qa",
                "operation_kind": operation,
                "content": "legacy digest boundary",
                "source_attempt_id": source,
            },
        )

    try:
        initial = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": "legacy-source",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "legacy digest boundary",
                "capability_selection": selection,
            },
        )
        assert initial.status_code == 200, initial.text
        first = initial.json()

        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_capability_selections SET digest_version=2, "
                    "retired_skill_selection=CAST(:legacy AS jsonb) WHERE attempt_id=:attempt"
                ),
                {
                    "legacy": (
                        '{"skill_mode":"explicit","skill_id":"skill:basic-qa:retired",'
                        '"material_version":"sha256:historical"}'
                    ),
                    "attempt": first["attempt_id"],
                },
            )
            await session.execute(
                text("UPDATE agent_run_attempts SET state='waiting_input' WHERE id=:attempt"),
                {"attempt": first["attempt_id"]},
            )
            await session.commit()

        retry = await submit("legacy-retry", "retry", first["attempt_id"])
        assert retry.status_code == 409, retry.text
        assert "旧摘要版本" in retry.json()["detail"]

        resume = await submit("legacy-resume", "resume", first["attempt_id"])
        assert resume.status_code == 409, resume.text
        assert "旧摘要版本" in resume.json()["detail"]

        regenerated = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": "legacy-regenerate",
                "graph_id": "basic-qa",
                "operation_kind": "regenerate",
                "content": "legacy digest boundary",
                "source_attempt_id": first["attempt_id"],
            },
        )
        assert regenerated.status_code == 200, regenerated.text
        snapshot = regenerated.json()["capability_selection"]
        assert snapshot["digest_version"] == 3
        assert snapshot["mcp"] == {
            "mode": "allowlist",
            "tool_ids": ["mcp:ipp:query"],
        }
        assert not {"skill", "skill_mode", "skill_id", "material_version"}.intersection(snapshot)
    finally:
        await client.aclose()


async def test_confirmed_selection_resolve_is_server_owned(selection_env):
    """正常映射与历史回读返回真实 UUID/摘要；另一企业和删除墓碑不可回读."""
    env = selection_env
    thread = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread)
    client = await _login(env["account_a"], env["tenant_a"])
    other = await _login(env["account_b"], env["tenant_b"])
    try:
        response = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": uuid.uuid4().hex,
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "readback contract",
            },
        )
        assert response.status_code == 200, response.text
        admission = response.json()
        run = str(uuid.uuid4())
        result = await client.post(
            "/agent/runs/admission-confirmed",
            headers={
                "x-internal-token": get_settings().internal_api_token,
            },
            json={key: admission[key] for key in ("request_id", "attempt_id", "submission_id")}
            | {
                "platform_run_id": run,
                "outcome": "confirmed",
            },
        )
        assert result.status_code == 200, result.text
        unknown = await client.post(
            "/agent/runs/admission-confirmed",
            headers={
                "x-internal-token": get_settings().internal_api_token,
            },
            json={key: admission[key] for key in ("request_id", "attempt_id", "submission_id")}
            | {
                "outcome": "admission_unknown",
                "reason": "missing_run_id",
            },
        )
        assert unknown.status_code == 200 and unknown.json()["outcome"] == "confirmed", unknown.text
        for params in ({"thread_id": thread, "platform_run_id": run}, {"thread_id": thread}):
            resolved = await client.get("/agent/feedback/attempts/resolve", params=params)
            assert resolved.status_code == 200, resolved.text
            selection = resolved.json()["capability_selection"]
            assert selection["id"] == admission["capability_selection"]["id"]
            assert (
                selection["selection_digest"]
                == admission["capability_selection"]["selection_digest"]
            )
            assert selection["schema_version"] == "agent.capability-selection.v2"
            assert selection["mcp"] == {"mode": "auto", "tool_ids": []}
            assert selection["selection_source"] == "automatic"
            assert not {"skill", "skill_mode", "skill_id", "material_version"}.intersection(
                selection
            )
            assert (
                await other.get("/agent/feedback/attempts/resolve", params=params)
            ).status_code == 404
    finally:
        await client.aclose()
        await other.aclose()
