#!/usr/bin/env python3
"""Explicit, fail-closed rehearsal for IAM/Agent physical database separation.

The default command is read-only inventory. Every mutating command requires an
explicit run UUID and --execute. Production credentials belong in a mode-0600
env/pass file; this tool never includes DSNs in output or subprocess arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[3]
DEFAULT_AUTHOR = REPO / "docs/reviews/evidence/run/i4-luna-20261018/author"
LEGACY_IDENTITY = (
    "users",
    "tenants",
    "tenant_memberships",
    "roles",
    "role_permissions",
    "user_roles",
    "membership_roles",
    "permissions",
    "auth_sessions",
)
DROP_ORDER = (
    "membership_roles",
    "user_roles",
    "role_permissions",
    "auth_sessions",
    "tenant_memberships",
    "roles",
    "permissions",
    "users",
    "tenants",
)
RUNTIME_ROLE_SET = (
    "air_agent_runtime",
    "air_agent_worker",
    "air_agent_ledger_worker",
    "air_agent_maintenance",
)
IAM_APP_ROLE_SET = ("iam_app",)
ALL_APPLICATION_ROLES = (
    "iam_app",
    "iam_migrate",
    "air_agent_runtime",
    "air_agent_worker",
    "air_agent_ledger_worker",
    "air_agent_maintenance",
    "agent_migrator",
)
I4_REQUIRED_IAM_FLYWAY_VERSION = "23"
_LEGACY_SQL = re.compile(
    r"\b(?:from|join|update|into|delete\s+from)\s+(?:public\.)?"
    r"(?:users|tenants|tenant_memberships|roles|role_permissions|user_roles|"
    r"membership_roles|permissions|auth_sessions)\b",
    re.IGNORECASE,
)
KNOWN_IDENTITY_FK_TARGETS = {
    (
        "agent_capability_selections",
        "fk_agent_selections_membership_same_tenant",
        "tenant_memberships",
    ),
    ("agent_capability_selections", "fk_agent_selections_tenant", "tenants"),
    ("agent_capability_selections", "fk_agent_selections_user", "users"),
    ("audit_logs", "audit_logs_actor_id_fkey", "users"),
    ("audit_logs", "audit_logs_actor_membership_id_fkey", "tenant_memberships"),
    ("audit_logs", "audit_logs_tenant_id_fkey", "tenants"),
    ("auth_sessions", "auth_sessions_active_tenant_id_fkey", "tenants"),
    ("auth_sessions", "auth_sessions_user_id_fkey", "users"),
    ("departments", "departments_tenant_id_fkey", "tenants"),
    ("mcp_identity_bindings", "fk_bindings_membership_same_tenant", "tenant_memberships"),
    ("mcp_identity_bindings", "mcp_identity_bindings_tenant_id_fkey", "tenants"),
    ("membership_followed_regions", "fk_membership_followed_same_tenant", "tenant_memberships"),
    (
        "membership_followed_regions",
        "membership_followed_regions_membership_id_fkey",
        "tenant_memberships",
    ),
    ("membership_followed_regions", "membership_followed_regions_tenant_id_fkey", "tenants"),
    ("membership_roles", "fk_membership_roles_membership_same_tenant", "tenant_memberships"),
    ("membership_roles", "membership_roles_granted_by_fkey", "users"),
    ("membership_roles", "membership_roles_membership_id_fkey", "tenant_memberships"),
    ("membership_roles", "membership_roles_tenant_id_fkey", "tenants"),
    ("membership_roles", "membership_roles_role_id_fkey", "roles"),
    ("membership_roles", "fk_membership_roles_role_same_tenant", "roles"),
    ("permissions", "permissions_parent_id_fkey", "permissions"),
    ("role_permissions", "role_permissions_role_id_fkey", "roles"),
    ("role_permissions", "role_permissions_permission_id_fkey", "permissions"),
    ("roles", "roles_tenant_id_fkey", "tenants"),
    ("tenant_memberships", "tenant_memberships_tenant_id_fkey", "tenants"),
    ("tenant_memberships", "tenant_memberships_user_id_fkey", "users"),
    ("thread_ownership", "thread_ownership_tenant_id_fkey", "tenants"),
    ("thread_ownership", "thread_ownership_user_id_fkey", "users"),
    ("user_followed_regions", "user_followed_regions_user_id_fkey", "users"),
    ("user_roles", "user_roles_granted_by_fkey", "users"),
    ("user_roles", "user_roles_user_id_fkey", "users"),
    ("user_roles", "user_roles_role_id_fkey", "roles"),
}
EXPECTED_I4_TRANSFORM_FKS = {
    "fk_i4_selection_membership_same_tenant": (
        "agent_capability_selections",
        ("membership_id", "tenant_id"),
        "iam_reference_memberships",
        ("id", "tenant_id"),
        "r",
    ),
    "fk_i4_selection_tenant": (
        "agent_capability_selections",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "r",
    ),
    "fk_i4_selection_user": (
        "agent_capability_selections",
        ("user_id",),
        "iam_reference_users",
        ("id",),
        "r",
    ),
    "fk_i4_audit_actor_user": (
        "audit_logs",
        ("actor_id",),
        "iam_reference_users",
        ("id",),
        "n",
    ),
    "fk_i4_audit_actor_membership": (
        "audit_logs",
        ("actor_membership_id",),
        "iam_reference_memberships",
        ("id",),
        "n",
    ),
    "fk_i4_audit_tenant": (
        "audit_logs",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "n",
    ),
    "fk_i4_departments_tenant": (
        "departments",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "r",
    ),
    "fk_i4_bindings_membership_same_tenant": (
        "mcp_identity_bindings",
        ("membership_id", "tenant_id"),
        "iam_reference_memberships",
        ("id", "tenant_id"),
        "r",
    ),
    "fk_i4_bindings_tenant": (
        "mcp_identity_bindings",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "r",
    ),
    "fk_i4_followed_membership_same_tenant": (
        "membership_followed_regions",
        ("membership_id", "tenant_id"),
        "iam_reference_memberships",
        ("id", "tenant_id"),
        "r",
    ),
    "fk_i4_followed_tenant": (
        "membership_followed_regions",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "r",
    ),
    "fk_i4_thread_owner_tenant": (
        "thread_ownership",
        ("tenant_id",),
        "iam_reference_tenants",
        ("id",),
        "r",
    ),
    "fk_i4_thread_owner_user": (
        "thread_ownership",
        ("user_id",),
        "iam_reference_users",
        ("id",),
        "r",
    ),
    "fk_i4_followed_user": (
        "user_followed_regions",
        ("user_id",),
        "iam_reference_users",
        ("id",),
        "r",
    ),
}

_OPERATION: dict[str, Any] = {}


def _operation_stage(stage: str) -> None:
    _OPERATION["stage"] = stage


def _operation_write(stage: str) -> None:
    _OPERATION["stage"] = stage
    _OPERATION["write_attempted"] = True


def _operation_committed(stage: str) -> None:
    _OPERATION["stage"] = stage
    _OPERATION["write_attempted"] = True
    _OPERATION.setdefault("committed_steps", []).append(stage)


def _sql_tokens(source: str) -> list[str]:
    """Tokenize SQL identifiers/keywords while ignoring comments and string bodies."""
    tokens: list[str] = []
    i, size = 0, len(source)
    while i < size:
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if source.startswith("--", i):
            newline = source.find("\n", i + 2)
            i = size if newline < 0 else newline + 1
            continue
        if source.startswith("/*", i):
            depth = 1
            i += 2
            while i < size and depth:
                if source.startswith("/*", i):
                    depth += 1
                    i += 2
                elif source.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", source[i:])
            if match:
                tag = match.group(0)
                end = source.find(tag, i + len(tag))
                i = size if end < 0 else end + len(tag)
                tokens.append("<string>")
                continue
        if char == "'":
            i += 1
            while i < size:
                if source[i] == "'":
                    if i + 1 < size and source[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                if source[i] == "\\" and i + 1 < size:
                    i += 2
                else:
                    i += 1
            tokens.append("<string>")
            continue
        if char == '"':
            i += 1
            identifier: list[str] = []
            while i < size:
                if source[i] == '"':
                    if i + 1 < size and source[i + 1] == '"':
                        identifier.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                identifier.append(source[i])
                i += 1
            tokens.append("".join(identifier).casefold())
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < size and (source[i].isalnum() or source[i] in "_$"):
                i += 1
            tokens.append(source[start:i].casefold())
            continue
        tokens.append(char)
        i += 1
    return tokens


def _legacy_relation_references(source: str) -> set[str]:
    tokens = _sql_tokens(source)
    found: set[str] = set()
    relation_starters = {"from", "join", "update", "truncate"}
    for index, token in enumerate(tokens):
        if token in relation_starters:
            cursor = index + 1
        elif token == "into" and any(
            prior in {"insert", "merge"} for prior in tokens[max(0, index - 3) : index]
        ):
            cursor = index + 1
        elif token == "delete" and tokens[index + 1 : index + 2] == ["from"]:
            cursor = index + 2
        else:
            continue
        while cursor < len(tokens) and tokens[cursor] in {"only", "lateral", "table"}:
            cursor += 1
        if cursor + 2 < len(tokens) and tokens[cursor + 1] == ".":
            schema, relation = tokens[cursor], tokens[cursor + 2]
            if schema == "public" and relation in LEGACY_IDENTITY:
                found.add(relation)
        elif cursor < len(tokens) and tokens[cursor] in LEGACY_IDENTITY:
            found.add(tokens[cursor])
    return found


def _unclassified_legacy_relation_reference(source: str) -> bool:
    """Conservatively flag identity names the limited relation parser cannot place."""
    tokens = set(_sql_tokens(source))
    return bool((tokens & set(LEGACY_IDENTITY)) - _legacy_relation_references(source))


class I4Error(RuntimeError):
    """A safe-to-report, non-secret migration gate failure."""


@dataclass(frozen=True)
class DbSpec:
    resource: str
    dsn: str
    expected_owner: str


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _load_env(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        raise I4Error("env 文件不存在")
    if env_path.stat().st_mode & 0o077:
        raise I4Error("env 文件权限过宽；请设为仅所有者可读写")
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(env_path).items():
            if value is not None:
                os.environ.setdefault(key, value)
    except Exception as exc:  # noqa: BLE001 - do not leak parser input
        raise I4Error(f"env 文件解析失败（{type(exc).__name__}）") from None


def _dsn(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise I4Error(f"缺少环境变量 {name}")
    return value


def _resource_specs() -> dict[str, DbSpec]:
    return {
        "iam_source": DbSpec(
            "iam_source",
            _dsn("I4_IAM_SOURCE_DSN"),
            os.environ.get("I4_IAM_MIGRATOR_ROLE", "iam_migrate"),
        ),
        "iam_target": DbSpec(
            "iam_target",
            _dsn("I4_IAM_TARGET_DSN"),
            os.environ.get("I4_IAM_MIGRATOR_ROLE", "iam_migrate"),
        ),
        "agent_source": DbSpec(
            "agent_source",
            _dsn("I4_AGENT_SOURCE_DSN"),
            os.environ.get("I4_AGENT_MIGRATOR_ROLE", "agent_migrator"),
        ),
        "agent_target": DbSpec(
            "agent_target",
            _dsn("I4_AGENT_TARGET_DSN"),
            os.environ.get("I4_AGENT_MIGRATOR_ROLE", "agent_migrator"),
        ),
    }


def _connect(spec: DbSpec, *, autocommit: bool = False) -> psycopg.Connection:
    try:
        conn = psycopg.connect(spec.dsn, connect_timeout=5, autocommit=autocommit)
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
            user, is_super = cur.fetchone()
        operation_mode = os.environ.get("I4_OPERATION_MODE", "isolated").strip()
        if operation_mode not in {"isolated", "deployment"}:
            conn.close()
            raise I4Error("I4_OPERATION_MODE 仅允许 isolated 或 deployment")
        # Deployment is an explicit operator choice. Keep every live identity,
        # owner, UUID, version, tool and writer-fence gate unchanged; only the
        # authenticated superuser's name may differ from the test fixture.
        if not is_super or (operation_mode == "isolated" and user != "postgres"):
            conn.close()
            raise I4Error(
                f"{spec.resource} 需要显式迁移管理员；默认隔离模式仅接受 postgres，"
                "正式维护需显式 I4_OPERATION_MODE=deployment 且管理员为超级用户"
            )
        return conn
    except I4Error:
        raise
    except Exception as exc:  # noqa: BLE001 - libpq errors can contain connection data
        raise I4Error(f"{spec.resource} 无法连接（{type(exc).__name__}）") from None


def _format_postgres_version(version_num: int) -> str:
    major = version_num // 10000
    if major >= 10:
        return f"{major}.{version_num % 10000}"
    return f"{major}.{(version_num // 100) % 100}.{version_num % 100}"


def _db_identity(spec: DbSpec) -> dict[str, Any]:
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int, "
            "(SELECT oid::text FROM pg_database WHERE datname=current_database()), "
            "(SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()), "
            "(SELECT system_identifier::text FROM pg_control_system())"
        )
        name, role, version_num, oid, owner, system_id = cur.fetchone()
        raw = f"{system_id}:{oid}:{name}".encode()
        cur.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
            "AND c.relkind IN ('r','p','v','m','S','f')"
        )
        object_count = cur.fetchone()[0]
        agent_head = None
        if _has_relation(cur, "public.alembic_version"):
            cur.execute("SELECT array_agg(version_num ORDER BY version_num) FROM alembic_version")
            rows = cur.fetchone()[0] or []
            agent_head = rows
        flyway = None
        if _has_relation(cur, "iam.flyway_schema_history"):
            cur.execute(
                "SELECT version FROM iam.flyway_schema_history WHERE success "
                "ORDER BY installed_rank DESC LIMIT 1"
            )
            row = cur.fetchone()
            flyway = row[0] if row else None
        return {
            "database": name,
            "role": role,
            "postgres_version": _format_postgres_version(version_num),
            "oid": oid,
            "owner": owner,
            "system_id_sha256": hashlib.sha256(system_id.encode()).hexdigest(),
            "identity_fingerprint": hashlib.sha256(raw).hexdigest(),
            "user_object_count": object_count,
            "alembic_versions": agent_head,
            "flyway_version": flyway,
        }


def _has_relation(cur: psycopg.Cursor, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return bool(cur.fetchone()[0])


def _assert_distinct(source: dict[str, Any], target: dict[str, Any], label: str) -> None:
    if source["system_id_sha256"] == target["system_id_sha256"] and source["oid"] == target["oid"]:
        raise I4Error(f"{label} 源与目标实际指向同一数据库，拒绝操作")


def _blocked_application_roles(spec: DbSpec) -> tuple[str, ...]:
    """Every configured app role except the actual database owner must be fenced."""
    return tuple(role for role in ALL_APPLICATION_ROLES if role != spec.expected_owner)


def _assert_source_version(resource: str, identity: dict[str, Any]) -> None:
    if resource == "agent_source":
        if identity["alembic_versions"] != ["0020_i4_reference_cleanup"]:
            raise I4Error("Agent 源库 Alembic head 不符；要求唯一 head 0020_i4_reference_cleanup")
    elif resource == "iam_source":
        if identity["flyway_version"] != I4_REQUIRED_IAM_FLYWAY_VERSION:
            raise I4Error("IAM 源库 Flyway 版本不符；I4 校准端点要求唯一 head V23")


def _legacy_rows(conn: psycopg.Connection, table: str, columns: str) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        if not _has_relation(cur, f"public.{table}"):
            return []
        cur.execute(
            sql.SQL("SELECT {} FROM public.{} ORDER BY 1").format(
                sql.SQL(columns), sql.Identifier(table)
            )
        )
        return cur.fetchall()


def _identity_reconciliation(agent: DbSpec, iam: DbSpec) -> dict[str, Any]:
    with _connect(agent) as ac, _connect(iam) as ic:
        agent_users = _legacy_rows(ac, "users", "id::text, account")
        agent_tenants = _legacy_rows(ac, "tenants", "id::text, code")
        agent_memberships = _legacy_rows(
            ac, "tenant_memberships", "id::text, tenant_id::text, user_id::text"
        )
        with ic.cursor() as cur:
            cur.execute(
                "SELECT id::text, account FROM iam.users WHERE realm='workforce' ORDER BY id"
            )
            iam_users = cur.fetchall()
            cur.execute("SELECT id::text, code FROM iam.tenants ORDER BY id")
            iam_tenants = cur.fetchall()
            cur.execute(
                "SELECT id::text, tenant_id::text, user_id::text "
                "FROM iam.tenant_memberships ORDER BY id"
            )
            iam_memberships = cur.fetchall()

        def compare_named(
            kind: str, old: list[tuple[Any, ...]], authority: list[tuple[Any, ...]]
        ) -> dict[str, Any]:
            by_id = {str(row[0]): row for row in authority}
            by_name: dict[str, list[str]] = {}
            for row in authority:
                by_name.setdefault(str(row[1]).casefold(), []).append(str(row[0]))
            unknown: set[str] = set()
            collisions: set[str] = set()
            mismatches: set[str] = set()
            for row in old:
                identity_id, label = str(row[0]), str(row[1])
                canonical = by_id.get(identity_id)
                if canonical is None:
                    unknown.add(identity_id)
                    if by_name.get(label.casefold()):
                        collisions.add(identity_id)
                elif str(canonical[1]).casefold() != label.casefold():
                    mismatches.add(identity_id)
            return {
                "source_count": len(old),
                "authority_count": len(authority),
                "unknown_ids": sorted(unknown),
                "same_name_different_id_source_ids": sorted(collisions),
                "same_id_name_mismatch_ids": sorted(mismatches),
            }

        user_report = compare_named("user", agent_users, iam_users)
        tenant_report = compare_named("tenant", agent_tenants, iam_tenants)
        iam_membership_map = {str(row[0]): (str(row[1]), str(row[2])) for row in iam_memberships}
        unknown_memberships: set[str] = set()
        binding_conflicts: set[str] = set()
        for row in agent_memberships:
            mid, tid, uid = map(str, row)
            if mid not in iam_membership_map:
                unknown_memberships.add(mid)
            elif iam_membership_map[mid] != (tid, uid):
                binding_conflicts.add(mid)
        return {
            "users": user_report,
            "tenants": tenant_report,
            "memberships": {
                "source_count": len(agent_memberships),
                "authority_count": len(iam_memberships),
                "unknown_ids": sorted(unknown_memberships),
                "binding_conflict_ids": sorted(binding_conflicts),
            },
        }


def _source_ref_integrity(agent: DbSpec, iam: DbSpec) -> dict[str, Any]:
    """Reject stale I2 references before replacing them with authoritative IAM rows."""
    with _connect(agent) as ac, _connect(iam) as ic, ac.cursor() as a, ic.cursor() as i:
        i.execute("SELECT id::text FROM iam.users WHERE realm='workforce'")
        user_ids = {r[0] for r in i.fetchall()}
        i.execute("SELECT id::text FROM iam.tenants")
        tenant_ids = {r[0] for r in i.fetchall()}
        i.execute("SELECT id::text, tenant_id::text, user_id::text FROM iam.tenant_memberships")
        memberships = {r[0]: (r[1], r[2]) for r in i.fetchall()}
        stale_users: set[str] = set()
        stale_tenants: set[str] = set()
        stale_memberships: set[str] = set()
        for table, ids, bucket in (
            ("iam_reference_users", user_ids, stale_users),
            ("iam_reference_tenants", tenant_ids, stale_tenants),
        ):
            if _has_relation(a, f"public.{table}"):
                a.execute(
                    sql.SQL("SELECT id::text FROM public.{} ORDER BY id").format(
                        sql.Identifier(table)
                    )
                )
                bucket.update(row[0] for row in a.fetchall() if row[0] not in ids)
        if _has_relation(a, "public.iam_reference_memberships"):
            a.execute(
                "SELECT id::text, tenant_id::text, user_id::text FROM iam_reference_memberships"
            )
            for mid, tid, uid in a.fetchall():
                if memberships.get(mid) != (tid, uid):
                    stale_memberships.add(mid)
        return {
            "stale_user_reference_ids": sorted(stale_users),
            "stale_tenant_reference_ids": sorted(stale_tenants),
            "stale_or_rebound_membership_reference_ids": sorted(stale_memberships),
        }


def _catalog(agent: DbSpec) -> dict[str, Any]:
    with _connect(agent) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(%s::text[])",
            (list(LEGACY_IDENTITY),),
        )
        identity_oids = [row[0] for row in cur.fetchall()]
        cur.execute(
            "SELECT source_ns.nspname, source.relname, con.conname, target_ns.nspname, "
            "target.relname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con "
            "JOIN pg_class source ON source.oid=con.conrelid "
            "JOIN pg_namespace source_ns ON source_ns.oid=source.relnamespace "
            "JOIN pg_class target ON target.oid=con.confrelid "
            "JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace "
            "WHERE con.contype='f' AND con.confrelid=ANY(%s::oid[]) "
            "ORDER BY 1,2,3",
            (identity_oids,),
        )
        fks = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT n.nspname,c.relname,md5(pg_get_viewdef(c.oid,true)), "
            "EXISTS(SELECT 1 FROM pg_rewrite rw JOIN pg_depend d "
            "ON d.classid='pg_rewrite'::regclass AND d.objid=rw.oid "
            "AND d.refclassid='pg_class'::regclass WHERE rw.ev_class=c.oid "
            "AND d.refobjid=ANY(%s::oid[])), "
            "EXISTS(SELECT 1 FROM pg_rewrite rw JOIN pg_depend d "
            "ON d.classid='pg_rewrite'::regclass AND d.objid=rw.oid "
            "AND d.refclassid='pg_proc'::regclass WHERE rw.ev_class=c.oid "
            "AND d.refobjid IN (SELECT oid FROM pg_proc WHERE pronamespace NOT IN "
            "('pg_catalog'::regnamespace,'information_schema'::regnamespace))), "
            "pg_get_viewdef(c.oid,true) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND c.relkind IN ('v','m') ORDER BY 1,2",
            (identity_oids,),
        )
        raw_views = cur.fetchall()
        cur.execute(
            "SELECT p.oid,n.nspname,p.proname,md5(pg_get_functiondef(p.oid)),l.lanname,"
            "coalesce(ext.extname,''),p.prosrc,p.prosecdef,"
            "EXISTS(SELECT 1 FROM pg_depend d JOIN pg_class c ON c.oid=d.refobjid "
            "JOIN pg_namespace rn ON rn.oid=c.relnamespace "
            "WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid "
            "AND d.refclassid='pg_class'::regclass AND rn.nspname='public' "
            "AND c.relname=ANY(%s::text[])) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_language l ON l.oid=p.prolang "
            "LEFT JOIN LATERAL(SELECT e.extname FROM pg_depend ed "
            "JOIN pg_extension e ON e.oid=ed.refobjid "
            "WHERE ed.classid='pg_proc'::regclass AND ed.objid=p.oid "
            "AND ed.refclassid='pg_extension'::regclass AND ed.deptype='e' LIMIT 1) ext ON true "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND p.prokind <> 'a' ORDER BY 1,2",
            (list(LEGACY_IDENTITY),),
        )
        raw_functions = cur.fetchall()
        user_function_names = {
            name
            for _oid, _schema, name, _hash, _lang, extname, *_rest in raw_functions
            if not extname
        }
        function_flags: dict[int, tuple[bool, bool]] = {}
        function_calls: dict[int, set[str]] = {}
        funcs = []
        for (
            oid,
            schema,
            name,
            digest,
            language,
            extname,
            body,
            _security_definer,
            dependency,
        ) in raw_functions:
            tokens = _sql_tokens(body or "")
            legacy_references = _legacy_relation_references(body or "")
            legacy = bool(dependency or legacy_references)
            unknown = not extname and (
                language not in {"sql", "plpgsql"}
                or "execute" in tokens
                or _unclassified_legacy_relation_reference(body or "")
            )
            calls = {
                tokens[index]
                for index in range(len(tokens) - 1)
                if tokens[index] in user_function_names
                and tokens[index] != name.casefold()
                and tokens[index + 1] == "("
            }
            function_flags[oid] = (legacy, unknown)
            function_calls[oid] = calls
            funcs.append([oid, schema, name, digest, language, bool(extname), legacy, unknown])
        name_flags: dict[str, tuple[bool, bool]] = {}
        for _oid, _schema, name, _digest, _language, _extension, legacy, unknown in funcs:
            prior = name_flags.get(name.casefold(), (False, False))
            name_flags[name.casefold()] = (prior[0] or legacy, prior[1] or unknown)
        while True:
            changed = False
            for oid, schema, name, _digest, _language, extension, legacy, unknown in funcs:
                child_flags = [
                    name_flags.get(child, (False, True)) for child in function_calls[oid]
                ]
                next_legacy = legacy or any(child[0] for child in child_flags)
                next_unknown = unknown or any(child[1] for child in child_flags)
                if (next_legacy, next_unknown) != (legacy, unknown):
                    function_flags[oid] = (next_legacy, next_unknown)
                    index = next(i for i, row in enumerate(funcs) if row[0] == oid)
                    funcs[index] = [
                        oid,
                        schema,
                        name,
                        _digest,
                        _language,
                        extension,
                        next_legacy,
                        next_unknown,
                    ]
                    prior = name_flags[name.casefold()]
                    name_flags[name.casefold()] = (
                        prior[0] or next_legacy,
                        prior[1] or next_unknown,
                    )
                    changed = True
            if not changed:
                break
        views = []
        for schema, name, digest, dependency, function_dependency, definition in raw_views:
            tokens = _sql_tokens(definition or "")
            custom_calls = {
                tokens[index]
                for index in range(len(tokens) - 1)
                if tokens[index] in name_flags and tokens[index + 1] == "("
            }
            function_legacy = any(
                name_flags.get(called, (False, True))[0] for called in custom_calls
            )
            function_unknown = any(
                name_flags.get(called, (False, True))[1] for called in custom_calls
            )
            views.append(
                [
                    schema,
                    name,
                    digest,
                    bool(
                        dependency
                        or _legacy_relation_references(definition or "")
                        or function_legacy
                    ),
                    bool(function_unknown or (function_dependency and not custom_calls)),
                ]
            )
        cur.execute(
            "SELECT n.nspname,c.relname,t.tgname,p.proname,p.oid,pg_get_triggerdef(t.oid) "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
            "WHERE NOT t.tgisinternal AND n.nspname='public' ORDER BY 1,2,3"
        )
        triggers = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT n.nspname,c.relname,p.polname,p.polcmd,p.polpermissive,p.polroles::text,"
            "coalesce(pg_get_expr(p.polqual,p.polrelid),''),"
            "coalesce(pg_get_expr(p.polwithcheck,p.polrelid),''),"
            "EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_policy'::regclass "
            "AND d.objid=p.oid AND d.refclassid='pg_class'::regclass "
            "AND d.refobjid=ANY(%s::oid[])),"
            "EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_policy'::regclass "
            "AND d.objid=p.oid AND d.refclassid='pg_proc'::regclass "
            "AND d.refobjid IN (SELECT oid FROM pg_proc WHERE pronamespace NOT IN "
            "('pg_catalog'::regnamespace,'information_schema'::regnamespace))) "
            "FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' ORDER BY c.relname,p.polname",
            (identity_oids,),
        )
        policies = []
        for (
            schema,
            table,
            name,
            command,
            permissive,
            roles,
            qual,
            check,
            legacy_dependency,
            function_dependency,
        ) in cur.fetchall():
            policy_text = f"{qual} {check}"
            tokens = _sql_tokens(policy_text)
            custom_calls = {
                tokens[index]
                for index in range(len(tokens) - 1)
                if tokens[index] in name_flags and tokens[index + 1] == "("
            }
            function_legacy = any(
                name_flags.get(called, (False, True))[0] for called in custom_calls
            )
            function_unknown = any(
                name_flags.get(called, (False, True))[1] for called in custom_calls
            )
            policies.append(
                [
                    schema,
                    table,
                    name,
                    command,
                    permissive,
                    roles,
                    qual,
                    check,
                    bool(
                        legacy_dependency
                        or _legacy_relation_references(policy_text)
                        or function_legacy
                    ),
                    bool(function_unknown),
                    bool(function_unknown or (function_dependency and not custom_calls)),
                ]
            )
        cur.execute(
            "SELECT n.nspname,c.relname,coalesce(c.relacl::text,''), "
            "coalesce(array_agg(a.attname||'='||a.attacl::text) "
            "FILTER (WHERE a.attacl IS NOT NULL),'{}')::text "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m') "
            "GROUP BY n.nspname,c.relname,c.relacl ORDER BY c.relname"
        )
        table_acls = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT r.rolname,r.rolsuper,r.rolcreatedb,r.rolcreaterole,"
            "r.rolbypassrls,r.rolcanlogin "
            "FROM pg_roles r WHERE r.rolname IN ('air_agent_runtime','air_agent_worker',"
            "'air_agent_ledger_worker','air_agent_maintenance','agent_migrator',"
            "'iam_app','iam_migrate') "
            "ORDER BY 1"
        )
        roles = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT pg_get_userbyid(member),pg_get_userbyid(roleid),admin_option "
            "FROM pg_auth_members WHERE pg_get_userbyid(member) IN "
            "('air_agent_runtime','air_agent_worker','air_agent_ledger_worker','iam_app') "
            "ORDER BY 1,2"
        )
        memberships = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relkind IN ('r','p') ORDER BY c.relname"
        )
        rls = [list(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT count(*) FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace "
            "WHERE n.nspname='public'"
        )
        default_acl_count = cur.fetchone()[0]
        return {
            "identity_foreign_keys": fks,
            "views": views,
            "functions": funcs,
            "triggers": triggers,
            "policies": policies,
            "table_and_column_acls": table_acls,
            "roles": roles,
            "role_memberships": memberships,
            "rls": rls,
            "public_default_acl_count": default_acl_count,
        }


def _migration_blockers(
    identity: dict[str, Any], catalog: dict[str, Any], refs: dict[str, Any]
) -> list[str]:
    blockers: list[str] = []
    reconcile = identity
    for group in ("users", "tenants"):
        for key in (
            "unknown_ids",
            "same_name_different_id_source_ids",
            "same_id_name_mismatch_ids",
        ):
            if reconcile[group][key]:
                blockers.append(f"{group}:{key}")
    if reconcile["memberships"]["unknown_ids"]:
        blockers.append("memberships:unknown_ids")
    if reconcile["memberships"]["binding_conflict_ids"]:
        blockers.append("memberships:binding_conflicts")
    if any(refs.values()):
        blockers.append("stale_or_rebound_existing_reference_rows")
    if any(
        row[0] != "public"
        or row[3] != "public"
        or (row[1], row[2], row[4]) not in KNOWN_IDENTITY_FK_TARGETS
        for row in catalog["identity_foreign_keys"]
    ):
        blockers.append("unknown_identity_foreign_key")
    if any(row[1] in LEGACY_IDENTITY for row in catalog["triggers"]):
        blockers.append("trigger_attached_to_legacy_identity_table")
    if any(row[6] for row in catalog["functions"]):
        blockers.append("function_body_reads_legacy_identity")
    if any(row[7] for row in catalog["functions"]):
        blockers.append("unclassified_function_body")
    function_flags = {row[0]: (row[6], row[7]) for row in catalog["functions"]}
    if any(
        row[1] not in LEGACY_IDENTITY
        and function_flags.get(row[4], (False, True)) != (False, False)
        for row in catalog["triggers"]
    ):
        blockers.append("trigger_has_unresolved_identity_dependency")
    if any(row[3] for row in catalog["views"]):
        blockers.append("view_depends_on_legacy_identity")
    if any(row[4] for row in catalog["views"]):
        blockers.append("view_has_unclassified_function_dependency")
    for row in catalog["policies"]:
        if row[1] not in LEGACY_IDENTITY and (row[8] or row[9] or row[10]):
            blockers.append("business_rls_policy_has_unresolved_identity_dependency")
            break
    return sorted(set(blockers))


def _target_check(
    spec: DbSpec, identity: dict[str, Any], *, claimed_run: str | None = None
) -> dict[str, Any]:
    if identity["owner"] != spec.expected_owner:
        raise I4Error(f"{spec.resource} 目标 owner 与本次配置不符")
    with _connect(spec) as conn, conn.cursor() as cur:
        has_control = _has_relation(cur, "i4_control.run_state")
        if not has_control:
            if identity["user_object_count"] != 0:
                raise I4Error(f"{spec.resource} 目标非空且无本 run 所有权标记")
            return {
                "state": "empty",
                "owned_by_run": False,
                "target_fingerprint": identity["identity_fingerprint"],
            }
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='i4_control' "
            "AND table_name='run_state' AND column_name='target_fingerprint'"
        )
        if cur.fetchone() is None:
            raise I4Error(f"{spec.resource} run 标记缺少目标身份绑定；拒绝恢复旧 marker")
        cur.execute(
            "SELECT run_id::text,resource,source_fingerprint,target_fingerprint,"
            "stage,metadata::text "
            "FROM i4_control.run_state"
        )
        row = cur.fetchone()
        if row is None or cur.fetchone() is not None:
            raise I4Error(f"{spec.resource} 目标所有权标记缺失或不唯一")
        if claimed_run and row[0] != claimed_run:
            raise I4Error(f"{spec.resource} 目标属于另一 run，拒绝覆盖")
        if row[3] != identity["identity_fingerprint"]:
            raise I4Error(f"{spec.resource} 目标身份与持久 run 绑定不符，拒绝操作")
        return {
            "state": row[4],
            "run_id": row[0],
            "resource": row[1],
            "source_fingerprint": row[2],
            "target_fingerprint": row[3],
            "metadata": json.loads(row[5]),
            "owned_by_run": True,
        }


def _inventory() -> dict[str, Any]:
    specs = _resource_specs()
    identities = {name: _db_identity(spec) for name, spec in specs.items()}
    fingerprints = [identity["identity_fingerprint"] for identity in identities.values()]
    if len(set(fingerprints)) != 4:
        raise I4Error("四个 I4 数据库必须各自唯一；发现源/目标别名或重复资源")
    system_ids = {identity["system_id_sha256"] for identity in identities.values()}
    if len(system_ids) != 1 and os.environ.get("I4_OPERATION_MODE", "isolated") != "deployment":
        raise I4Error("默认隔离模式要求四库属于同一受控集群；跨集群正式维护须显式 deployment")
    _assert_source_version("iam_source", identities["iam_source"])
    _assert_source_version("agent_source", identities["agent_source"])
    iam_target = _target_check(specs["iam_target"], identities["iam_target"])
    agent_target = _target_check(specs["agent_target"], identities["agent_target"])
    reconciliation = _identity_reconciliation(specs["agent_source"], specs["iam_source"])
    refs = _source_ref_integrity(specs["agent_source"], specs["iam_source"])
    catalog = _catalog(specs["agent_source"])
    blockers = _migration_blockers(reconciliation, catalog, refs)
    return {
        "generated_at_utc": _utc(),
        "operation_mode": os.environ.get("I4_OPERATION_MODE", "isolated"),
        "write_effect": False,
        "databases": identities,
        "targets": {"iam": iam_target, "agent": agent_target},
        "identity_reconciliation": reconciliation,
        "existing_reference_integrity": refs,
        "catalog": catalog,
        "blockers": blockers,
        "ready_for_snapshot": not blockers
        and not iam_target["owned_by_run"]
        and not agent_target["owned_by_run"],
    }


def _phase_preflight(
    run_id: str,
    allowed_stages: set[str],
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Refresh all four live identities and verify both durable run bindings."""
    report = _inventory()
    if report["blockers"]:
        raise I4Error("阶段预检发现身份迁移阻断：" + ", ".join(report["blockers"]))
    for target_name, resource, source_name in (
        ("iam_target", "iam", "iam_source"),
        ("agent_target", "agent", "agent_source"),
    ):
        state = report["targets"][resource]
        if not state["owned_by_run"]:
            if allow_empty and state["state"] == "empty":
                continue
            raise I4Error(f"{target_name} 缺少持久 run 所有权标记")
        if state.get("run_id") != run_id or state.get("resource") != resource:
            raise I4Error(f"{target_name} run/resource 绑定不符，拒绝阶段操作")
        if (
            state.get("source_fingerprint")
            != report["databases"][source_name]["identity_fingerprint"]
        ):
            raise I4Error(f"{target_name} 所绑定源身份已变化，拒绝阶段操作")
        if (
            state.get("target_fingerprint")
            != report["databases"][target_name]["identity_fingerprint"]
        ):
            raise I4Error(f"{target_name} 实际身份与本 run 目标绑定不符")
        if state["state"] not in allowed_stages:
            raise I4Error(f"{target_name} 持久阶段 {state['state']} 不允许本操作")
    return report


def _emit(report: dict[str, Any], path: Path | None = None) -> None:
    raw = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.chmod(path, 0o600)
    print(raw, end="")


def _archive_path(run_id: str, resource: str, phase: str) -> Path:
    author = Path(os.environ.get("I4_AUTHOR_DIR", str(DEFAULT_AUTHOR))).expanduser().resolve()
    directory = author / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / f"{resource}-{phase}.dump"
    attempt = 2
    while path.exists():
        path = directory / f"{resource}-{phase}-{attempt}.dump"
        attempt += 1
    return path


def _container_name() -> str | None:
    name = os.environ.get("I4_PG_TOOL_CONTAINER", "").strip()
    if not name:
        return None
    if not name.startswith("i4-luna-"):
        raise I4Error("pg 工具容器必须明确指定本轮自有 i4-luna 容器")
    return name


def _libpq_parts(dsn: str) -> dict[str, str]:
    try:
        return conninfo_to_dict(dsn)
    except Exception:
        raise I4Error("数据库连接参数无法解析") from None


_CONTAINER_PATH_OPTIONS = {
    "sslcert",
    "sslkey",
    "sslrootcert",
    "sslcrl",
    "sslcrldir",
    "passfile",
    "servicefile",
}


def _service_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise I4Error("libpq 参数包含不支持的控制字符")
    if value != value.strip():
        raise I4Error("PGSERVICEFILE 参数首尾空白无法无损表达；已拒绝迁移")
    return value


def _container_endpoint(container: str, parts: dict[str, str]) -> tuple[str, str]:
    host = parts.get("host", "")
    port = parts.get("port", "5432")
    hostaddr = parts.get("hostaddr")
    if not host or host.startswith("/") or "," in host:
        raise I4Error("容器客户端不支持该 DSN socket/多主机 host；已在写入前拒绝")
    if hostaddr and "," in hostaddr:
        raise I4Error("容器客户端不支持多地址 hostaddr；已在写入前拒绝")
    try:
        raw = subprocess.run(
            ["container", "inspect", container], check=True, capture_output=True, text=True
        ).stdout
        entries = json.loads(raw)
        entry = next(item for item in entries if item.get("id") == container)
        if entry.get("status", {}).get("state") != "running":
            raise I4Error("pg 工具容器未运行")
        published = entry.get("configuration", {}).get("publishedPorts", [])
    except I4Error:
        raise
    except Exception as exc:  # noqa: BLE001 - no raw inspect output or secrets
        raise I4Error(f"无法验证 pg 工具容器端点（{type(exc).__name__}）") from None
    matches = [
        port_row
        for port_row in published
        if port_row.get("proto") == "tcp"
        and str(port_row.get("containerPort")) == "5432"
        and str(port_row.get("hostPort")) == str(port)
        and port_row.get("hostAddress") == host
    ]
    if len(matches) != 1:
        raise I4Error("DSN host/port 与工具容器实际发布端口不一致；拒绝备份/恢复")
    if hostaddr and hostaddr != host:
        raise I4Error("DSN hostaddr 与已验证发布地址不一致；拒绝备份/恢复")
    unsupported = sorted(key for key in _CONTAINER_PATH_OPTIONS if parts.get(key))
    if unsupported:
        raise I4Error(
            "容器客户端无法安全映射 DSN 文件参数 " + ",".join(unsupported) + "；已在写入前拒绝"
        )
    return host, str(matches[0]["containerPort"])


@contextmanager
def _tool_connection(spec: DbSpec) -> Iterator[tuple[str, list[str], dict[str, str]]]:
    """Prepare an exact libpq service for the actual host/container client."""
    container = _container_name()
    parts = _libpq_parts(spec.dsn)
    if not (parts.get("dbname") or parts.get("database")):
        raise I4Error(f"{spec.resource} DSN 缺少数据库名")
    if parts.get("service") or parts.get("servicefile"):
        raise I4Error("迁移工具不接受嵌套 libpq service；请提供显式 DSN")
    service = dict(parts)
    remote_path: str | None = None
    local_path: Path | None = None
    if container:
        host, port = _container_endpoint(container, parts)
        service["host"] = host
        service["port"] = port
        if parts.get("hostaddr"):
            service["hostaddr"] = host
        remote_path = f"/tmp/i4-client-{uuid.uuid4().hex}.conf"
    fd, filename = tempfile.mkstemp(prefix="i4-pg-service-", suffix=".conf")
    local_path = Path(filename)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("[i4_client]\n")
            for key, value in sorted(service.items()):
                stream.write(f"{key}={_service_value(value)}\n")
        if container and remote_path:
            subprocess.run(
                ["container", "cp", str(local_path), f"{container}:{remote_path}"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["container", "exec", container, "chmod", "600", remote_path],
                check=True,
                capture_output=True,
            )
            prefix = [
                "container",
                "exec",
                container,
                "env",
                f"PGSERVICEFILE={remote_path}",
                "PGSERVICE=i4_client",
            ]
            environment = os.environ.copy()
        else:
            if not shutil_which("psql"):
                raise I4Error("未找到 psql；无法验证将执行迁移的客户端实际数据库身份")
            prefix = []
            environment = os.environ.copy()
            environment["PGSERVICEFILE"] = str(local_path)
            environment["PGSERVICE"] = "i4_client"
        yield container or "", prefix, environment
    except I4Error:
        raise
    except (OSError, subprocess.CalledProcessError) as exc:
        raise I4Error(
            f"{spec.resource} PostgreSQL 客户端配置失败（{type(exc).__name__}）"
        ) from None
    finally:
        local_path.unlink(missing_ok=True)
        if container and remote_path:
            subprocess.run(
                ["container", "exec", container, "rm", "-f", remote_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _tool_identity(prefix: list[str], environment: dict[str, str]) -> tuple[str, str, str, str]:
    query = (
        "SELECT current_database(), d.oid::text, s.system_identifier::text, current_user "
        "FROM pg_database d CROSS JOIN pg_control_system() s "
        "WHERE d.datname=current_database()"
    )
    command = prefix + ["psql", "-X", "-A", "-t", "-F", "|", "-c", query]
    try:
        output = subprocess.run(
            command, check=True, capture_output=True, text=True, env=environment
        ).stdout.strip()
        database, oid, system_id, role = output.split("|")
        return database, oid, system_id, role
    except Exception as exc:  # noqa: BLE001 - captured output may contain credentials
        raise I4Error(f"迁移客户端身份探针失败（{type(exc).__name__}）") from None


def _verify_tool_identity(
    spec: DbSpec,
    expected_identity: dict[str, Any],
    prefix: list[str],
    environment: dict[str, str],
) -> None:
    actual = _tool_identity(prefix, environment)
    expected = (
        expected_identity["database"],
        expected_identity["oid"],
        expected_identity["role"],
    )
    if (actual[0], actual[1], actual[3]) != expected or hashlib.sha256(
        actual[2].encode()
    ).hexdigest() != expected_identity["system_id_sha256"]:
        raise I4Error(f"{spec.resource} 实际 pg 客户端连接身份与预检不符；拒绝读写")


def _verify_all_tool_identities(report: dict[str, Any], specs: dict[str, DbSpec]) -> None:
    """Probe all four actual libpq/psql endpoints before a mutating phase starts."""
    for name in ("iam_source", "iam_target", "agent_source", "agent_target"):
        with _tool_connection(specs[name]) as (_container, prefix, environment):
            _verify_tool_identity(specs[name], report["databases"][name], prefix, environment)


def _dump(spec: DbSpec, path: Path, expected_identity: dict[str, Any]) -> str:
    with _tool_connection(spec) as (_container, prefix, environment):
        _verify_tool_identity(spec, expected_identity, prefix, environment)
        _operation_write(f"dump:{spec.resource}")
        try:
            result = subprocess.run(
                prefix + ["pg_dump", "-Fc", "--no-password", "--dbname=service=i4_client"],
                check=True,
                capture_output=True,
                env=environment,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise I4Error(f"{spec.resource} pg_dump 失败（{type(exc).__name__}）") from None
        payload = result.stdout
    if not payload.startswith(b"PGDMP"):
        raise I4Error(f"{spec.resource} pg_dump 输出不是自定义归档")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
    os.chmod(path, 0o600)
    _operation_committed(f"archive_created:{spec.resource}")
    return hashlib.sha256(payload).hexdigest()


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _restore(
    spec: DbSpec,
    archive: Path,
    *,
    clean: bool,
    expected_identity: dict[str, Any],
) -> None:
    with _tool_connection(spec) as (container, prefix, environment):
        _verify_tool_identity(spec, expected_identity, prefix, environment)
        remote_archive = str(archive)
        if container:
            remote_archive = f"/tmp/i4-archive-{uuid.uuid4().hex}.dump"
            try:
                subprocess.run(
                    ["container", "cp", str(archive), f"{container}:{remote_archive}"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["container", "exec", container, "chmod", "600", remote_archive],
                    check=True,
                    capture_output=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise I4Error(f"{spec.resource} 私有归档传入失败（{type(exc).__name__}）") from None
        _operation_write(f"restore:{spec.resource}")
        command = prefix + [
            "pg_restore",
            "--exit-on-error",
            "--single-transaction",
            "--no-password",
            "--dbname=service=i4_client",
        ]
        if clean:
            command += ["--clean", "--if-exists"]
        command.append(remote_archive)
        try:
            subprocess.run(command, check=True, capture_output=True, env=environment)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise I4Error(
                f"{spec.resource} pg_restore 失败（{type(exc).__name__}）；"
                "目标可能存在部分阶段效果，需检查持久 run 状态"
            ) from None
        finally:
            if container:
                subprocess.run(
                    ["container", "exec", container, "rm", "-f", remote_archive],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    _operation_committed(f"restore_committed:{spec.resource}")


def _ensure_control(
    spec: DbSpec,
    run_id: str,
    resource: str,
    source_fp: str,
    target_fp: str,
    stage: str,
    archive_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _operation_write(f"control:{spec.resource}:{stage}")
    proposed_metadata = metadata or {}
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regnamespace('i4_control') IS NOT NULL")
        exists = cur.fetchone()[0]
        if not exists:
            cur.execute("CREATE SCHEMA i4_control AUTHORIZATION CURRENT_USER")
            cur.execute(
                "CREATE TABLE i4_control.run_state ("
                "run_id uuid PRIMARY KEY,resource text NOT NULL UNIQUE,"
                "source_fingerprint text NOT NULL,target_fingerprint text NOT NULL,"
                "stage text NOT NULL,archive_sha256 text,"
                "updated_at timestamptz NOT NULL DEFAULT now(),"
                "metadata jsonb NOT NULL DEFAULT '{}'::jsonb)"
            )
            cur.execute("REVOKE ALL ON SCHEMA i4_control FROM PUBLIC")
            cur.execute("REVOKE ALL ON ALL TABLES IN SCHEMA i4_control FROM PUBLIC")
        elif not _has_relation(cur, "i4_control.run_state"):
            raise I4Error(f"{spec.resource} 有非本工具 control schema，拒绝接管")
        cur.execute(
            "SELECT run_id::text,resource,source_fingerprint,target_fingerprint,stage "
            "FROM i4_control.run_state"
        )
        rows = cur.fetchall()
        if rows:
            if len(rows) != 1 or rows[0][:4] != (run_id, resource, source_fp, target_fp):
                raise I4Error(f"{spec.resource} 目标 run 所有权不匹配，拒绝覆盖")
            ranks = {
                "claimed": 0,
                "target_fence_intent": 1,
                "target_fenced": 2,
                "target_drain_blocked": 2,
                "restoring": 3,
                "snapshot_closing": 4,
                "snapshotted": 5,
                "source_fence_intent": 6,
                "source_fenced": 7,
                "drain_intent": 8,
                "drain_blocked": 8,
                "drained": 9,
                "frozen": 10,
                "final_target_fence_intent": 11,
                "final_target_fenced": 12,
                "final_target_drain_blocked": 12,
                "final_restoring": 13,
                "final_restored": 14,
                "transforming": 15,
                "transform_intent": 15,
                "transformed": 16,
                "publish_intent": 17,
                "published": 18,
                "ready": 19,
                "publish_blocked": 20,
            }
            old_stage = rows[0][4]
            if old_stage not in ranks or stage not in ranks or ranks[stage] < ranks[old_stage]:
                raise I4Error(f"{spec.resource} 阶段不能从 {old_stage} 回退到 {stage}")
            cur.execute(
                "UPDATE i4_control.run_state SET stage=%s,"
                "archive_sha256=coalesce(%s,archive_sha256),"
                "metadata=metadata || %s::jsonb,updated_at=now() "
                "WHERE run_id=%s",
                (stage, archive_hash, Jsonb(proposed_metadata), run_id),
            )
        else:
            cur.execute(
                "INSERT INTO i4_control.run_state("
                "run_id,resource,source_fingerprint,target_fingerprint,"
                "stage,archive_sha256,metadata) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    resource,
                    source_fp,
                    target_fp,
                    stage,
                    archive_hash,
                    Jsonb(proposed_metadata),
                ),
            )
    _operation_committed(f"control_committed:{spec.resource}:{stage}")


def _control_state(spec: DbSpec, run_id: str) -> dict[str, Any]:
    with _connect(spec) as conn, conn.cursor() as cur:
        if not _has_relation(cur, "i4_control.run_state"):
            raise I4Error(f"{spec.resource} 缺少持久 run 所有权标记")
        cur.execute(
            "SELECT resource,source_fingerprint,target_fingerprint,stage,metadata::text "
            "FROM i4_control.run_state WHERE run_id=%s",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise I4Error(f"{spec.resource} 不属于指定 run")
        return {
            "resource": row[0],
            "source_fingerprint": row[1],
            "target_fingerprint": row[2],
            "stage": row[3],
            "metadata": json.loads(row[4]),
        }


def _require_execute(args: argparse.Namespace) -> str:
    if not args.execute:
        raise I4Error("修改命令必须附加 --execute；默认操作为只读")
    try:
        return str(uuid.UUID(args.run_id))
    except (ValueError, TypeError):
        raise I4Error("修改命令需要有效 --run-id UUID") from None


def _snapshot(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _require_execute(args)
    specs = _resource_specs()
    allowed = {
        "claimed",
        "target_fence_intent",
        "target_fenced",
        "target_drain_blocked",
        "restoring",
        "snapshot_closing",
        "snapshotted",
    }
    resources = (
        ("iam_source", "iam_target", "iam"),
        ("agent_source", "agent_target", "agent"),
    )
    preflight = _phase_preflight(run_id, allowed, allow_empty=True)
    _verify_all_tool_identities(preflight, specs)
    snapshots: dict[str, Any] = {}
    # Persist ownership for both targets before progressing either one.
    for source_name, target_name, resource in resources:
        report = _phase_preflight(run_id, allowed, allow_empty=True)
        target_state = report["targets"][resource]
        if not target_state["owned_by_run"]:
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][source_name]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "claimed",
                metadata={"stage_fact": "target_claim_committed"},
            )

    _phase_preflight(run_id, allowed)
    # Revoke PUBLIC and application CONNECT on both destinations before any
    # archive restore, then inspect every pre-existing session while both stay shut.
    for source_name, target_name, resource in resources:
        report = _phase_preflight(run_id, allowed)
        target_state = report["targets"][resource]
        source_fp = report["databases"][source_name]["identity_fingerprint"]
        target_fp = report["databases"][target_name]["identity_fingerprint"]
        if target_state["state"] == "snapshotted":
            _assert_target_closed(specs[target_name], resource, verify_sessions=False)
            continue
        report = _phase_preflight(run_id, allowed)
        if report["targets"][resource]["state"] in {
            "claimed",
            "target_fence_intent",
            "target_fenced",
            "target_drain_blocked",
        }:
            _close_target(
                specs[target_name],
                resource,
                run_id=run_id,
                source_fp=source_fp,
                target_fp=target_fp,
                intent_stage="target_fence_intent",
                done_stage="target_fenced",
                blocked_stage="target_drain_blocked",
                allowed_stages=allowed,
                defer_session_check=True,
            )
        else:
            _assert_target_closed(specs[target_name], resource, verify_sessions=False)

    for source_name, target_name, resource in resources:
        report = _phase_preflight(run_id, allowed)
        database = report["databases"][target_name]["database"]
        with _connect(specs[target_name]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT usename,count(*) FROM pg_stat_activity WHERE datname=%s "
                "AND pid<>pg_backend_pid() AND usename<>'postgres' "
                "GROUP BY usename ORDER BY usename",
                (database,),
            )
            sessions = cur.fetchall()
        if sessions:
            if report["targets"][resource]["state"] in {"target_fenced", "target_drain_blocked"}:
                _ensure_control(
                    specs[target_name],
                    run_id,
                    resource,
                    report["databases"][source_name]["identity_fingerprint"],
                    report["databases"][target_name]["identity_fingerprint"],
                    "target_drain_blocked",
                    metadata={
                        "stage_fact": "preexisting_non_admin_sessions_blocked_restore",
                        "session_roles": [row[0] for row in sessions],
                        "target_connect_revoked": True,
                    },
                )
            raise I4Error(
                f"{resource} 目标栅栏已提交但仍有非管理员会话；所有目标保持封闭并拒绝恢复"
            )

    for source_name, target_name, resource in resources:
        report = _phase_preflight(run_id, allowed)
        source_fp = report["databases"][source_name]["identity_fingerprint"]
        target_fp = report["databases"][target_name]["identity_fingerprint"]
        if report["targets"][resource]["state"] == "snapshotted":
            snapshots[resource] = {"stage": "snapshotted", "reused_completed_snapshot": True}
            continue
        report = _phase_preflight(run_id, allowed)
        archive = _archive_path(run_id, resource, "online")
        digest = _dump(specs[source_name], archive, report["databases"][source_name])
        report = _phase_preflight(run_id, allowed)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "restoring",
            digest,
            {"stage_fact": "online_archive_created", "archive_sha256": digest},
        )
        report = _phase_preflight(run_id, allowed)
        _assert_target_closed(specs[target_name], resource)
        _restore(
            specs[target_name],
            archive,
            clean=True,
            expected_identity=report["databases"][target_name],
        )
        report = _phase_preflight(run_id, allowed)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "snapshot_closing",
            digest,
            {"stage_fact": "online_restore_committed"},
        )
        _assert_target_closed(specs[target_name], resource)
        report = _phase_preflight(run_id, allowed)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "snapshotted",
            digest,
            {"stage_fact": "online_snapshot_complete", "archive_sha256": digest},
        )
        snapshots[resource] = {
            "sha256": digest,
            "archive": str(archive),
            "stage": "snapshotted",
        }
    return {
        "run_id": run_id,
        "stage": "online_snapshot_complete",
        "write_effect": True,
        "snapshots": snapshots,
        "archives_mode": "0600",
    }


def _freeze_resource(
    source: DbSpec,
    roles: tuple[str, ...],
    run_id: str,
    resource: str,
    target: DbSpec,
    allowed_stages: set[str],
) -> dict[str, Any]:
    target_name = f"{resource}_target"
    source_name = f"{resource}_source"
    report = _phase_preflight(run_id, allowed_stages)
    source_fp = report["databases"][source_name]["identity_fingerprint"]
    target_fp = report["databases"][target_name]["identity_fingerprint"]
    target_state = report["targets"][resource]["state"]
    if target_state == "frozen":
        dbname = report["databases"][source_name]["database"]
        with _connect(source) as conn, conn.cursor() as cur:
            for role in roles:
                cur.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (role, dbname))
                if cur.fetchone()[0]:
                    raise I4Error(f"{resource} 已标记 frozen 但角色仍可 CONNECT：{role}")
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            if cur.fetchone()[0]:
                raise I4Error(f"{resource} 已标记 frozen 但仍有连接；保留栅栏并拒绝继续")
        return {
            "resource": resource,
            "connect_revoked_from": ["PUBLIC", *roles],
            "drained_sessions": True,
            "already_frozen": True,
            "active_transactions": 0,
        }
    if target_state not in {
        "source_fence_intent",
        "source_fenced",
        "drain_intent",
        "drain_blocked",
        "drained",
        "frozen",
    }:
        _ensure_control(
            target,
            run_id,
            resource,
            source_fp,
            target_fp,
            "source_fence_intent",
            metadata={"stage_fact": "source_acl_revoke_intent", "source_fingerprint": source_fp},
        )
    report = _phase_preflight(run_id, allowed_stages)
    dbname = report["databases"][source_name]["database"]
    _operation_write(f"source_acl_revoke:{resource}")
    with _connect(source) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname))
        )
        for role in roles:
            cur.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                    sql.Identifier(dbname), sql.Identifier(role)
                )
            )
        conn.commit()
    _operation_committed(f"source_acl_revoked:{resource}")
    report = _phase_preflight(run_id, allowed_stages)
    with _connect(source) as conn, conn.cursor() as cur:
        for role in roles:
            cur.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (role, dbname))
            if cur.fetchone()[0]:
                raise I4Error(f"{resource} 源角色仍可 CONNECT：{role}")
    if report["targets"][resource]["state"] in {"source_fence_intent", "source_fenced"}:
        _ensure_control(
            target,
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "source_fenced",
            metadata={
                "stage_fact": "source_acl_revoke_committed",
                "connect_denied": ["PUBLIC", *roles],
            },
        )
    report = _phase_preflight(run_id, allowed_stages)
    if report["targets"][resource]["state"] in {"source_fence_intent", "source_fenced"}:
        _ensure_control(
            target,
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "drain_intent",
            metadata={"stage_fact": "source_session_drain_intent"},
        )
    timeout = max(0.0, float(os.environ.get("I4_DRAIN_TIMEOUT_SECONDS", "20")))
    deadline = time.monotonic() + timeout
    while True:
        with _connect(source) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT usename,count(*) FILTER (WHERE xact_start IS NOT NULL),count(*) "
                "FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid() "
                "AND usename<>'postgres' GROUP BY usename ORDER BY usename",
                (dbname,),
            )
            sessions = cur.fetchall()
        active = sum(row[1] for row in sessions)
        if active == 0:
            break
        if time.monotonic() >= deadline:
            _ensure_control(
                target,
                run_id,
                resource,
                source_fp,
                report["databases"][target_name]["identity_fingerprint"],
                "drain_blocked",
                metadata={
                    "stage_fact": "active_transaction_timeout",
                    "active_transactions": active,
                    "connect_denied": True,
                },
            )
            raise I4Error(f"{resource} 源栅栏已提交但活动事务未排空；保留栅栏并停止")
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
    unknown = sorted({row[0] for row in sessions if row[0] not in roles})
    if unknown:
        _ensure_control(
            target,
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "drain_blocked",
            metadata={
                "stage_fact": "unrecognized_sessions_blocked",
                "unknown_session_roles": unknown,
                "connect_denied": True,
            },
        )
        raise I4Error(f"{resource} 源栅栏已提交但存在未识别会话角色；拒绝终止")
    _operation_write(f"terminate_idle_source_sessions:{resource}")
    with _connect(source) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s "
            "AND pid<>pg_backend_pid() AND usename=ANY(%s::text[])",
            (dbname, list(roles)),
        )
        terminated = sum(bool(row[0]) for row in cur.fetchall())
        conn.commit()
    _operation_committed(f"known_source_sessions_terminated:{resource}")
    with _connect(source) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
            (dbname,),
        )
        remaining = cur.fetchone()[0]
    if remaining:
        _ensure_control(
            target,
            run_id,
            resource,
            source_fp,
            report["databases"][target_name]["identity_fingerprint"],
            "drain_blocked",
            metadata={
                "stage_fact": "session_drain_incomplete",
                "remaining_sessions": remaining,
                "connect_denied": True,
            },
        )
        raise I4Error(f"{resource} 源栅栏已提交但连接未排空；保留栅栏并停止")
    _ensure_control(
        target,
        run_id,
        resource,
        source_fp,
        report["databases"][target_name]["identity_fingerprint"],
        "drained",
        metadata={"stage_fact": "source_sessions_drained", "terminated_idle_sessions": terminated},
    )
    if resource == "agent":
        report = _phase_preflight(run_id, allowed_stages)
        _operation_write("agent_source_state_frozen")
        with _connect(source) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE i4_separation_state SET state='frozen',run_id=%s,"
                "source_fingerprint=%s,updated_at=now() WHERE singleton=1 AND "
                "(mode='local' OR (mode='remote' AND state='frozen' AND run_id=%s "
                "AND source_fingerprint=%s))",
                (
                    run_id,
                    Jsonb({"identity_fingerprint": source_fp}),
                    run_id,
                    Jsonb({"identity_fingerprint": source_fp}),
                ),
            )
            if cur.rowcount != 1:
                raise I4Error("Agent 源分离标记缺失或不是 local 模式")
            conn.commit()
        _operation_committed("agent_source_state_frozen")
    report = _phase_preflight(run_id, allowed_stages)
    _ensure_control(
        target,
        run_id,
        resource,
        source_fp,
        report["databases"][target_name]["identity_fingerprint"],
        "frozen",
        metadata={"stage_fact": "source_frozen", "connect_denied": True, "sessions_drained": True},
    )
    return {
        "resource": resource,
        "connect_revoked_from": ["PUBLIC", *roles],
        "drained_sessions": True,
        "terminated_idle_sessions": terminated,
        "active_transactions": 0,
    }


def _freeze(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _require_execute(args)
    specs = _resource_specs()
    allowed = {
        "snapshotted",
        "source_fence_intent",
        "source_fenced",
        "drain_intent",
        "drain_blocked",
        "drained",
        "frozen",
    }
    preflight = _phase_preflight(run_id, allowed)
    _verify_all_tool_identities(preflight, specs)
    result = [
        _freeze_resource(
            specs["iam_source"], IAM_APP_ROLE_SET, run_id, "iam", specs["iam_target"], allowed
        ),
        _freeze_resource(
            specs["agent_source"], RUNTIME_ROLE_SET, run_id, "agent", specs["agent_target"], allowed
        ),
    ]
    _phase_preflight(run_id, {"frozen"})
    return {"run_id": run_id, "stage": "source_frozen", "write_effect": True, "sources": result}


def _project_references(agent_target: DbSpec, iam_source: DbSpec, run_id: str) -> dict[str, int]:
    _phase_preflight(run_id, _FINAL_STAGES)
    _operation_write("project_authoritative_iam_references")
    with _connect(iam_source) as iam, _connect(agent_target) as target:
        with iam.cursor() as cur:
            cur.execute(
                "SELECT id,account,display_name,status,auth_revision FROM iam.users "
                "WHERE realm='workforce' ORDER BY id"
            )
            users = cur.fetchall()
            cur.execute("SELECT id,code,name,status,auth_revision FROM iam.tenants ORDER BY id")
            tenants = cur.fetchall()
            cur.execute(
                "SELECT id,tenant_id,user_id,status,revision "
                "FROM iam.tenant_memberships ORDER BY id"
            )
            memberships = cur.fetchall()
        with target.cursor() as cur:
            for user_id, account, display_name, status, revision in users:
                cur.execute(
                    "INSERT INTO iam_reference_users("
                    "id,account,display_name,status,auth_revision,synced_at) "
                    "VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(id) DO UPDATE SET "
                    "account=excluded.account,display_name=excluded.display_name,status=excluded.status,"
                    "auth_revision=excluded.auth_revision,synced_at=now()",
                    (user_id, account, display_name, status, revision),
                )
            for tenant_id, code, name, status, revision in tenants:
                cur.execute(
                    "INSERT INTO iam_reference_tenants("
                    "id,code,name,status,auth_revision,synced_at) "
                    "VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(id) DO UPDATE SET "
                    "code=excluded.code,name=excluded.name,status=excluded.status,"
                    "auth_revision=excluded.auth_revision,synced_at=now()",
                    (tenant_id, code, name, status, revision),
                )
            counts = {
                "users": len(users),
                "tenants": len(tenants),
                "memberships": len(memberships),
            }
            for mid, tenant_id, user_id, status, revision in memberships:
                cur.execute(
                    "INSERT INTO iam_reference_memberships("
                    "id,tenant_id,user_id,status,revision,synced_at) "
                    "VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(id) DO UPDATE SET "
                    "status=excluded.status,revision=excluded.revision,synced_at=now() "
                    "WHERE iam_reference_memberships.tenant_id=excluded.tenant_id "
                    "AND iam_reference_memberships.user_id=excluded.user_id",
                    (mid, tenant_id, user_id, status, revision),
                )
                if cur.rowcount != 1:
                    raise I4Error(f"IAM 成员引用重绑冲突，membership UUID={mid}")
        target.commit()
    _operation_committed("authoritative_iam_references_committed")
    _phase_preflight(run_id, _FINAL_STAGES)
    return counts


def _transform_agent(agent_target: DbSpec, iam_source: DbSpec, run_id: str) -> dict[str, Any]:
    # Strictly refuse an unexpected FK before touching it; no CASCADE is used.
    preflight = _phase_preflight(run_id, _FINAL_STAGES)
    source_fp = preflight["databases"]["agent_source"]["identity_fingerprint"]
    catalog = _catalog(agent_target)
    unexpected = []
    for schema, table, name, target_schema, referenced, _definition in catalog[
        "identity_foreign_keys"
    ]:
        identity_fk = (table, name, referenced)
        if (
            schema != "public"
            or target_schema != "public"
            or identity_fk not in KNOWN_IDENTITY_FK_TARGETS
        ):
            unexpected.append(f"{schema}.{table}.{name}->{target_schema}.{referenced}")
    if unexpected:
        raise I4Error("目标出现未审查的身份外键：" + ", ".join(unexpected))
    _project_references(agent_target, iam_source, run_id)
    _phase_preflight(run_id, _FINAL_STAGES)
    _operation_write("transform_agent_identity_schema")
    with _connect(agent_target) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()")
                database_name = cur.fetchone()[0]
                drops = {
                    "agent_capability_selections": (
                        "fk_agent_selections_membership_same_tenant",
                        "fk_agent_selections_tenant",
                        "fk_agent_selections_user",
                    ),
                    "audit_logs": (
                        "audit_logs_actor_id_fkey",
                        "audit_logs_actor_membership_id_fkey",
                        "audit_logs_tenant_id_fkey",
                    ),
                    "departments": ("departments_tenant_id_fkey",),
                    "mcp_identity_bindings": (
                        "fk_bindings_membership_same_tenant",
                        "mcp_identity_bindings_tenant_id_fkey",
                    ),
                    "membership_followed_regions": (
                        "fk_membership_followed_same_tenant",
                        "membership_followed_regions_membership_id_fkey",
                        "membership_followed_regions_tenant_id_fkey",
                    ),
                    "thread_ownership": (
                        "thread_ownership_tenant_id_fkey",
                        "thread_ownership_user_id_fkey",
                    ),
                    "user_followed_regions": ("user_followed_regions_user_id_fkey",),
                }
                for table, constraints in drops.items():
                    for constraint in constraints:
                        cur.execute(
                            sql.SQL("ALTER TABLE public.{} DROP CONSTRAINT {}").format(
                                sql.Identifier(table), sql.Identifier(constraint)
                            )
                        )
                additions = (
                    "ALTER TABLE public.agent_capability_selections "
                    "ADD CONSTRAINT fk_i4_selection_membership_same_tenant "
                    "FOREIGN KEY(membership_id,tenant_id) "
                    "REFERENCES iam_reference_memberships(id,tenant_id) ON DELETE RESTRICT",
                    "ALTER TABLE public.agent_capability_selections "
                    "ADD CONSTRAINT fk_i4_selection_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE RESTRICT",
                    "ALTER TABLE public.agent_capability_selections "
                    "ADD CONSTRAINT fk_i4_selection_user "
                    "FOREIGN KEY(user_id) REFERENCES iam_reference_users(id) ON DELETE RESTRICT",
                    "ALTER TABLE public.audit_logs ADD CONSTRAINT fk_i4_audit_actor_user "
                    "FOREIGN KEY(actor_id) REFERENCES iam_reference_users(id) ON DELETE SET NULL",
                    "ALTER TABLE public.audit_logs "
                    "ADD CONSTRAINT fk_i4_audit_actor_membership "
                    "FOREIGN KEY(actor_membership_id) "
                    "REFERENCES iam_reference_memberships(id) ON DELETE SET NULL",
                    "ALTER TABLE public.audit_logs ADD CONSTRAINT fk_i4_audit_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE SET NULL",
                    "ALTER TABLE public.departments "
                    "ADD CONSTRAINT fk_i4_departments_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE RESTRICT",
                    "ALTER TABLE public.mcp_identity_bindings "
                    "ADD CONSTRAINT fk_i4_bindings_membership_same_tenant "
                    "FOREIGN KEY(membership_id,tenant_id) "
                    "REFERENCES iam_reference_memberships(id,tenant_id) ON DELETE RESTRICT",
                    "ALTER TABLE public.mcp_identity_bindings "
                    "ADD CONSTRAINT fk_i4_bindings_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE RESTRICT",
                    "ALTER TABLE public.membership_followed_regions "
                    "ADD CONSTRAINT fk_i4_followed_membership_same_tenant "
                    "FOREIGN KEY(membership_id,tenant_id) "
                    "REFERENCES iam_reference_memberships(id,tenant_id) ON DELETE RESTRICT",
                    "ALTER TABLE public.membership_followed_regions "
                    "ADD CONSTRAINT fk_i4_followed_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE RESTRICT",
                    "ALTER TABLE public.thread_ownership "
                    "ADD CONSTRAINT fk_i4_thread_owner_tenant "
                    "FOREIGN KEY(tenant_id) REFERENCES iam_reference_tenants(id) "
                    "ON DELETE RESTRICT",
                    "ALTER TABLE public.thread_ownership "
                    "ADD CONSTRAINT fk_i4_thread_owner_user "
                    "FOREIGN KEY(user_id) REFERENCES iam_reference_users(id) ON DELETE RESTRICT",
                    "ALTER TABLE public.user_followed_regions ADD CONSTRAINT fk_i4_followed_user "
                    "FOREIGN KEY(user_id) REFERENCES iam_reference_users(id) ON DELETE RESTRICT",
                )
                for statement in additions:
                    cur.execute(statement)
                cur.execute(
                    "SELECT c.relname,p.polname FROM pg_policy p "
                    "JOIN pg_class c ON c.oid=p.polrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=ANY(%s::text[]) "
                    "ORDER BY c.relname,p.polname",
                    (list(LEGACY_IDENTITY),),
                )
                old_policies = cur.fetchall()
                for table, policy in old_policies:
                    cur.execute(
                        sql.SQL("DROP POLICY {} ON public.{}").format(
                            sql.Identifier(policy), sql.Identifier(table)
                        )
                    )
                for table in DROP_ORDER:
                    cur.execute(sql.SQL("DROP TABLE public.{}").format(sql.Identifier(table)))
                cur.execute(
                    "UPDATE i4_separation_state SET mode='remote',state='preparing',"
                    "run_id=%s,source_fingerprint=%s,updated_at=now() WHERE singleton=1",
                    (run_id, Jsonb({"identity_fingerprint": source_fp})),
                )
                if cur.rowcount != 1:
                    raise I4Error("Agent 目标缺少唯一分离状态行")
                cur.execute(
                    sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(database_name)
                    )
                )
                cur.execute(
                    sql.SQL(
                        "REVOKE CONNECT,TEMP ON DATABASE {} FROM air_agent_runtime,"
                        "air_agent_worker,air_agent_ledger_worker,air_agent_maintenance,"
                        "agent_migrator"
                    ).format(sql.Identifier(database_name))
                )
        conn.commit()
    _operation_committed("agent_identity_schema_transform_committed")
    return {
        "projected_references": "complete",
        "foreign_keys_rewired": 14,
        "legacy_identity_policies_dropped": len(old_policies),
        "legacy_identity_tables_dropped": list(DROP_ORDER),
        "cascade_used": False,
    }


def _verify_agent_transform_state(
    agent_target: DbSpec,
    iam_target: DbSpec,
    run_id: str,
    source_fingerprint: str,
) -> bool:
    """Return True only for a complete committed transform; reject hybrid schemas."""
    with _connect(agent_target) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mode,state,run_id::text,source_fingerprint "
            "FROM i4_separation_state WHERE singleton=1"
        )
        state = cur.fetchone()
        cur.execute(
            "SELECT name FROM unnest(%s::text[]) AS t(name) "
            "WHERE to_regclass('public.'||quote_ident(name)) IS NOT NULL ORDER BY name",
            (list(LEGACY_IDENTITY),),
        )
        legacy_tables = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT name FROM unnest(%s::text[]) AS t(name) "
            "WHERE to_regclass('public.'||quote_ident(name)) IS NULL ORDER BY name",
            (["iam_reference_users", "iam_reference_tenants", "iam_reference_memberships"],),
        )
        missing_reference_tables = [row[0] for row in cur.fetchall()]

    if missing_reference_tables:
        raise I4Error("转换意图后 Agent 引用表缺失；拒绝恢复或重放")
    local_schema_states = (
        ("local", "local", None, None),
        (
            "local",
            "frozen",
            run_id,
            {"identity_fingerprint": source_fingerprint},
        ),
    )
    if legacy_tables == set(LEGACY_IDENTITY) and state in local_schema_states:
        _assert_target_closed(agent_target, "agent")
        _assert_target_closed(iam_target, "iam")
        return False
    expected_state = (
        "remote",
        "preparing",
        run_id,
        {"identity_fingerprint": source_fingerprint},
    )
    ready_state = (
        "remote",
        "ready",
        run_id,
        {"identity_fingerprint": source_fingerprint},
    )
    if legacy_tables or (state != expected_state and state != ready_state):
        raise I4Error("Agent 转换状态与持久意图不一致；目标保持封闭并拒绝重放")
    if state == ready_state:
        _assert_target_closed(agent_target, "agent")
        _assert_target_closed(iam_target, "iam")

    columns = {
        "iam_reference_users": [
            ("id", "uuid", None),
            ("account", "character varying", 64),
            ("display_name", "character varying", 128),
            ("status", "character varying", 16),
            ("auth_revision", "integer", None),
            ("synced_at", "timestamp with time zone", None),
        ],
        "iam_reference_tenants": [
            ("id", "uuid", None),
            ("code", "character varying", 64),
            ("name", "character varying", 128),
            ("status", "character varying", 16),
            ("auth_revision", "integer", None),
            ("synced_at", "timestamp with time zone", None),
        ],
        "iam_reference_memberships": [
            ("id", "uuid", None),
            ("tenant_id", "uuid", None),
            ("user_id", "uuid", None),
            ("status", "character varying", 16),
            ("revision", "integer", None),
            ("synced_at", "timestamp with time zone", None),
        ],
    }
    with _connect(agent_target) as conn, conn.cursor() as cur:
        for table, expected in columns.items():
            cur.execute(
                "SELECT column_name,data_type,character_maximum_length,is_nullable "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=%s ORDER BY ordinal_position",
                (table,),
            )
            actual = cur.fetchall()
            normalized = [(name, kind, length) for name, kind, length, _nullable in actual]
            if normalized != expected or any(row[3] != "NO" for row in actual):
                raise I4Error(f"Agent 目标 {table} 列定义与已审查变换不符")

        cur.execute(
            "SELECT con.conname, rel.relname, ref.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum,ord) "
            "JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum "
            "ORDER BY k.ord), "
            "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum,ord) "
            "JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=k.attnum "
            "ORDER BY k.ord),con.confdeltype,con.convalidated "
            "FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid "
            "JOIN pg_namespace ns ON ns.oid=rel.relnamespace "
            "JOIN pg_class ref ON ref.oid=con.confrelid "
            "WHERE ns.nspname='public' AND con.contype='f' "
            "AND con.conname LIKE 'fk_i4_%' ORDER BY con.conname"
        )
        fk_rows = cur.fetchall()
        actual_fks = {
            row[0]: (row[1], tuple(row[3]), row[2], tuple(row[4]), row[5], row[6])
            for row in fk_rows
        }
        expected_fks = {
            name: (*definition, True) for name, definition in EXPECTED_I4_TRANSFORM_FKS.items()
        }
        if actual_fks != expected_fks:
            raise I4Error("Agent 目标十四条 I4 身份外键与审查定义不符")

        for table in columns:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege('air_agent_runtime',%s,%s)",
                    (f"public.{table}", privilege),
                )
                if not cur.fetchone()[0]:
                    raise I4Error(f"Agent runtime 缺少已声明引用表权限：{table}:{privilege}")

        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database d "
            "CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a "
            "WHERE d.datname=current_database() AND a.grantee=0 "
            "AND a.privilege_type='CONNECT')"
        )
        if cur.fetchone()[0]:
            raise I4Error("Agent 转换后 PUBLIC 仍可 CONNECT")
        for role in _blocked_application_roles(agent_target):
            cur.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')", (role,))
            if cur.fetchone()[0]:
                raise I4Error(f"Agent 转换后跨界/运行角色仍可 CONNECT：{role}")

    projection_queries = (
        (
            "SELECT id::text,account,display_name,status,auth_revision "
            "FROM public.iam_reference_users ORDER BY id",
            "SELECT id::text,account,display_name,status,auth_revision "
            "FROM iam.users WHERE realm='workforce' ORDER BY id",
        ),
        (
            "SELECT id::text,code,name,status,auth_revision "
            "FROM public.iam_reference_tenants ORDER BY id",
            "SELECT id::text,code,name,status,auth_revision FROM iam.tenants ORDER BY id",
        ),
        (
            "SELECT id::text,tenant_id::text,user_id::text,status,revision "
            "FROM public.iam_reference_memberships ORDER BY id",
            "SELECT id::text,tenant_id::text,user_id::text,status,revision "
            "FROM iam.tenant_memberships ORDER BY id",
        ),
    )
    for agent_query, iam_query in projection_queries:
        with _connect(agent_target) as agent_conn, agent_conn.cursor() as agent_cur:
            agent_cur.execute(agent_query)
            agent_rows = agent_cur.fetchall()
        with _connect(iam_target) as iam_conn, iam_conn.cursor() as iam_cur:
            iam_cur.execute(iam_query)
            iam_rows = iam_cur.fetchall()
        if agent_rows != iam_rows:
            raise I4Error("Agent IAM 引用投影与目标权威身份数据不一致")
    _assert_target_closed(iam_target, "iam")
    return True


def _set_target_acls(spec: DbSpec, resource: str, run_id: str) -> None:
    report = _phase_preflight(run_id, _FINAL_STAGES)
    identity = report["databases"][f"{resource}_target"]
    _operation_write(f"target_acl_publish:{resource}")
    with _connect(spec) as conn, conn.cursor() as cur:
        db = sql.Identifier(identity["database"])
        cur.execute(sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM PUBLIC").format(db))
        if resource == "iam":
            for role in (*RUNTIME_ROLE_SET, "agent_migrator"):
                cur.execute(
                    sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM {}").format(
                        db, sql.Identifier(role)
                    )
                )
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO iam_app,iam_migrate").format(db))
            for role in (*RUNTIME_ROLE_SET, "agent_migrator"):
                cur.execute(
                    "SELECT has_database_privilege(%s,%s,'CONNECT')", (role, identity["database"])
                )
                if cur.fetchone()[0]:
                    raise I4Error(f"Agent 角色意外可连接 IAM 目标：{role}")
        else:
            for role in (*IAM_APP_ROLE_SET, "iam_migrate"):
                cur.execute(
                    sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM {}").format(
                        db, sql.Identifier(role)
                    )
                )
            cur.execute(
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO air_agent_runtime,air_agent_worker,"
                    "air_agent_ledger_worker,air_agent_maintenance,agent_migrator"
                ).format(db)
            )
            for role in (*IAM_APP_ROLE_SET, "iam_migrate"):
                cur.execute(
                    "SELECT has_database_privilege(%s,%s,'CONNECT')", (role, identity["database"])
                )
                if cur.fetchone()[0]:
                    raise I4Error(f"IAM 角色意外可连接 Agent 目标：{role}")
        conn.commit()
    _operation_committed(f"target_acl_published:{resource}")
    _phase_preflight(run_id, _FINAL_STAGES)
    _assert_target_published(spec, resource)


def _assert_target_published(spec: DbSpec, resource: str) -> None:
    identity = _db_identity(spec)
    allowed_roles = (
        ("iam_app", "iam_migrate") if resource == "iam" else (*RUNTIME_ROLE_SET, "agent_migrator")
    )
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database d "
            "CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a "
            "WHERE d.datname=current_database() AND a.grantee=0 "
            "AND a.privilege_type='CONNECT')"
        )
        if cur.fetchone()[0]:
            raise I4Error(f"{resource} 目标发布后 PUBLIC 仍有 CONNECT")
        for role in ALL_APPLICATION_ROLES:
            cur.execute(
                "SELECT has_database_privilege(%s,%s,'CONNECT')",
                (role, identity["database"]),
            )
            actual = bool(cur.fetchone()[0])
            expected = role in allowed_roles
            if actual != expected:
                raise I4Error(f"{resource} 目标发布 ACL 不符：{role}")


def _close_target(
    spec: DbSpec,
    resource: str,
    *,
    run_id: str,
    source_fp: str,
    target_fp: str,
    intent_stage: str,
    done_stage: str,
    blocked_stage: str,
    allowed_stages: set[str],
    defer_session_check: bool = False,
) -> None:
    target_name = f"{resource}_target"
    current = _phase_preflight(run_id, allowed_stages)
    database = current["databases"][target_name]["database"]
    state = current["targets"][resource]["state"]
    completed_stages = {done_stage, blocked_stage}
    if state not in completed_stages and state != intent_stage:
        _ensure_control(
            spec,
            run_id,
            resource,
            source_fp,
            target_fp,
            intent_stage,
            metadata={"stage_fact": "target_acl_fence_intent", "connect_denied": "pending"},
        )
    current = _phase_preflight(run_id, allowed_stages)
    database = current["databases"][target_name]["database"]
    blocked_roles = _blocked_application_roles(spec)
    _operation_write(f"target_acl_revoke:{resource}")
    with _connect(spec) as conn, conn.cursor() as cur:
        db = sql.Identifier(database)
        cur.execute(sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM PUBLIC").format(db))
        for role in blocked_roles:
            cur.execute(
                sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM {}").format(
                    db, sql.Identifier(role)
                )
            )
        conn.commit()
    _operation_committed(f"target_acl_revoked:{resource}")
    current = _phase_preflight(run_id, allowed_stages)
    _ensure_control(
        spec,
        run_id,
        resource,
        source_fp,
        current["databases"][target_name]["identity_fingerprint"],
        done_stage,
        metadata={
            "stage_fact": "target_acl_revocation_committed",
            "connect_denied": ["PUBLIC", *blocked_roles],
            "owner_role_exempt": spec.expected_owner,
        },
    )
    _assert_target_closed(spec, resource, database=database, verify_sessions=False)
    if defer_session_check:
        return
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT usename,count(*) FROM pg_stat_activity WHERE datname=%s "
            "AND pid<>pg_backend_pid() AND usename<>'postgres' GROUP BY usename ORDER BY usename",
            (database,),
        )
        sessions = cur.fetchall()
    if sessions:
        _ensure_control(
            spec,
            run_id,
            resource,
            source_fp,
            current["databases"][target_name]["identity_fingerprint"],
            blocked_stage,
            metadata={
                "stage_fact": "existing_non_admin_sessions_blocked_restore",
                "session_roles": [row[0] for row in sessions],
            },
        )
        raise I4Error(f"{resource} 目标栅栏已提交但仍有非管理员会话；拒绝覆盖目标")


def _assert_target_closed(
    spec: DbSpec,
    resource: str,
    *,
    database: str | None = None,
    verify_sessions: bool = True,
) -> None:
    identity = _db_identity(spec)
    database = database or identity["database"]
    if database != identity["database"]:
        raise I4Error(f"{resource} 目标身份在栅栏检查期间改变")
    blocked_roles = _blocked_application_roles(spec)
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database d "
            "CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a "
            "WHERE d.datname=current_database() AND a.grantee=0 "
            "AND a.privilege_type='CONNECT')"
        )
        if cur.fetchone()[0]:
            raise I4Error(f"{resource} 目标仍通过 PUBLIC 开放 CONNECT")
        for role in blocked_roles:
            cur.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (role, database))
            if cur.fetchone()[0]:
                raise I4Error(f"{resource} 目标应用角色仍可 CONNECT：{role}")
        if verify_sessions:
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s "
                "AND pid<>pg_backend_pid() AND usename <> 'postgres'",
                (database,),
            )
            if cur.fetchone()[0]:
                raise I4Error(f"{resource} 目标仍有非管理员连接；拒绝覆盖目标")


def _table_counts(spec: DbSpec) -> dict[str, int]:
    counts: dict[str, int] = {}
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(name)))
            counts[name] = cur.fetchone()[0]
    return counts


_FINAL_STAGES = {
    "frozen",
    "final_target_fence_intent",
    "final_target_fenced",
    "final_target_drain_blocked",
    "final_restoring",
    "final_restored",
    "transforming",
    "transform_intent",
    "transformed",
    "publish_intent",
    "published",
    "publish_blocked",
    "ready",
}
_PUBLISH_STAGES = {"publish_intent", "published", "publish_blocked", "ready"}
_TRANSFORM_STAGES = {"transforming", "transform_intent", "transformed"}
_NO_RESTORE_STAGES = _TRANSFORM_STAGES | _PUBLISH_STAGES


def _verify_frozen_sources(report: dict[str, Any], specs: dict[str, DbSpec]) -> None:
    for source_name, roles in (
        ("iam_source", IAM_APP_ROLE_SET),
        ("agent_source", RUNTIME_ROLE_SET),
    ):
        dbname = report["databases"][source_name]["database"]
        with _connect(specs[source_name]) as conn, conn.cursor() as cur:
            for role in roles:
                cur.execute("SELECT has_database_privilege(%s,%s,'CONNECT')", (role, dbname))
                if cur.fetchone()[0]:
                    raise I4Error(f"{source_name} 未真实冻结：{role} 仍可连接")
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            if cur.fetchone()[0]:
                raise I4Error(f"{source_name} 尚有存量连接，拒绝最终复制")


def _verify_agent_publish_ready(spec: DbSpec, run_id: str) -> None:
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute("SELECT mode,state,run_id::text FROM i4_separation_state WHERE singleton=1")
        row = cur.fetchone()
        if row != ("remote", "ready", run_id):
            raise I4Error("Agent 目标没有持久 remote/ready 发布状态")
        cur.execute(
            "SELECT name FROM unnest(%s::text[]) AS t(name) "
            "WHERE to_regclass('public.'||quote_ident(name)) IS NOT NULL",
            (list(LEGACY_IDENTITY),),
        )
        leftovers = [value[0] for value in cur.fetchall()]
        if leftovers:
            raise I4Error("Agent 发布前仍有旧身份表：" + ", ".join(sorted(leftovers)))


def _resume_transform_and_publish(
    run_id: str,
    specs: dict[str, DbSpec],
    final_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    report = _phase_preflight(run_id, _FINAL_STAGES)
    if any(
        report["targets"][resource]["state"] in _PUBLISH_STAGES for resource in ("iam", "agent")
    ):
        return _resume_publish(run_id, specs, final_hashes)

    _verify_frozen_sources(report, specs)
    for target_name, resource in (("iam_target", "iam"), ("agent_target", "agent")):
        report = _phase_preflight(run_id, _FINAL_STAGES)
        current = report["targets"][resource]["state"]
        if current == "final_restored":
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][f"{resource}_source"]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "transform_intent",
                metadata={
                    "stage_fact": "non_overwritable_transform_intent_committed",
                    "source_fingerprint": report["databases"][f"{resource}_source"][
                        "identity_fingerprint"
                    ],
                    "target_fingerprint": report["databases"][target_name]["identity_fingerprint"],
                },
            )
        elif current == "transforming":
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][f"{resource}_source"]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "transform_intent",
                metadata={
                    "stage_fact": "legacy_transform_marker_upgraded_to_non_overwritable_intent"
                },
            )
        elif current not in _TRANSFORM_STAGES:
            raise I4Error(f"{resource} 目标处于不可续行转换阶段：{current}")

    report = _phase_preflight(run_id, _FINAL_STAGES)
    if report["databases"]["iam_target"]["flyway_version"] != I4_REQUIRED_IAM_FLYWAY_VERSION:
        raise I4Error("转换意图后的 IAM 目标版本不符；I4 校准端点要求 V23，拒绝恢复或重放")
    if report["databases"]["agent_target"]["alembic_versions"] != ["0020_i4_reference_cleanup"]:
        raise I4Error("转换意图后的 Agent 目标版本不符；拒绝恢复或重放")

    agent_source_fp = report["databases"]["agent_source"]["identity_fingerprint"]
    transformed = _verify_agent_transform_state(
        specs["agent_target"], specs["iam_target"], run_id, agent_source_fp
    )
    transform_result: dict[str, Any] = {"already_committed": transformed}
    if not transformed:
        transform_result = _transform_agent(specs["agent_target"], specs["iam_target"], run_id)
        report = _phase_preflight(run_id, _FINAL_STAGES)
        transformed = _verify_agent_transform_state(
            specs["agent_target"],
            specs["iam_target"],
            run_id,
            report["databases"]["agent_source"]["identity_fingerprint"],
        )
        if not transformed:
            raise I4Error("Agent 转换提交后未通过实际 schema/ACL 校验；目标保持封闭")
        transform_result["committed_and_verified"] = True

    for target_name, resource in (("iam_target", "iam"), ("agent_target", "agent")):
        report = _phase_preflight(run_id, _FINAL_STAGES)
        if report["targets"][resource]["state"] in _TRANSFORM_STAGES:
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][f"{resource}_source"]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "transformed",
                metadata={
                    "stage_fact": "committed_transform_verified_without_restore",
                    "agent_identity_foreign_keys_verified": 14 if resource == "agent" else None,
                },
            )

    report = _phase_preflight(run_id, _FINAL_STAGES)
    _operation_write("agent_publish_state_ready")
    with _connect(specs["agent_target"]) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE i4_separation_state SET mode='remote',state='ready',updated_at=now() "
            "WHERE singleton=1 AND mode='remote' AND state='preparing' AND run_id=%s "
            "AND source_fingerprint=%s",
            (run_id, Jsonb({"identity_fingerprint": agent_source_fp})),
        )
        if cur.rowcount != 1:
            cur.execute(
                "SELECT mode,state,run_id::text,source_fingerprint "
                "FROM i4_separation_state WHERE singleton=1"
            )
            actual = cur.fetchone()
            expected = (
                "remote",
                "ready",
                run_id,
                {"identity_fingerprint": agent_source_fp},
            )
            if actual != expected:
                raise I4Error("Agent 转换已验证但发布状态不匹配；拒绝开放目标")
        else:
            conn.commit()
            _operation_committed("agent_publish_state_ready")

    report = _phase_preflight(run_id, _FINAL_STAGES)
    _verify_agent_publish_ready(specs["agent_target"], run_id)
    published = _resume_publish(run_id, specs, final_hashes, transform_result)
    return {**published, "transform_verified_before_publish_intent": True}


def _fail_closed_publish(run_id: str, specs: dict[str, DbSpec], source_fps: dict[str, str]) -> None:
    for target_name, resource, roles in (
        ("iam_target", "iam", IAM_APP_ROLE_SET),
        ("agent_target", "agent", RUNTIME_ROLE_SET),
    ):
        report = _phase_preflight(run_id, _FINAL_STAGES)
        dbname = report["databases"][target_name]["database"]
        _operation_write(f"publish_fail_closed_revoke:{resource}")
        with _connect(specs[target_name]) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(dbname)
                )
            )
            for role in roles:
                cur.execute(
                    sql.SQL("REVOKE CONNECT,TEMP ON DATABASE {} FROM {}").format(
                        sql.Identifier(dbname), sql.Identifier(role)
                    )
                )
            conn.commit()
        _operation_committed(f"publish_fail_closed_acl_revoked:{resource}")
        with _connect(specs[target_name]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s "
                "AND pid<>pg_backend_pid() AND usename=ANY(%s::text[])",
                (dbname, list(roles)),
            )
            terminated = sum(bool(row[0]) for row in cur.fetchall())
            conn.commit()
        _operation_committed(f"publish_fail_closed_sessions_terminated:{resource}")
        report = _phase_preflight(run_id, _FINAL_STAGES)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            source_fps[resource],
            report["databases"][target_name]["identity_fingerprint"],
            "publish_blocked",
            metadata={
                "stage_fact": "partial_publish_failed_closed",
                "target_connect_revoked": True,
                "known_application_sessions_terminated": terminated,
            },
        )


def _resume_publish(
    run_id: str,
    specs: dict[str, DbSpec],
    final_hashes: dict[str, str] | None = None,
    agent_transform: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = _phase_preflight(run_id, _FINAL_STAGES)
    _verify_frozen_sources(report, specs)
    _verify_agent_publish_ready(specs["agent_target"], run_id)
    for target_name, resource in (("iam_target", "iam"), ("agent_target", "agent")):
        report = _phase_preflight(run_id, _FINAL_STAGES)
        state = report["targets"][resource]
        if state["state"] == "publish_blocked":
            raise I4Error("发布曾发生跨库部分失败并已封闭；拒绝自动覆盖或重试发布，需人工核对")
        if state["state"] not in _PUBLISH_STAGES:
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][f"{resource}_source"]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "publish_intent",
                metadata={
                    "stage_fact": "non_overwritable_publish_intent_committed",
                    "source_fingerprint": report["databases"][f"{resource}_source"][
                        "identity_fingerprint"
                    ],
                    "target_fingerprint": report["databases"][target_name]["identity_fingerprint"],
                },
            )
    try:
        for target_name, resource in (("agent_target", "agent"), ("iam_target", "iam")):
            report = _phase_preflight(run_id, _FINAL_STAGES)
            _verify_agent_publish_ready(specs["agent_target"], run_id)
            current = report["targets"][resource]["state"]
            if current == "ready":
                continue
            if current not in {"publish_intent", "published"}:
                raise I4Error(f"{resource} target activation stage is {current}; refusing publish")
            _set_target_acls(specs[target_name], resource, run_id)
            report = _phase_preflight(run_id, _FINAL_STAGES)
            _ensure_control(
                specs[target_name],
                run_id,
                resource,
                report["databases"][f"{resource}_source"]["identity_fingerprint"],
                report["databases"][target_name]["identity_fingerprint"],
                "published",
                metadata={"stage_fact": "application_connect_grant_committed"},
            )
        report = _phase_preflight(run_id, _FINAL_STAGES)
        for target_name, resource in (("iam_target", "iam"), ("agent_target", "agent")):
            report = _phase_preflight(run_id, _FINAL_STAGES)
            if report["targets"][resource]["state"] != "ready":
                _ensure_control(
                    specs[target_name],
                    run_id,
                    resource,
                    report["databases"][f"{resource}_source"]["identity_fingerprint"],
                    report["databases"][target_name]["identity_fingerprint"],
                    "ready",
                    (final_hashes or {}).get(resource),
                    {"stage_fact": "publish_complete", "both_targets_activated": True},
                )
    except Exception:
        _fail_closed_publish(
            run_id,
            specs,
            {
                "iam": report["databases"]["iam_source"]["identity_fingerprint"],
                "agent": report["databases"]["agent_source"]["identity_fingerprint"],
            },
        )
        raise
    report = _phase_preflight(run_id, {"ready"})
    return {
        "run_id": run_id,
        "stage": "ready",
        "write_effect": True,
        "final_archive_sha256": final_hashes or {},
        "agent_transform": agent_transform or {},
        "target_identity_bindings_verified": True,
        "publish_intent_persisted_before_connect_grants": True,
    }


def _finalize(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _require_execute(args)
    specs = _resource_specs()
    report = _phase_preflight(run_id, _FINAL_STAGES)
    _verify_all_tool_identities(report, specs)
    if any(report["targets"][r]["state"] in _NO_RESTORE_STAGES for r in ("iam", "agent")):
        return _resume_transform_and_publish(run_id, specs)
    _verify_frozen_sources(report, specs)
    reconciliation = _identity_reconciliation(specs["agent_source"], specs["iam_source"])
    refs = _source_ref_integrity(specs["agent_source"], specs["iam_source"])
    catalog = _catalog(specs["agent_source"])
    blockers = _migration_blockers(reconciliation, catalog, refs)
    if blockers:
        raise I4Error("冻结后最终盘点出现阻断，目标保持未启用：" + ", ".join(blockers))
    final_hashes: dict[str, str] = {}
    allowed = set(_FINAL_STAGES)
    for source_name, target_name, resource in (
        ("iam_source", "iam_target", "iam"),
        ("agent_source", "agent_target", "agent"),
    ):
        report = _phase_preflight(run_id, allowed)
        stage = report["targets"][resource]["state"]
        if stage in {
            "frozen",
            "final_target_fence_intent",
            "final_target_fenced",
            "final_target_drain_blocked",
        }:
            _close_target(
                specs[target_name],
                resource,
                run_id=run_id,
                source_fp=report["databases"][source_name]["identity_fingerprint"],
                target_fp=report["databases"][target_name]["identity_fingerprint"],
                intent_stage="final_target_fence_intent",
                done_stage="final_target_fenced",
                blocked_stage="final_target_drain_blocked",
                allowed_stages=allowed,
            )
        else:
            _assert_target_closed(specs[target_name], resource)
        report = _phase_preflight(run_id, allowed)
        archive = _archive_path(run_id, resource, "final")
        digest = _dump(specs[source_name], archive, report["databases"][source_name])
        final_hashes[resource] = digest
        report = _phase_preflight(run_id, allowed)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            report["databases"][source_name]["identity_fingerprint"],
            report["databases"][target_name]["identity_fingerprint"],
            "final_restoring",
            digest,
            {"stage_fact": "final_archive_created", "archive_sha256": digest},
        )
        report = _phase_preflight(run_id, allowed)
        _assert_target_closed(specs[target_name], resource)
        _restore(
            specs[target_name],
            archive,
            clean=True,
            expected_identity=report["databases"][target_name],
        )
        report = _phase_preflight(run_id, allowed)
        _ensure_control(
            specs[target_name],
            run_id,
            resource,
            report["databases"][source_name]["identity_fingerprint"],
            report["databases"][target_name]["identity_fingerprint"],
            "final_restored",
            digest,
            {"stage_fact": "final_restore_committed", "archive_sha256": digest},
        )
    report = _phase_preflight(run_id, allowed)
    iam_version = report["databases"]["iam_target"]["flyway_version"]
    agent_version = report["databases"]["agent_target"]["alembic_versions"]
    if iam_version != I4_REQUIRED_IAM_FLYWAY_VERSION or agent_version != [
        "0020_i4_reference_cleanup"
    ]:
        raise I4Error("最终恢复后的 IAM Flyway/Alembic 版本不符，目标保持封闭")
    published = _resume_transform_and_publish(run_id, specs, final_hashes)
    counts = _table_counts(specs["agent_target"])
    return {
        **published,
        "run_id": run_id,
        "final_archive_sha256": final_hashes,
        "versions": {"iam_flyway": iam_version, "agent_alembic": agent_version},
        "agent_target_table_counts": counts,
        "publish_intent_persisted_before_connect_grants": True,
        "target_connection_isolation": (
            "Agent runtime roles cannot CONNECT to IAM target; "
            "IAM app cannot CONNECT to Agent target"
        ),
    }


def _table_data_hash(spec: DbSpec, schema: str, exclude: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    with _connect(spec) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p') ORDER BY c.relname",
            (schema,),
        )
        tables = [row[0] for row in cur.fetchall() if row[0] not in exclude]
        for table in tables:
            cur.execute(
                sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            digest = hashlib.sha256()
            for (raw,) in cur:
                digest.update(raw.encode())
                digest.update(b"\n")
            result[table] = digest.hexdigest()
    return result


def _recovery_check(args: argparse.Namespace) -> dict[str, Any]:
    run_id = str(uuid.UUID(args.run_id))
    specs = _resource_specs()
    preflight = _phase_preflight(run_id, {"ready"})
    _verify_all_tool_identities(preflight, specs)
    iam_source_hash = _table_data_hash(specs["iam_source"], "iam", {"bootstrap_meta"})
    iam_target_hash = _table_data_hash(specs["iam_target"], "iam", {"bootstrap_meta"})
    identity_tables = set(LEGACY_IDENTITY) | {
        "iam_reference_users",
        "iam_reference_tenants",
        "iam_reference_memberships",
        "i4_separation_state",
    }
    agent_source_hash = _table_data_hash(specs["agent_source"], "public", identity_tables)
    agent_target_hash = _table_data_hash(specs["agent_target"], "public", identity_tables)
    differences = {
        "iam_changed_tables": sorted(
            k
            for k in iam_source_hash.keys() | iam_target_hash.keys()
            if iam_source_hash.get(k) != iam_target_hash.get(k)
        ),
        "agent_changed_tables": sorted(
            k
            for k in agent_source_hash.keys() | agent_target_hash.keys()
            if agent_source_hash.get(k) != agent_target_hash.get(k)
        ),
    }
    target_sessions: dict[str, int] = {}
    for target in ("iam_target", "agent_target"):
        db = _db_identity(specs[target])["database"]
        with _connect(specs[target]) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s "
                "AND pid<>pg_backend_pid() AND usename <> 'postgres'",
                (db,),
            )
            target_sessions[target] = cur.fetchone()[0]
    zero_delta_observed = not any(differences.values()) and not any(target_sessions.values())
    return {
        "run_id": run_id,
        "write_effect": False,
        "source_remains_frozen": True,
        "source_target_data_differences": differences,
        "target_application_sessions": target_sessions,
        "zero_delta_instantaneous_observation": zero_delta_observed,
        "observation_scope": "read_only_instantaneous_snapshot_not_reopen_authorization",
        "safe_to_repoint_without_merge": False,
        "decision": "automatic_recovery_disabled_forward_merge_and_reconciliation_required",
    }


def _recovery_apply(args: argparse.Namespace) -> dict[str, Any]:
    raise I4Error(
        "自动 recover --execute 已永久禁用：不得撤销源栅栏或改写目标；"
        "必须在双边持久栅栏下人工 forward merge 并完成最终对账"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        required=True,
        help="mode-0600 env file; values are never printed",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("inventory", help="read-only database/catalog/UUID plan (default)")
    for name, help_text in (
        ("snapshot", "create privately owned online source snapshots"),
        ("freeze", "revoke old app connections and drain source sessions"),
        ("finalize", "final copy, reference transform, ACL isolation and ready gate"),
        (
            "recover",
            "read-only transient diagnostics; automatic recovery is permanently "
            "disabled; forward merge required",
        ),
    ):
        child = sub.add_parser(name, help=help_text)
        if name != "recover":
            child.add_argument("--run-id", required=True)
            child.add_argument("--execute", action="store_true")
        else:
            child.add_argument("--run-id", required=True)
            child.add_argument(
                "--execute",
                action="store_true",
                help="rejected permanently; manual forward merge and final reconciliation required",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "inventory"
    _OPERATION.clear()
    _OPERATION.update(
        {"command": command, "stage": "preflight", "write_attempted": False, "committed_steps": []}
    )
    try:
        _load_env(args.env_file)
        _operation_stage(f"{command}:preflight")
        if command == "inventory":
            report = _inventory()
            _emit(
                report,
                Path(os.environ.get("I4_AUTHOR_DIR", str(DEFAULT_AUTHOR)))
                / "latest-inventory.json",
            )
            return 0 if not report["blockers"] else 2
        if command == "snapshot":
            report = _snapshot(args)
        elif command == "freeze":
            report = _freeze(args)
        elif command == "finalize":
            report = _finalize(args)
        elif command == "recover":
            report = _recovery_apply(args) if args.execute else _recovery_check(args)
        else:
            raise I4Error("未知命令")
        _emit(report)
        return 0
    except I4Error as exc:
        effect = "possible_partial_effects" if _OPERATION.get("write_attempted") else "not_started"
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "write_effect": effect,
                    "operation": {
                        **_OPERATION,
                        "next_step": "inspect_persisted_run_state_before_retry"
                        if effect == "possible_partial_effects"
                        else "no_database_write_started",
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except psycopg.Error as exc:
        diag = exc.diag
        # PostgreSQL DETAIL can contain rejected row values; keep failures useful
        # without emitting identity fields or credentials from the database.
        primary = getattr(diag, "message_primary", None)
        safe = str(primary) if primary else "database operation failed"
        print(
            json.dumps(
                {
                    "error": f"postgres_{type(exc).__name__}: {safe}",
                    "write_effect": "possible_partial_effects"
                    if _OPERATION.get("write_attempted")
                    else "not_started",
                    "operation": {
                        **_OPERATION,
                        "next_step": "inspect_persisted_run_state_before_retry"
                        if _OPERATION.get("write_attempted")
                        else "no_database_write_started",
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 4
    except Exception as exc:  # noqa: BLE001 - avoid leaking connection details or payloads
        print(
            json.dumps(
                {
                    "error": f"unexpected_{type(exc).__name__}",
                    "write_effect": "possible_partial_effects"
                    if _OPERATION.get("write_attempted")
                    else "not_started",
                    "operation": {
                        **_OPERATION,
                        "next_step": "inspect_persisted_run_state_before_retry"
                        if _OPERATION.get("write_attempted")
                        else "no_database_write_started",
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
