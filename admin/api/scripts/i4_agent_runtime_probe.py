#!/usr/bin/env python3
"""Exercise Agent's I4 target RLS, identity projection, and preference boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

AUTHOR = Path(
    "/Users/acerola/Dev/Java/air_agent_iam/docs/reviews/evidence/run/i4-luna-20261018/author"
)
PRIVATE_ENV = Path(os.environ.get("I4_PRIVATE_ENV", "/private/tmp/i4-luna-20261018.private.env"))
VALUES = {k: v for k, v in dotenv_values(PRIVATE_ENV).items() if v is not None}
DB_NAME = "i4_luna_agent_target"
STAGE = "initial"


def configure() -> tuple[str, str]:
    owner = make_url(VALUES["DATABASE_URI"]).set(database=DB_NAME)
    runtime = make_url(VALUES["ADMIN_RUNTIME_DATABASE_URI"]).set(database=DB_NAME)
    os.environ.update(VALUES)
    os.environ.update(
        {
            "DATABASE_URI": owner.render_as_string(hide_password=False),
            "ADMIN_RUNTIME_DATABASE_URI": runtime.render_as_string(hide_password=False),
            "AGENT_IDENTITY_BACKEND": "remote",
            "IAM_BASE_URL": "http://127.0.0.1:50400",
            "IAM_ISSUER": "air-agent-iam",
            "IAM_INTERNAL_CLIENT_ID": VALUES["IAM_INTERNAL_RUNTIME_CLIENT_ID"],
            "IAM_INTERNAL_CLIENT_SECRET": VALUES["IAM_INTERNAL_RUNTIME_CLIENT_SECRET"],
            "IAM_RUNTIME_AUDIENCE": "langgraph-runtime",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    return owner.render_as_string(hide_password=False), runtime.render_as_string(
        hide_password=False
    )


async def main_async(owner_dsn: str, runtime_dsn: str) -> dict[str, object]:
    global STAGE
    import psycopg
    from common.identity.db import IdentityBackendUnavailable
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.db import SessionLocal, TenantContext, apply_tenant_context, runtime_engine
    from app.services.followed_regions import (
        FollowedRegionsError,
        TooManyRegionsError,
        set_followed_regions,
        set_membership_followed_regions,
    )
    from app.services.physical_separation import verify_database_mode
    from app.services.remote_identity import upsert_reference_projection

    ids = json.loads((AUTHOR / "agent-fixture-ids.json").read_text(encoding="utf-8"))
    user_id = uuid.UUID(ids["user_id"])
    tenant_id = uuid.UUID(ids["tenant_id"])
    membership_id = uuid.UUID(ids["membership_id"])
    run_id = uuid.UUID(VALUES["I4_RUN_ID"])
    disabled_account = f"i4_luna_revoked_{run_id.hex[:10]}"
    STAGE = "lookup disabled IAM target user"
    with psycopg.connect(VALUES["I4_IAM_TARGET_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id::text, auth_revision FROM iam.users WHERE account=%s", (disabled_account,)
        )
        disabled_row = cur.fetchone()
    if disabled_row is None:
        raise RuntimeError("disabled IAM fixture is missing from the target")
    disabled_user_id = uuid.UUID(disabled_row[0])

    STAGE = "seed target-only regions"
    region_codes = [f"I4LUNA-R{index:02d}" for index in range(1, 13)]
    with psycopg.connect(owner_dsn) as conn, conn.cursor() as cur:
        for index, code in enumerate(region_codes, start=1):
            cur.execute(
                "INSERT INTO regions(code,name,short_name,parent_code,level,sort) "
                "VALUES (%s,%s,%s,NULL,2,%s) ON CONFLICT (code) DO NOTHING",
                (code, f"I4 Luna Test Region {index}", f"I4R{index}", index),
            )

    evidence: dict[str, object] = {"database": DB_NAME, "runtime_role": "air_agent_runtime"}
    STAGE = "runtime role and target guard"
    async with runtime_engine.connect() as connection:
        await verify_database_mode(connection, "remote")
        evidence["remote_startup_guard"] = "passed"
        marker = (
            await connection.execute(
                text("SELECT mode,state FROM i4_separation_state WHERE singleton=1")
            )
        ).one()
        evidence["database_marker"] = {"mode": marker[0], "state": marker[1]}
        role = (
            await connection.execute(
                text(
                    "SELECT current_user,r.rolsuper,r.rolbypassrls "
                    "FROM pg_roles r WHERE r.rolname=current_user"
                )
            )
        ).one()
        evidence["runtime_role_flags"] = {
            "current_user": role[0],
            "superuser": role[1],
            "bypass_rls": role[2],
        }
        evidence["forced_rls_tables"] = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname IN "
                    "('agent_iam_sessions','iam_reference_users','iam_reference_tenants',"
                    "'iam_reference_memberships') "
                    "AND c.relrowsecurity AND c.relforcerowsecurity"
                )
            )
        ).scalar_one()
        evidence["no_context_reference_rows"] = {
            table: (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in (
                "iam_reference_users",
                "iam_reference_tenants",
                "iam_reference_memberships",
            )
        }
        try:
            await connection.execute(text("SELECT count(*) FROM public.users"))
            raise RuntimeError("legacy public.users unexpectedly exists in remote target")
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42P01":
                raise
            evidence["legacy_users_relation"] = "absent"
            await connection.rollback()
        try:
            await verify_database_mode(connection, "local")
            raise RuntimeError("local mode unexpectedly accepted remote target")
        except RuntimeError as exc:
            if "local 模式连接已拒绝" not in str(exc):
                raise
            evidence["local_mode_against_remote_target"] = "rejected"

    STAGE = "scoped RLS controls"
    async with SessionLocal() as session:
        await apply_tenant_context(
            session, TenantContext(user_id=str(user_id), tenant_id=str(tenant_id))
        )
        scoped = {}
        for table in ("iam_reference_users", "iam_reference_tenants", "iam_reference_memberships"):
            scoped[table] = (
                await session.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
        evidence["correct_context_reference_rows"] = scoped
        await session.commit()

    async with SessionLocal() as session:
        await apply_tenant_context(
            session,
            TenantContext(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())),
        )
        cross_scope = {}
        for table in ("iam_reference_users", "iam_reference_tenants", "iam_reference_memberships"):
            cross_scope[table] = (
                await session.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
        evidence["unrelated_context_reference_rows"] = cross_scope
        await session.rollback()

    STAGE = "membership reference rebind control"
    rebind_rejected = False
    async with SessionLocal() as session:
        try:
            await upsert_reference_projection(
                session,
                user_id=str(disabled_user_id),
                account=disabled_account,
                display_name="I4 disabled rebind negative control",
                tenant_id=str(tenant_id),
                tenant_code=ids["tenant_code"],
                tenant_name="I4 Luna tenant",
                membership_id=str(membership_id),
                user_auth_revision=int(disabled_row[1]),
                tenant_auth_revision=1,
                membership_revision=1,
            )
        except IdentityBackendUnavailable:
            rebind_rejected = True
        finally:
            await session.rollback()
    async with SessionLocal() as session:
        await apply_tenant_context(
            session, TenantContext(user_id=str(user_id), tenant_id=str(tenant_id))
        )
        original_binding = (
            await session.execute(
                text(
                    "SELECT user_id::text,tenant_id::text "
                    "FROM iam_reference_memberships WHERE id=:id"
                ),
                {"id": membership_id},
            )
        ).one_or_none()
        await session.commit()
    evidence["membership_rebind_rejected"] = rebind_rejected
    evidence["membership_binding_preserved"] = bool(
        original_binding
        and original_binding[0] == str(user_id)
        and original_binding[1] == str(tenant_id)
    )

    STAGE = "user preference limit"
    preference_context = TenantContext(user_id=str(user_id), tenant_id=str(tenant_id))
    async with SessionLocal() as session:
        await apply_tenant_context(session, preference_context)
        await set_followed_regions(session, user_id, region_codes[:5])
        await session.commit()
    six_rejected = False
    async with SessionLocal() as session:
        await apply_tenant_context(session, preference_context)
        try:
            await set_followed_regions(session, user_id, region_codes[:6])
        except TooManyRegionsError:
            six_rejected = True
        count_after_limit = (
            await session.execute(
                text("SELECT count(*) FROM user_followed_regions WHERE user_id=:u"), {"u": user_id}
            )
        ).scalar_one()
        await session.commit()
    evidence["five_user_regions_accepted"] = True
    evidence["sixth_user_region_rejected"] = six_rejected
    evidence["user_region_count_after_limit"] = count_after_limit

    async def replace_preferences(codes: list[str]) -> None:
        async with SessionLocal() as session:
            await apply_tenant_context(session, preference_context)
            await set_followed_regions(session, user_id, codes)
            await session.commit()

    STAGE = "concurrent user preference replacement"
    concurrency = await asyncio.gather(
        *(replace_preferences(region_codes[index : index + 5]) for index in range(6)),
        return_exceptions=True,
    )
    STAGE = "membership preference controls"
    async with SessionLocal() as session:
        await apply_tenant_context(session, preference_context)
        concurrent_count = (
            await session.execute(
                text("SELECT count(*) FROM user_followed_regions WHERE user_id=:u"), {"u": user_id}
            )
        ).scalar_one()
        concurrent_regions = (
            await session.execute(
                text(
                    "SELECT array_agg(region_code ORDER BY region_code) "
                    "FROM user_followed_regions WHERE user_id=:u"
                ),
                {"u": user_id},
            )
        ).scalar_one()
        await session.commit()
    evidence["concurrent_replacements"] = len(concurrency)
    evidence["concurrent_replacement_failures"] = sum(
        isinstance(item, Exception) for item in concurrency
    )
    evidence["concurrent_final_region_count"] = concurrent_count
    evidence["concurrent_final_regions_valid"] = set(concurrent_regions or []).issubset(
        region_codes
    )

    async with SessionLocal() as session:
        await apply_tenant_context(session, preference_context)
        await set_membership_followed_regions(session, membership_id, tenant_id, [region_codes[0]])
        await session.commit()
    mismatch_rejected = False
    async with SessionLocal() as session:
        await apply_tenant_context(session, preference_context)
        try:
            await set_membership_followed_regions(
                session, membership_id, uuid.uuid4(), [region_codes[-1]]
            )
        except FollowedRegionsError:
            mismatch_rejected = True
        await session.rollback()
    evidence["membership_preference_valid_write"] = True
    evidence["membership_wrong_tenant_rejected"] = mismatch_rejected

    with psycopg.connect(owner_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT conname,confrelid::regclass::text,confdeltype,pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE conrelid='public.membership_followed_regions'::regclass "
            "AND contype='f' ORDER BY conname"
        )
        fk_rows = cur.fetchall()
    composite = [r for r in fk_rows if "(membership_id, tenant_id)" in r[3]]
    evidence["membership_preference_foreign_keys"] = [
        {"name": r[0], "target": r[1], "delete_action": r[2]} for r in fk_rows
    ]
    evidence["membership_composite_fk_targets_remote_reference"] = bool(
        composite and composite[0][1] == "iam_reference_memberships" and composite[0][2] == "r"
    )

    STAGE = "Agent runtime connection denied to IAM database"
    runtime_url = make_url(runtime_dsn).set(database="i4_luna_iam_target")
    iam_connect_denied = False
    try:
        with psycopg.connect(runtime_url.render_as_string(hide_password=False), connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        iam_connect_denied = "permission denied for database" in str(
            exc
        ) and "CONNECT privilege" in str(exc)
    evidence["agent_runtime_connect_to_iam_denied"] = iam_connect_denied

    evidence["checks_passed"] = all(
        [
            evidence["database_marker"] == {"mode": "remote", "state": "ready"},
            evidence["runtime_role_flags"]
            == {"current_user": "air_agent_runtime", "superuser": False, "bypass_rls": False},
            evidence["forced_rls_tables"] == 4,
            all(v == 0 for v in evidence["no_context_reference_rows"].values()),
            all(v == 1 for v in evidence["correct_context_reference_rows"].values()),
            all(v == 0 for v in evidence["unrelated_context_reference_rows"].values()),
            rebind_rejected,
            evidence["membership_binding_preserved"],
            six_rejected,
            count_after_limit == 5,
            len(concurrency) == 6,
            evidence["concurrent_replacement_failures"] == 0,
            concurrent_count <= 5,
            evidence["concurrent_final_regions_valid"],
            mismatch_rejected,
            evidence["membership_composite_fk_targets_remote_reference"],
            iam_connect_denied,
        ]
    )
    return evidence


def main() -> None:
    global STAGE
    STAGE = "configure target DSNs"
    owner_dsn, runtime_dsn = configure()
    STAGE = "execute target runtime probes"
    result = asyncio.run(main_async(owner_dsn, runtime_dsn))
    out = AUTHOR / "agent-runtime-target-probes.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.chmod(out, 0o600)
    print(
        "AGENT_RUNTIME_TARGET_PROBES_PASSED"
        if result["checks_passed"]
        else "AGENT_RUNTIME_TARGET_PROBES_FAILED"
    )
    if not result["checks_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never print credentials or connection data
        sqlstate = getattr(exc, "sqlstate", None) or getattr(
            getattr(exc, "orig", None), "sqlstate", None
        )
        message = str(exc)
        import re

        message = re.sub(r"(?i)(password|secret|token)(\s*[=:]\s*)\S+", r"\1\2[redacted]", message)
        message = re.sub(r"(?i)postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", message)
        print(
            f"AGENT_RUNTIME_TARGET_PROBE_ERROR stage={STAGE} "
            f"type={type(exc).__name__} sqlstate={sqlstate} detail={message[:240]}"
        )
        raise SystemExit(2) from None
