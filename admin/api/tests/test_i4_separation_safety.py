from __future__ import annotations

import hashlib

import pytest

from scripts.i4_separation import (
    ALL_APPLICATION_ROLES,
    DbSpec,
    I4Error,
    _assert_source_version,
    _blocked_application_roles,
    _format_postgres_version,
    _legacy_relation_references,
    _migration_blockers,
    _service_value,
    _unclassified_legacy_relation_reference,
    _verify_all_tool_identities,
    _verify_tool_identity,
)


def test_service_file_values_are_literal_libpq_values() -> None:
    assert _service_value("postgres") == "postgres"
    assert _service_value("not-a-conninfo-quoted-value") == "not-a-conninfo-quoted-value"
    with pytest.raises(I4Error):
        _service_value("postgres\nuser=attacker")
    with pytest.raises(I4Error):
        _service_value(" postgres")


@pytest.mark.parametrize(
    ("owner", "expected_blocked"),
    [
        (
            "agent_migrator",
            tuple(role for role in ALL_APPLICATION_ROLES if role != "agent_migrator"),
        ),
        ("postgres", ALL_APPLICATION_ROLES),
    ],
)
def test_transform_connect_guard_only_exempts_actual_database_owner(owner, expected_blocked):
    spec = DbSpec("agent_target", "postgresql://local/agent_target", owner)

    assert _blocked_application_roles(spec) == expected_blocked
    assert ("agent_migrator" in _blocked_application_roles(spec)) is (owner != "agent_migrator")


def test_all_four_real_tool_endpoints_are_probed_against_preflight_identity(monkeypatch):
    import scripts.i4_separation as separation

    names = ("iam_source", "iam_target", "agent_source", "agent_target")
    specs = {name: object() for name in names}
    report = {"databases": {name: {"identity_fingerprint": name} for name in names}}
    opened = []
    verified = []

    class Context:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return "container", [self.name], {"PGSERVICE": self.name}

        def __exit__(self, *_args):
            return False

    def connection(spec):
        name = next(name for name in names if specs[name] is spec)
        opened.append(name)
        return Context(name)

    def verify(spec, expected, prefix, environment):
        verified.append(
            (next(name for name in names if specs[name] is spec), expected, prefix, environment)
        )

    monkeypatch.setattr(separation, "_tool_connection", connection)
    monkeypatch.setattr(separation, "_verify_tool_identity", verify)

    _verify_all_tool_identities(report, specs)

    assert opened == list(names)
    assert [item[0] for item in verified] == list(names)
    assert [item[1] for item in verified] == [report["databases"][name] for name in names]


def test_tool_identity_rejects_client_connected_to_a_different_database(monkeypatch):
    import scripts.i4_separation as separation

    spec = DbSpec("agent_target", "postgresql://local/expected", "postgres")
    expected = {
        "database": "expected",
        "oid": "4242",
        "role": "postgres",
        "system_id_sha256": hashlib.sha256(b"expected-cluster").hexdigest(),
    }
    monkeypatch.setattr(
        separation,
        "_tool_identity",
        lambda *_args: ("wrong", "4242", "expected-cluster", "postgres"),
    )

    with pytest.raises(I4Error, match="实际 pg 客户端连接身份与预检不符"):
        _verify_tool_identity(spec, expected, ["container"], {})


@pytest.mark.parametrize("version", ["22", "24", None])
def test_i4_requires_the_flyway_head_that_contains_tenant_status_api(version):
    with pytest.raises(I4Error, match="V23"):
        _assert_source_version("iam_source", {"flyway_version": version})


def test_i4_accepts_only_flyway_v23_for_the_required_tenant_status_api():
    _assert_source_version("iam_source", {"flyway_version": "23"})


@pytest.mark.parametrize(
    ("version_num", "expected"),
    [(170010, "17.10"), (170000, "17.0"), (100002, "10.2"), (90624, "9.6.24")],
)
def test_postgres_version_num_formats_patch_releases(version_num: int, expected: str) -> None:
    assert _format_postgres_version(version_num) == expected


def test_legacy_relation_scanner_finds_plpgsql_statement_and_ignores_comments() -> None:
    body = """
    BEGIN
      -- SELECT * FROM public.tenants is only a comment
      RETURN (SELECT id FROM public.users LIMIT 1);
    END;
    """
    assert _legacy_relation_references(body) == {"users"}


@pytest.mark.parametrize(
    "body",
    [
        "BEGIN RETURN (SELECT count(*) FROM public.regions r, public.users u); END",
        "BEGIN DELETE FROM public.regions r USING public.users u WHERE r.id=u.id; END",
    ],
)
def test_unclassified_legacy_relation_identifiers_block_custom_functions(body: str) -> None:
    assert _unclassified_legacy_relation_reference(body)


def test_unclassified_legacy_relation_scanner_ignores_comments_and_strings() -> None:
    body = "BEGIN -- users is only a comment\n RETURN 'public.users'; END"
    assert not _unclassified_legacy_relation_reference(body)


def test_trigger_attached_to_legacy_identity_table_blocks_inventory() -> None:
    identity = {
        "users": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "tenants": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "memberships": {"unknown_ids": [], "binding_conflict_ids": []},
    }
    catalog = {
        "identity_foreign_keys": [],
        "functions": [[301, "public", "noop_trigger", "hash", "plpgsql", False, False, False]],
        "triggers": [
            ["public", "users", "users_noop_trigger", "noop_trigger", 301, "BEFORE UPDATE"]
        ],
        "views": [],
        "policies": [],
    }

    assert "trigger_attached_to_legacy_identity_table" in _migration_blockers(identity, catalog, {})


@pytest.mark.parametrize("target", ["roles", "permissions", "auth_sessions"])
def test_unknown_business_foreign_key_to_any_legacy_identity_table_blocks_inventory(
    target: str,
) -> None:
    identity = {
        "users": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "tenants": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "memberships": {"unknown_ids": [], "binding_conflict_ids": []},
    }
    catalog = {
        "identity_foreign_keys": [
            [
                "public",
                "i4_review_unknown_role_fk",
                f"i4_review_unknown_role_fk_{target}_id_fkey",
                "public",
                target,
                f"FOREIGN KEY (identity_id) REFERENCES {target}(id)",
            ]
        ],
        "functions": [],
        "triggers": [],
        "views": [],
        "policies": [],
    }

    assert "unknown_identity_foreign_key" in _migration_blockers(identity, catalog, {})


def test_known_internal_role_and_permission_foreign_keys_are_allowlisted() -> None:
    identity = {
        "users": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "tenants": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "memberships": {"unknown_ids": [], "binding_conflict_ids": []},
    }
    catalog = {
        "identity_foreign_keys": [
            ["public", "role_permissions", "role_permissions_role_id_fkey", "public", "roles", ""],
            [
                "public",
                "role_permissions",
                "role_permissions_permission_id_fkey",
                "public",
                "permissions",
                "",
            ],
            ["public", "user_roles", "user_roles_role_id_fkey", "public", "roles", ""],
            [
                "public",
                "membership_roles",
                "fk_membership_roles_role_same_tenant",
                "public",
                "roles",
                "",
            ],
            ["public", "permissions", "permissions_parent_id_fkey", "public", "permissions", ""],
        ],
        "functions": [],
        "triggers": [],
        "views": [],
        "policies": [],
    }

    assert "unknown_identity_foreign_key" not in _migration_blockers(identity, catalog, {})


def test_known_constraint_from_non_public_schema_is_not_treated_as_internal() -> None:
    identity = {
        "users": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "tenants": {
            "unknown_ids": [],
            "same_name_different_id_source_ids": [],
            "same_id_name_mismatch_ids": [],
        },
        "memberships": {"unknown_ids": [], "binding_conflict_ids": []},
    }
    catalog = {
        "identity_foreign_keys": [
            [
                "review_extension",
                "membership_roles",
                "fk_membership_roles_role_same_tenant",
                "public",
                "roles",
                "FOREIGN KEY (role_id) REFERENCES public.roles(id)",
            ]
        ],
        "functions": [],
        "triggers": [],
        "views": [],
        "policies": [],
    }

    assert "unknown_identity_foreign_key" in _migration_blockers(identity, catalog, {})
