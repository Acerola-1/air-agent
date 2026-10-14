"""合并两个独立实施分支，保留已执行迁移。"""

revision = "0013_merge"
down_revision = ("0011_feedback", "0012_observability")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("不自动回退已采集事实")
