"""价格纠错保留旧版本；运行角色仅在已授权事务内发布价格。"""

from alembic import op

revision = "0012o_pricing"
down_revision = "0012o_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agent_model_prices ADD COLUMN retired_at timestamptz")
    op.execute("ALTER TABLE agent_model_prices ADD COLUMN supersedes_price_ref text")
    op.execute(
        "ALTER TABLE agent_model_prices ADD CONSTRAINT fk_price_supersedes "
        "FOREIGN KEY (tenant_id,supersedes_price_ref) "
        "REFERENCES agent_model_prices(tenant_id,price_ref)"
    )
    op.execute("ALTER TABLE agent_model_prices DROP CONSTRAINT ex_model_prices_no_overlap")
    op.execute(
        "ALTER TABLE agent_model_prices ADD CONSTRAINT ex_model_prices_no_overlap "
        "EXCLUDE USING gist (provider WITH =, model WITH =, tenant_id WITH =, "
        "tstzrange(effective_from,COALESCE(effective_to,'infinity'::timestamptz),'[)') "
        "WITH &&) WHERE (retired_at IS NULL)"
    )
    from app.services.enterprise_permissions import pricing_permission_statements

    for statement in pricing_permission_statements():
        op.execute(statement)


def downgrade():
    raise RuntimeError("不能自动丢弃价格纠错历史")
