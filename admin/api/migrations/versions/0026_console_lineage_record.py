# ruff: noqa: E501 -- SQL替换模板保留与0025一致的完整匹配文本。
"""修正不同处理表共享触发器的record字段解析，不改已执行revision。"""

from alembic import op

revision = "0026_console_lineage_record"
down_revision = "0025_console_integrity"
branch_labels = None
depends_on = None


def lineage_record_statements():
    return [
        """DO $$
    DECLARE definition text;
    BEGIN
      SELECT pg_get_functiondef('public.console_check_lineage()'::regprocedure) INTO definition;
      definition := replace(definition,
        'TG_TABLE_NAME=''agent_feedback_case_notes'' AND NEW.user_id IS DISTINCT FROM parent.user_id',
        'TG_TABLE_NAME=''agent_feedback_case_notes'' AND (to_jsonb(NEW)->>''user_id'')::uuid IS DISTINCT FROM parent.user_id');
      EXECUTE definition;
    END $$"""
    ]


def upgrade():
    for statement in lineage_record_statements():
        op.execute(statement)


def downgrade():
    raise NotImplementedError("不回退同源校验")
