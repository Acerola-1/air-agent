"""修复观测关联、计费状态和删除隔离。"""

from alembic import op

revision = "0012o_integrity"
down_revision = "0011f_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE agent_model_observations ADD CONSTRAINT uq_obs_tenant_attempt "
        "UNIQUE(tenant_id,observation_id,attempt_id)"
    )
    op.execute(
        "ALTER TABLE agent_model_observations ADD CONSTRAINT fk_obs_parent FOREIGN "
        "KEY(tenant_id,parent_observation_id,attempt_id) REFERENCES "
        "agent_model_observations(tenant_id,observation_id,attempt_id)"
    )
    op.execute(
        "ALTER TABLE agent_submissions ADD CONSTRAINT uq_obs_submission_tenant UNIQUE(id,tenant_id)"
    )
    op.execute(
        "ALTER TABLE agent_model_observations ADD CONSTRAINT fk_obs_submission "
        "FOREIGN KEY(submission_id,tenant_id) REFERENCES "
        "agent_submissions(id,tenant_id)"
    )
    op.execute(
        "ALTER TABLE agent_tool_call_attempts ADD CONSTRAINT fk_tool_logical "
        "FOREIGN KEY(tenant_id,logical_call_id) REFERENCES "
        "agent_tool_calls(tenant_id,logical_call_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE agent_cost_revisions ADD CONSTRAINT fk_cost_observation "
        "FOREIGN KEY(tenant_id,observation_id,attempt_id) REFERENCES "
        "agent_model_observations(tenant_id,observation_id,attempt_id) ON DELETE "
        "CASCADE"
    )
    op.execute("ALTER TABLE agent_cost_revisions DROP CONSTRAINT ck_cost_revision_status")
    op.execute(
        "ALTER TABLE agent_cost_revisions ADD CONSTRAINT ck_cost_revision_status "
        "CHECK(measurement_status IN "
        "('measured','usage_missing','invalid_usage','estimated','not_collected','price_unknown'))"
    )
    op.execute("ALTER TABLE agent_trace_links ADD COLUMN observation_id uuid")
    op.execute(
        "ALTER TABLE agent_trace_links ADD CONSTRAINT fk_trace_model_observation "
        "FOREIGN KEY(tenant_id,observation_id,attempt_id) REFERENCES "
        "agent_model_observations(tenant_id,observation_id,attempt_id) ON DELETE "
        "CASCADE"
    )
    op.execute(
        "ALTER TABLE agent_capability_spans ADD CONSTRAINT fk_capability_model "
        "FOREIGN KEY(tenant_id,model_observation_id,attempt_id) REFERENCES "
        "agent_model_observations(tenant_id,observation_id,attempt_id)"
    )
    op.execute(
        "ALTER TABLE agent_model_prices ADD CONSTRAINT ck_prices_nonnegative "
        "CHECK(input_per_unit>=0 AND output_per_unit>=0 AND (cache_read_per_unit IS "
        "NULL OR cache_read_per_unit>=0) AND (cache_write_per_unit IS NULL OR "
        "cache_write_per_unit>=0) AND (reasoning_per_unit IS NULL OR "
        "reasoning_per_unit>=0))"
    )
    from app.services.enterprise_permissions import permission_statements

    for statement in permission_statements():
        op.execute(statement)


def downgrade():
    raise RuntimeError("不自动移除观测完整性约束")
