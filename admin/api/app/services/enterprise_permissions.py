"""组合域权限修复与备份恢复共用的可重施策略。"""


def permission_statements(available=None):
    statements = []
    feedback = ("agent_feedback_current", "agent_feedback_operations", "agent_feedback_comments")
    for table in feedback:
        if available is not None and table not in available:
            continue
        guard = (
            f"NOT EXISTS (SELECT 1 FROM agent_thread_deletions d WHERE d.ten"
            f"ant_id={table}.tenant_id AND d.thread_id={table}.thread_id)"
        )
        statements += [
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS feedback_deleted_guard ON {table}",
            (
                f"CREATE POLICY feedback_deleted_guard ON {table} AS RESTRICTIVE FOR ALL TO "
                f"air_agent_runtime USING (({guard}) OR "
                f"current_setting('air_agent.deletion_cleanup', true)='on') WITH CHECK "
                f"({guard})"
            ),
        ]
    for table in (
        "agent_model_observations",
        "agent_capability_spans",
        "agent_tool_calls",
        "agent_tool_call_attempts",
        "agent_cost_revisions",
        "agent_trace_links",
    ):
        guard = (
            f"NOT EXISTS (SELECT 1 FROM agent_run_attempts a JOIN agent_requ"
            f"ests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id JOIN a"
            f"gent_thread_deletions d ON d.thread_id=r.thread_id AND d.tenan"
            f"t_id=r.tenant_id WHERE a.id={table}.attempt_id AND a.tenant_id"
            f"={table}.tenant_id)"
        )
        statements += [
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS observability_deleted_guard ON {table}",
            (
                f"CREATE POLICY observability_deleted_guard ON {table} AS RESTRICTIVE FOR "
                f"ALL TO air_agent_runtime USING (({guard}) OR "
                f"current_setting('air_agent.deletion_cleanup', true)='on') WITH CHECK "
                f"({guard})"
            ),
        ]
    return statements


def pricing_permission_statements():
    """价格可读，发布和退役必须经独立权限校验后的事务上下文。"""
    scope = "tenant_id=NULLIF(current_setting('air_agent.tenant_id',true),'')::uuid"
    manage = "current_setting('air_agent.pricing_manage',true)='on'"
    return [
        "DROP POLICY IF EXISTS observability_tenant ON agent_model_prices",
        "DROP POLICY IF EXISTS observability_price_read ON agent_model_prices",
        "DROP POLICY IF EXISTS observability_price_write ON agent_model_prices",
        f"CREATE POLICY observability_price_read ON agent_model_prices F"
        f"OR SELECT TO air_agent_runtime USING ({scope})",
        f"CREATE POLICY observability_price_write ON agent_model_prices "
        f"FOR INSERT TO air_agent_runtime WITH CHECK ({scope} AND {manage}"
        f")",
        "DROP POLICY IF EXISTS observability_price_update ON agent_model_prices",
        f"CREATE POLICY observability_price_update ON agent_model_prices"
        f" FOR UPDATE TO air_agent_runtime USING ({scope} AND {manage}) "
        f"WITH CHECK ({scope} AND {manage})",
    ]


def delivery_permission_statements():
    """跨度关联继承模型行的租户、本人及永久删除策略。"""
    table = "agent_observation_trace_links"
    visible = (
        f"EXISTS (SELECT 1 FROM agent_model_observations m WHERE m.tenant_id"
        f"={table}.tenant_id AND m.observation_id={table}.observation_id AND"
        f" m.attempt_id={table}.attempt_id AND m.user_id={table}.user_id)"
    )
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"GRANT SELECT,INSERT,UPDATE ON {table} TO air_agent_runtime",
        f"DROP POLICY IF EXISTS observation_visible ON {table}",
        f"CREATE POLICY observation_visible ON {table} FOR ALL TO air_ag"
        f"ent_runtime USING ({visible}) WITH CHECK ({visible})",
    ]
