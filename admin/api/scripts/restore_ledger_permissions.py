"""恢复流程：在**开放读取前**重施台账权限与 RLS 策略（幂等）。

备份恢复若丢失 ACL（例如 `pg_dump --no-privileges`），运行角色会被直接拒绝，
服务表现为 503。本脚本复用迁移 0007 的同一份语句（单一事实源，不复制 DDL），
在恢复后、开放读取前执行；同时可验证「已删除原文」不会被重新激活。

用法（owner 连接，指向被恢复的库）：

    DATABASE_URI=postgresql://air_agent:...@host:15432/air_agent \\
      .venv/bin/python scripts/restore_ledger_permissions.py [--verify-only]

退出码 0 = 权限已就绪；非 0 = 环境不满足（角色缺失/连接失败），不得开放读取。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
RUNTIME_ROLE = "air_agent_runtime"


def _load_migration():
    """按路径加载 0007 迁移模块（复用其语句构造，不重复 DDL）."""
    path = next(VERSIONS.glob("0007_*.py"))
    spec = importlib.util.spec_from_file_location("ledger_0007", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只校验当前权限/策略是否就绪，不做修改",
    )
    args = parser.parse_args()

    engine = create_engine(get_settings().sqlalchemy_uri, future=True)
    migration = _load_migration()

    def role_exists(name: str) -> bool:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name}
                ).scalar()
            )

    if not role_exists(RUNTIME_ROLE):
        print(f"运行角色 {RUNTIME_ROLE} 缺失：先执行 bootstrap_tenant_db.py", file=sys.stderr)
        return 2

    if not args.verify_only:
        statements = migration.ledger_grant_statements(role_checker=role_exists)
        with engine.connect() as conn:
            tables = set(
                conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )

        def module_for(filename):
            spec = importlib.util.spec_from_file_location(filename, VERSIONS / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        if "agent_feedback_current" in tables:
            statements.extend(
                module_for("0011_feedback.py").feedback_grant_statements(role_checker=role_exists)
            )
        if "agent_capability_selections" in tables:
            statements.extend(
                module_for("0013s_capability_selection.py").selection_grant_statements()
            )
        if "agent_model_observations" in tables:
            module = module_for("0012_observability.py")

            class Capture:
                @staticmethod
                def execute(statement):
                    statements.append(statement)

            module.op = Capture
            module._apply_rls_and_grants()
        from app.services.enterprise_permissions import permission_statements

        statements.extend(permission_statements(tables))
        if "agent_model_prices" in tables:
            from app.services.enterprise_permissions import pricing_permission_statements

            statements.extend(pricing_permission_statements())
        if "agent_observation_trace_links" in tables:
            from app.services.enterprise_permissions import delivery_permission_statements

            statements.extend(delivery_permission_statements())
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
        print(f"已重施 {len(statements)} 条授权/策略语句")

    with engine.connect() as conn:
        policies = conn.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename LIKE 'agent_%'")
        ).scalar()
        grants = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.role_table_grants"
                " WHERE grantee = :r AND table_name LIKE 'agent_%'"
            ),
            {"r": RUNTIME_ROLE},
        ).scalar()
        deleted_refs = conn.execute(
            text("SELECT count(*) FROM agent_content_refs WHERE deleted_at IS NOT NULL")
        ).scalar()
        open_deletions = conn.execute(
            text("SELECT count(*) FROM agent_thread_deletions WHERE state <> 'completed'")
        ).scalar()
    print(
        f"policies={policies} grants={grants} "
        f"deleted_content_refs={deleted_refs} open_deletions={open_deletions}"
    )
    if not policies or not grants:
        print("权限未就绪：不得开放读取", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
