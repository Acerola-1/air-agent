"""保存逐模型远端跨度映射与物理网络尝试证据。"""

from alembic import op

revision = "0012o_delivery"
down_revision = "0012o_pricing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agent_tool_call_attempts ADD COLUMN network_attempted boolean")
    op.execute("""CREATE TABLE agent_observation_trace_links (
        tenant_id uuid NOT NULL, user_id uuid NOT NULL, attempt_id uuid NOT NULL,
        observation_id uuid NOT NULL, provider text NOT NULL, project_scope text NOT NULL,
        trace_id text NOT NULL, remote_observation_id text,
        sync_state text NOT NULL DEFAULT 'pending'
          CHECK(sync_state IN ('pending','confirmed','failed')),
        synced_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,observation_id,provider,project_scope,trace_id),
        FOREIGN KEY(tenant_id,observation_id,attempt_id)
          REFERENCES agent_model_observations(tenant_id,observation_id,attempt_id)
          ON DELETE CASCADE)""")
    from app.services.enterprise_permissions import delivery_permission_statements

    for statement in delivery_permission_statements():
        op.execute(statement)


def downgrade():
    raise RuntimeError("不能自动删除实际网络和Trace对应证据")
