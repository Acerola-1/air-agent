"""远程桥接表强制作用域，修复首交未启用 RLS；不修改已执行的 0014–0017。"""

from alembic import op

revision = "0018_iam_scope_guards"
down_revision = "0017_iam_session_token"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("GRANT UPDATE (account) ON users TO air_agent_runtime")
    for table, predicate in (
        ("agent_iam_sessions", "handle_hash = current_setting('air_agent.iam_handle_hash', true)"),
        (
            "agent_iam_ticket_bindings",
            "ticket_hash = current_setting('air_agent.iam_ticket_hash', true)",
        ),
        ("iam_reference_users", "id::text = current_setting('air_agent.user_id', true)"),
        ("iam_reference_tenants", "id::text = current_setting('air_agent.tenant_id', true)"),
        (
            "iam_reference_memberships",
            "tenant_id::text = current_setting('air_agent.tenant_id', true)"
            " AND user_id::text = current_setting('air_agent.user_id', true)",
        ),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY iam_scope ON {table} FOR ALL TO air_agent_runtime "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade():
    for table in (
        "agent_iam_sessions",
        "agent_iam_ticket_bindings",
        "iam_reference_users",
        "iam_reference_tenants",
        "iam_reference_memberships",
    ):
        op.execute(f"DROP POLICY iam_scope ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE UPDATE (account) ON users FROM air_agent_runtime")
