"""Agent 端到端验收 fixture：在专用测试库创建 A/B 企业、用户、会话并签发运行票据。

写入 **air_agent_tenant_test**（隔离库），不触业务库。票据文件写到指定目录
（默认 /tmp/air_agent_tickets），文件内容即 Bearer 后的票据字符串。

    set -a; . ../../.env; set +a
    .venv/bin/python scripts/agent_e2e_fixture.py --out /tmp/air_agent_tickets

用途见 docs/reviews/tenant-identity-access-acceptance.md（5.2/5.3 六图 API 实测）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import OwnerSessionLocal  # noqa: E402
from app.models import AuthSession, Tenant, User  # noqa: E402
from app.security import RuntimeTicket, new_session_token, sign_runtime_ticket  # noqa: E402
from app.services.tenant_seed import TENANT_ADMIN_ROLE_CODE, ensure_tenant_admin_role  # noqa: E402

E2E_TENANTS = {"a": "e2e-tenant-a", "b": "e2e-tenant-b"}
E2E_USERS = {"alice": "e2e_alice", "bob": "e2e_bob"}


async def main(out_dir: str) -> int:
    settings = get_settings()
    keys = settings.runtime_ticket_key_pairs
    if not keys:
        print("RUNTIME_TICKET_KEYS 缺失", file=sys.stderr)
        return 2

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    async with OwnerSessionLocal() as session:
        # 幂等：先清同名 fixture
        for code in E2E_TENANTS.values():
            tenant_id = await session.scalar(
                text("SELECT id FROM tenants WHERE code = :c"), {"c": code}
            )
            if tenant_id:
                for stmt in (
                    "DELETE FROM membership_roles WHERE tenant_id = :t",
                    "DELETE FROM tenant_memberships WHERE tenant_id = :t",
                    "DELETE FROM roles WHERE tenant_id = :t",
                    "DELETE FROM tenants WHERE id = :t",
                ):
                    await session.execute(text(stmt), {"t": str(tenant_id)})
        for account in E2E_USERS.values():
            user_id = await session.scalar(
                text("SELECT id FROM users WHERE account = :a"), {"a": account}
            )
            if user_id:
                for stmt in (
                    "DELETE FROM auth_sessions WHERE user_id = :u",
                    "DELETE FROM users WHERE id = :u",
                ):
                    await session.execute(text(stmt), {"u": str(user_id)})
        await session.commit()

        tenants = {}
        for label, code in E2E_TENANTS.items():
            tid = uuid.uuid4()
            session.add(Tenant(id=tid, code=code, name=f"E2E 企业 {label.upper()}"))
            tenants[label] = tid
        await session.flush()
        for _label, tid in tenants.items():
            await ensure_tenant_admin_role(session, tid)

        users = {}
        for label, account in E2E_USERS.items():
            uid = uuid.uuid4()
            session.add(
                User(
                    id=uid,
                    account=account,
                    display_name=f"E2E {label}",
                    password_hash="e2e-no-login",
                )
            )
            users[label] = uid
        await session.flush()

        # alice：A 的 tenant_admin + B 的普通成员；bob：仅 B
        alice_a = uuid.uuid4()
        alice_b = uuid.uuid4()
        bob_b = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO tenant_memberships (id, tenant_id, user_id) VALUES"
                " (:m1, :ta, :alice), (:m2, :tb, :alice), (:m3, :tb, :bob)"
            ),
            {
                "m1": str(alice_a),
                "m2": str(alice_b),
                "m3": str(bob_b),
                "ta": str(tenants["a"]),
                "tb": str(tenants["b"]),
                "alice": str(users["alice"]),
                "bob": str(users["bob"]),
            },
        )
        admin_role_a = await session.scalar(
            text("SELECT id FROM roles WHERE code = :c AND tenant_id = :t"),
            {"c": TENANT_ADMIN_ROLE_CODE, "t": str(tenants["a"])},
        )
        await session.execute(
            text(
                "INSERT INTO membership_roles (membership_id, role_id, tenant_id)"
                " VALUES (:m, :r, :t)"
            ),
            {"m": str(alice_a), "r": str(admin_role_a), "t": str(tenants["a"])},
        )

        now = datetime.now(UTC)
        tickets: dict[str, str] = {}
        for name, (user_label, tenant_label) in {
            "alice_a": ("alice", "a"),
            "alice_b": ("alice", "b"),
            "bob_b": ("bob", "b"),
        }.items():
            sid, raw, digest = new_session_token()
            session.add(
                AuthSession(
                    id=uuid.UUID(sid),
                    user_id=users[user_label],
                    token_hash=digest,
                    active_tenant_id=tenants[tenant_label],
                    user_auth_revision=0,
                    expires_at=now + timedelta(hours=1),
                )
            )
            tickets[name] = sign_runtime_ticket(
                RuntimeTicket(
                    session_id=sid,
                    user_id=str(users[user_label]),
                    tenant_id=str(tenants[tenant_label]),
                    membership_id=str(
                        {"alice_a": alice_a, "alice_b": alice_b, "bob_b": bob_b}[name]
                    ),
                    audience=settings.runtime_ticket_audience,
                    issued_at=int(now.timestamp()),
                    expires_at=int((now + timedelta(hours=1)).timestamp()),
                    session_ctx_version=1,
                    user_auth_revision=0,
                    tenant_auth_revision=1,
                    membership_revision=1,
                    kid=keys[0][0],
                ),
                keys,
            )
        await session.commit()

        ids_doc = {
            "tenant_a": str(tenants["a"]),
            "tenant_b": str(tenants["b"]),
            "alice": str(users["alice"]),
            "bob": str(users["bob"]),
            "membership_alice_a": str(alice_a),
            "membership_alice_b": str(alice_b),
            "membership_bob_b": str(bob_b),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }

    for name, token in tickets.items():
        (out / f"ticket_{name}.txt").write_text(token)
    (out / "ids.json").write_text(str(ids_doc))
    import json

    (out / "ids.json").write_text(json.dumps(ids_doc, indent=2))
    print(f"fixture 就绪（1 小时有效）: {ids_doc}")
    print(f"票据写入 {out}/ （alice_a / alice_b / bob_b）")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/air_agent_tickets")
    raise SystemExit(asyncio.run(main(parser.parse_args().out)))
