"""独立复测探针：真实服务端受理与声明裁决（R01 / R04 / R07）."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from pathlib import Path

from httpx import ASGITransport
from sqlalchemy import text

from app.config import get_settings
from app.db import OwnerSessionLocal
from app.main import app
from app.models import Tenant, TenantMembership, User
from app.security import hash_password
from app.services import ledger as ledger_service
from tests.ctx_client import ContextClient


PASSWORD = "probe_live_password_123"
PREFIX = "probe_live_"


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


async def run_probe():
    suffix = uuid.uuid4().hex[:6]
    thread_id = str(uuid.uuid4())
    get_settings().agent_capability_selection_enabled = True

    tenant_a = await _make_tenant(f"probe_t_{suffix}")
    user_a = await _make_user(f"{PREFIX}{suffix}")
    mem_a1 = await _make_membership(tenant_a, user_a)
    await _grant_thread(tenant_a, user_a, thread_id)

    async with OwnerSessionLocal() as session:
        # 插入受控 MCP 绑定 (已核验协议 v1)
        await session.execute(
            text(
                "INSERT INTO mcp_identity_bindings ("
                " tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status)"
                " VALUES (:t, :m, 'business', 'sub_probe', 'env:REF', 'v1', 'active')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": str(tenant_a), "m": str(mem_a1)},
        )
        # 插入未核验协议绑定 (unverified)
        await session.execute(
            text(
                "INSERT INTO mcp_identity_bindings ("
                " tenant_id, membership_id, server_key, external_subject, credential_ref, protocol_version, status)"
                " VALUES (:t, :m, 'unverified_svc', 'sub_unv', 'env:REF', 'unverified', 'active')"
                " ON CONFLICT DO NOTHING"
            ),
            {"t": str(tenant_a), "m": str(mem_a1)},
        )
        await session.commit()

    account = f"{PREFIX}{suffix}"
    client = await _login(account, tenant_a)

    results = {}

    try:
        # R04: 1. 未核验协议绑定在显式选择时被 403 拒绝
        unv_res = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": f"key_unv_{suffix}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试未核验协议拒绝",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "auto"},
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:unverified_svc:query"]},
                    "catalog_version": "v1",
                },
            },
        )
        results["unverified_protocol_rejected"] = {
            "status_code": unv_res.status_code,
            "rejected_with_403": unv_res.status_code == 403,
        }

        # R04: 2. 伪造目录版本被 422 拒绝
        forged_cat_res = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": f"key_forged_{suffix}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "测试伪造目录版本",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "explicit", "skill_id": "skill:basic-qa:air-quality-basic-query"},
                    "mcp": {"mode": "allowlist", "tool_ids": []},
                    "catalog_version": "not-a-real-catalog-123",
                },
            },
        )
        results["forged_catalog_rejected"] = {
            "status_code": forged_cat_res.status_code,
            "rejected_with_422": forged_cat_res.status_code == 422,
        }

        # R01: 3. 正常受理并带有 v2 摘要快照
        normal_res = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": f"key_normal_{suffix}",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "正常显式提问",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "explicit", "skill_id": "skill:basic-qa:air-quality-basic-query"},
                    "mcp": {"mode": "allowlist", "tool_ids": ["mcp:business:query"]},
                    "catalog_version": "v1",
                },
            },
        )
        normal_data = normal_res.json()
        sub_id = normal_data["submission_id"]
        att_id = normal_data["attempt_id"]

        results["normal_admission_v2_digest"] = {
            "status_code": normal_res.status_code,
            "has_selection": "capability_selection" in normal_data,
            "digest_version": normal_data["capability_selection"].get("digest_version"),
        }

        # R01: 4. claim_start 使用 v2 摘要进行校验
        async with OwnerSessionLocal() as session:
            from app.models import AuthSession
            from app.services.identity import Identity

            u_obj = await session.get(User, user_a)
            t_obj = await session.get(Tenant, tenant_a)
            m_obj = await session.get(TenantMembership, mem_a1)
            sess_obj = (
                await session.execute(
                    text("SELECT id, ctx_version FROM auth_sessions WHERE user_id = :u ORDER BY created_at DESC LIMIT 1"),
                    {"u": str(user_a)},
                )
            ).mappings().first()

            mock_identity = Identity(
                user=u_obj,
                session=sess_obj,
                tenant=t_obj,
                membership=m_obj,
                platform_admin=False,
                permissions=frozenset({"agent:access"}),
            )

            # 正确内容 claim -> claimed
            claim_ok = await ledger_service.claim_start(
                session,
                mock_identity,
                submission_id=uuid.UUID(sub_id),
                attempt_id=uuid.UUID(att_id),
                thread_id=thread_id,
                graph_id="basic-qa",
                operation_kind="initial",
                content="正常显式提问",
                source="native",
            )
            # 篡改内容 claim -> mismatch
            claim_tampered = await ledger_service.claim_start(
                session,
                mock_identity,
                submission_id=uuid.UUID(sub_id),
                attempt_id=uuid.UUID(att_id),
                thread_id=thread_id,
                graph_id="basic-qa",
                operation_kind="initial",
                content="被篡改的内容",
                source="native",
            )
            results["claim_start_v2_digest_check"] = {
                "claim_ok_status": claim_ok.get("status"),
                "claim_tampered_status": claim_tampered.get("status"),
            }

        # R07: 5. 恢复运行 (resume) 尝试修改选择被 409 拒绝
        async with OwnerSessionLocal() as session:
            await session.execute(
                text("UPDATE agent_run_attempts SET state = 'waiting_input' WHERE id = :a"),
                {"a": att_id},
            )
            await session.commit()

        resume_mod_res = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": f"key_resume_{suffix}",
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "source_attempt_id": att_id,
                "content": "用户补充输入",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v1",
                    "skill": {"mode": "auto"},
                    "mcp": {"mode": "allowlist", "tool_ids": []},
                    "catalog_version": "v1",
                },
            },
        )
        results["resume_selection_immutable"] = {
            "status_code": resume_mod_res.status_code,
            "rejected_with_409": resume_mod_res.status_code == 409,
        }

    finally:
        await client.aclose()
        # 清理
        async with OwnerSessionLocal() as session:
            await session.execute(text("DELETE FROM agent_capability_selections WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM agent_submissions WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM agent_run_attempts WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM agent_requests WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM agent_events WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM mcp_identity_bindings WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM thread_ownership WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM tenant_memberships WHERE tenant_id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_a)})
            await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": str(user_a)})
            await session.commit()

    all_passed = (
        results["unverified_protocol_rejected"]["rejected_with_403"] is True
        and results["forged_catalog_rejected"]["rejected_with_422"] is True
        and results["normal_admission_v2_digest"]["digest_version"] == 2
        and results["claim_start_v2_digest_check"]["claim_ok_status"] == "claimed"
        and results["claim_start_v2_digest_check"]["claim_tampered_status"] == "mismatch"
        and results["resume_selection_immutable"]["rejected_with_409"] is True
    )

    results["all_passed"] = all_passed
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


if __name__ == "__main__":
    asyncio.run(run_probe())
