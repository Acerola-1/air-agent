#!/usr/bin/env python3
"""Run the local preference path only against a disposable clone of the frozen source."""

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
DB_NAME = "i4_luna_agent_local_test"


def configure() -> tuple[str, str]:
    owner = make_url(VALUES["DATABASE_URI"]).set(database=DB_NAME)
    runtime = make_url(VALUES["ADMIN_RUNTIME_DATABASE_URI"]).set(database=DB_NAME)
    os.environ.update(VALUES)
    os.environ.update(
        {
            "DATABASE_URI": owner.render_as_string(hide_password=False),
            "ADMIN_RUNTIME_DATABASE_URI": runtime.render_as_string(hide_password=False),
            "AGENT_IDENTITY_BACKEND": "local",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    return owner.render_as_string(hide_password=False), runtime.render_as_string(
        hide_password=False
    )


async def run(owner_dsn: str) -> dict[str, object]:
    import psycopg
    from sqlalchemy import func, select

    from app.db import SessionLocal, TenantContext, apply_tenant_context, runtime_engine
    from app.models import User, UserFollowedRegion
    from app.services.followed_regions import TooManyRegionsError, set_followed_regions
    from app.services.physical_separation import verify_database_mode

    ids = json.loads((AUTHOR / "agent-fixture-ids.json").read_text(encoding="utf-8"))
    user_id = uuid.UUID(ids["user_id"])
    tenant_id = uuid.UUID(ids["tenant_id"])
    codes = [
        "I4LUNA-LOCAL01",
        "I4LUNA-LOCAL02",
        "I4LUNA-LOCAL03",
        "I4LUNA-LOCAL04",
        "I4LUNA-LOCAL05",
        "I4LUNA-LOCAL06",
    ]
    with psycopg.connect(owner_dsn) as conn, conn.cursor() as cur:
        for index, code in enumerate(codes, start=1):
            cur.execute(
                "INSERT INTO regions(code,name,short_name,parent_code,level,sort) "
                "VALUES (%s,%s,%s,NULL,2,%s) ON CONFLICT (code) DO NOTHING",
                (code, f"I4 Luna Local Region {index}", f"I4L{index}", index),
            )

    evidence: dict[str, object] = {"database": DB_NAME, "mode": "local"}
    async with runtime_engine.connect() as connection:
        await verify_database_mode(connection, "local")
        evidence["local_startup_guard"] = "passed"
        try:
            await verify_database_mode(connection, "remote")
            raise RuntimeError("remote mode unexpectedly accepted local clone")
        except RuntimeError as exc:
            if "remote 身份模式要求" not in str(exc):
                raise
            evidence["remote_mode_against_local_clone"] = "rejected"

    async with SessionLocal() as session:
        await apply_tenant_context(
            session, TenantContext(user_id=str(user_id), tenant_id=str(tenant_id))
        )
        user_visible = await session.scalar(select(User.id).where(User.id == user_id))
        if user_visible != user_id:
            raise RuntimeError("fixture user is not visible through local RLS")
        regions = await set_followed_regions(session, user_id, codes[:1])
        await session.commit()
    evidence["local_user_for_update_path"] = "passed"
    evidence["local_preference_write_count"] = len(regions)

    async with SessionLocal() as session:
        await apply_tenant_context(
            session, TenantContext(user_id=str(user_id), tenant_id=str(tenant_id))
        )
        current_count = await session.scalar(
            select(func.count())
            .select_from(UserFollowedRegion)
            .where(UserFollowedRegion.user_id == user_id)
        )
        limit_rejected = False
        try:
            await set_followed_regions(session, user_id, codes)
        except TooManyRegionsError:
            limit_rejected = True
        after_rejection = await session.scalar(
            select(func.count())
            .select_from(UserFollowedRegion)
            .where(UserFollowedRegion.user_id == user_id)
        )
        await session.commit()
    evidence["existing_local_preference_count"] = current_count
    evidence["local_sixth_region_rejected"] = limit_rejected
    evidence["local_preference_count_after_rejection"] = after_rejection
    evidence["checks_passed"] = bool(
        evidence["remote_mode_against_local_clone"] == "rejected"
        and evidence["local_preference_write_count"] == 1
        and current_count == 1
        and limit_rejected
        and after_rejection == 1
    )
    return evidence


def main() -> None:
    owner_dsn, _runtime_dsn = configure()
    result = asyncio.run(run(owner_dsn))
    out = AUTHOR / "agent-local-regression.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.chmod(out, 0o600)
    print(
        "AGENT_LOCAL_REGRESSION_PASSED"
        if result["checks_passed"]
        else "AGENT_LOCAL_REGRESSION_FAILED"
    )
    if not result["checks_passed"]:
        raise SystemExit(2) from None


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never print connection data or credentials
        print(f"AGENT_LOCAL_REGRESSION_ERROR {type(exc).__name__}")
        raise SystemExit(2) from None
