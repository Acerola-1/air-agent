"""派生来源到期/墓碑立即禁读；不改已执行的0028。"""

from alembic import op

revision = "0029_insight_retention"
down_revision = "0028_demand_insights"
branch_labels = None
depends_on = None


def retention_statements():
    conditions = {
        "agent_question_sources": "(retention_until IS NULL OR retention_until>now())"
        " AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
        " WHERE d.tenant_id=agent_question_sources.tenant_id"
        " AND d.thread_id=agent_question_sources.thread_id)",
        "agent_question_intents": "content_expiry>now() AND EXISTS"
        " (SELECT 1 FROM agent_question_sources s WHERE s.id=source_id"
        " AND s.tenant_id=agent_question_intents.tenant_id)",
        "agent_question_intent_contents": "EXISTS (SELECT 1 FROM agent_question_intents i"
        " WHERE i.id=intent_id AND i.tenant_id=agent_question_intent_contents.tenant_id)",
    }
    statements = []
    for table, condition in conditions.items():
        statements.extend(
            [
                f"DROP POLICY IF EXISTS insight_retention ON {table}",
                f"CREATE POLICY insight_retention ON {table} AS RESTRICTIVE FOR SELECT"
                f" TO air_agent_runtime USING ({condition})",
            ]
        )
    return statements


def upgrade():
    for statement in retention_statements():
        op.execute(statement)


def downgrade():
    raise RuntimeError("不能回退已实施的派生正文保留期保护")
