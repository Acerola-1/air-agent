"""规模复验定位的attempt分页排序索引；不改变事实或授权。"""

from alembic import op

revision = "0027_console_paging_index"
down_revision = "0026_console_lineage_record"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_console_attempt_page ON agent_run_attempts "
        "(tenant_id,admitted_at DESC,id DESC)"
    )


def downgrade():
    op.execute("DROP INDEX ix_console_attempt_page")
