"""Read-only check for the IAM insights permission catalog."""

from __future__ import annotations

from pathlib import Path

PROPOSED_MIGRATION = """\
-- 与 IAM V28 迁移一致的幂等登记片段（供缺失目录时核对）。
-- Change7：只登记 agent:insights:manage，不授予企业、角色或 root。
SELECT set_config('app.is_platform', 'on', true);

INSERT INTO iam.permissions
    (id, code, name_zh, scope, grantable_to_tenants, resource_code, sort)
VALUES
    ('01930000-0000-4000-8000-000000000515', 'agent:insights:manage',
     '运营需求洞察', 'tenant', true, NULL, 515)
ON CONFLICT (code) DO NOTHING;
"""


def _version_number(path: Path) -> int:
    """Return the numeric Flyway version from a migration filename."""
    return int(path.name.split("__", 1)[0][1:])


def inspect_migrations(migration_dir: Path) -> dict[str, object]:
    """Report whether the insights code exists. Do not modify the directory."""
    files = sorted(migration_dir.glob("V*.sql"), key=_version_number)
    combined = "\n".join(path.read_text() for path in files)
    latest = files[-1].name.split("__", 1)[0] if files else None
    latest_number = _version_number(files[-1]) if files else 0
    return {
        "migration_dir": str(migration_dir),
        "latest_version": latest,
        "feedback_manage_registered": "agent:feedback:manage" in combined,
        "insights_manage_registered": "agent:insights:manage" in combined,
        "proposed_next_version": f"V{latest_number + 1}",
        "proposed_sql": PROPOSED_MIGRATION,
        "applied": False,
    }
