# ruff: noqa: E501 -- 一条触发器分支是0026的精确匹配模板，保留原始SQL文本。
"""补齐后台正文元数据、同源约束与最小写权限；不改已执行0024。"""

from alembic import op

revision = "0025_console_integrity"
down_revision = "0024_console_read_model"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE agent_answer_contents ADD CONSTRAINT uq_answer_content_id_tenant
        UNIQUE(id,tenant_id);
        CREATE TABLE agent_answer_content_metadata (
            id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
            thread_id text NOT NULL,
            answer_id uuid NOT NULL, content_version integer NOT NULL,
            content_sha256 text NOT NULL, total_chars integer NOT NULL,
            truncated boolean NOT NULL, capture_status text NOT NULL,
            captured_at timestamptz, retention_until timestamptz,
            UNIQUE(tenant_id,answer_id,content_version),
            FOREIGN KEY(id,tenant_id) REFERENCES agent_answer_contents(id,tenant_id)
            ON DELETE CASCADE
        );
        INSERT INTO agent_answer_content_metadata
            SELECT
            id,tenant_id,user_id,thread_id,answer_id,content_version,content_sha256,total_chars,
                   truncated,capture_status,captured_at,retention_until FROM agent_answer_contents;
    """)
    for statement in console_integrity_statements():
        op.execute(statement)


def console_integrity_statements():
    """恢复权限时与0024按顺序重施；元数据不含任何正文或摘要。"""
    role = "air_agent_runtime"
    tenant = "tenant_id=NULLIF(current_setting('air_agent.tenant_id',true),'')::uuid"
    own = "user_id=NULLIF(current_setting('air_agent.user_id',true),'')::uuid"
    ops = "current_setting('air_agent.agent_operations',true)='on'"
    manage = "current_setting('air_agent.feedback_manage',true)='on'"
    cleanup = "current_setting('air_agent.deletion_cleanup',true)='on'"
    content = "current_setting('air_agent.answer_content',true)='on'"
    statements = [
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON agent_answer_content_metadata TO {role}",
        "ALTER TABLE agent_answer_content_metadata ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE agent_answer_content_metadata FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS console_metadata_read ON agent_answer_content_metadata",
        (
            f"CREATE POLICY console_metadata_read ON agent_answer_content_metadata FOR SELECT "
            f"TO {role} USING ({tenant} AND ({own} OR {ops}))"
        ),
        "DROP POLICY IF EXISTS console_metadata_write ON agent_answer_content_metadata",
        (
            f"CREATE POLICY console_metadata_write ON agent_answer_content_metadata FOR ALL "
            f"TO {role} USING ({tenant} AND pg_trigger_depth()>0 AND ({own} OR {ops})) WITH "
            f"CHECK ({tenant} AND pg_trigger_depth()>0 AND ({own} OR {ops}))"
        ),
        "DROP POLICY IF EXISTS console_metadata_live ON agent_answer_content_metadata",
        (
            f"CREATE POLICY console_metadata_live ON agent_answer_content_metadata AS "
            f"RESTRICTIVE FOR ALL TO {role} USING (NOT EXISTS(SELECT 1 FROM "
            f"agent_thread_deletions d WHERE "
            f"d.tenant_id=agent_answer_content_metadata.tenant_id AND "
            f"d.thread_id=agent_answer_content_metadata.thread_id) OR {cleanup}) WITH CHECK "
            f"(NOT EXISTS(SELECT 1 FROM agent_thread_deletions d WHERE "
            f"d.tenant_id=agent_answer_content_metadata.tenant_id AND "
            f"d.thread_id=agent_answer_content_metadata.thread_id) OR {cleanup})"
        ),
        "DROP POLICY IF EXISTS answer_content_scope ON agent_answer_contents",
        "DROP POLICY IF EXISTS console_body_read ON agent_answer_contents",
        (
            f"CREATE POLICY console_body_read ON agent_answer_contents FOR SELECT TO {role} "
            f"USING ({tenant} AND ({own} OR ({ops} AND {content})))"
        ),
        "DROP POLICY IF EXISTS console_body_insert ON agent_answer_contents",
        (
            f"CREATE POLICY console_body_insert ON agent_answer_contents FOR INSERT TO {role} "
            f"WITH CHECK ({tenant} AND {own})"
        ),
        "DROP POLICY IF EXISTS console_body_repair ON agent_answer_contents",
        (
            f"CREATE POLICY console_body_repair ON agent_answer_contents FOR UPDATE TO {role} "
            f"USING ({tenant} AND {own} AND capture_status='capture_failed') WITH CHECK "
            f"({tenant} AND {own} AND capture_status='captured')"
        ),
        "DROP POLICY IF EXISTS console_body_delete ON agent_answer_contents",
        (
            f"CREATE POLICY console_body_delete ON agent_answer_contents FOR DELETE TO {role} "
            f"USING ({tenant} AND {cleanup})"
        ),
    ]
    # SELECT仍按0024分级，写策略单独要求经权限核验的事务GUC。
    for table, old, read_expr in [
        ("agent_feedback_cases", "feedback_case_scope", f"{tenant} AND ({own} OR {ops})"),
        ("agent_feedback_case_actions", "feedback_case_action_scope", f"{tenant} AND {ops}"),
        (
            "agent_feedback_case_notes",
            "feedback_case_note_scope",
            (
                f"{tenant} AND ({own} OR ({ops} AND "
                f"current_setting('air_agent.feedback_content',true)='on'))"
            ),
        ),
    ]:
        statements += [f"DROP POLICY IF EXISTS {old} ON {table}"]
        for command in ("read", "insert", "update", "delete"):
            statements.append(f"DROP POLICY IF EXISTS console_{command} ON {table}")
        statements += [
            f"CREATE POLICY console_read ON {table} FOR SELECT TO {role} USING ({read_expr})",
            (
                f"CREATE POLICY console_insert ON {table} FOR INSERT TO {role} WITH CHECK "
                f"({tenant} AND {ops} AND {manage})"
            ),
            (
                f"CREATE POLICY console_delete ON {table} FOR DELETE TO {role} USING "
                f"({tenant} AND {cleanup})"
            ),
        ]
        if table == "agent_feedback_cases":
            statements.append(
                f"CREATE POLICY console_update ON {table} FOR UPDATE TO {role} USING "
                f"({tenant} AND {ops} AND {manage}) WITH CHECK ({tenant} AND {ops} AND "
                f"{manage})"
            )
        else:
            statements.append(f"REVOKE UPDATE ON {table} FROM {role}")
    statements += [
        """CREATE OR REPLACE FUNCTION console_check_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE parent record;
        BEGIN
          IF TG_TABLE_NAME IN ('agent_answer_contents','agent_feedback_cases') THEN
            SELECT a.user_id,a.attempt_id,a.content_version,a.message_id,a.confirmed,a.partial,
                   r.id AS request_id,r.thread_id INTO parent
            FROM public.agent_answers a
            JOIN public.agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
            JOIN public.agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id
            WHERE a.id=NEW.answer_id AND a.tenant_id=NEW.tenant_id;
            IF NOT FOUND OR NOT parent.confirmed OR parent.partial
               OR NEW.user_id IS DISTINCT FROM parent.user_id
               OR NEW.attempt_id IS DISTINCT FROM parent.attempt_id
               OR NEW.request_id IS DISTINCT FROM parent.request_id
               OR NEW.thread_id IS DISTINCT FROM parent.thread_id THEN
              RAISE EXCEPTION 'console source mismatch' USING ERRCODE='23514';
            END IF;
            IF TG_TABLE_NAME='agent_answer_contents' THEN
              IF NEW.content_version IS DISTINCT FROM parent.content_version
                 OR (NEW.message_id IS NOT NULL AND NEW.message_id IS DISTINCT
                 FROM parent.message_id) THEN
                RAISE EXCEPTION 'console version mismatch' USING ERRCODE='23514';
              END IF;
              IF TG_OP='UPDATE' AND OLD.capture_status='captured' AND
                (NEW.content_text IS DISTINCT FROM OLD.content_text OR
                NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
                 OR NEW.summary IS DISTINCT FROM OLD.summary OR
                 NEW.capture_status IS DISTINCT FROM OLD.capture_status) THEN
                RAISE EXCEPTION 'confirmed content is immutable' USING ERRCODE='23514';
              END IF;
            END IF;
          ELSE
            SELECT answer_id,thread_id,user_id INTO parent FROM public.agent_feedback_cases
            WHERE id=NEW.case_id AND tenant_id=NEW.tenant_id;
            IF NOT FOUND OR NEW.answer_id IS DISTINCT FROM parent.answer_id
               OR NEW.thread_id IS DISTINCT FROM parent.thread_id THEN
              RAISE EXCEPTION 'console case source mismatch' USING ERRCODE='23514';
            END IF;
            IF TG_TABLE_NAME='agent_feedback_case_notes' AND NEW.user_id IS DISTINCT FROM parent.user_id THEN
              RAISE EXCEPTION 'console note source mismatch' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$""",
        """CREATE OR REPLACE FUNCTION console_sync_metadata() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          INSERT INTO public.agent_answer_content_metadata
            VALUES(NEW.id,NEW.tenant_id,NEW.user_id,NEW.thread_id,NEW.answer_id,NEW.content_version,
              NEW.content_sha256,NEW.total_chars,NEW.truncated,NEW.capture_status,NEW.captured_at,NEW.retention_until)
          ON CONFLICT(id) DO UPDATE SET content_sha256=EXCLUDED.content_sha256,
            total_chars=EXCLUDED.total_chars,truncated=EXCLUDED.truncated,
            capture_status=EXCLUDED.capture_status,captured_at=EXCLUDED.captured_at,
            retention_until=EXCLUDED.retention_until;
          RETURN NEW;
        END $$""",
    ]
    for table in (
        "agent_answer_contents",
        "agent_feedback_cases",
        "agent_feedback_case_actions",
        "agent_feedback_case_notes",
    ):
        statements += [
            f"DROP TRIGGER IF EXISTS console_lineage ON {table}",
            (
                f"CREATE TRIGGER console_lineage BEFORE INSERT OR UPDATE ON {table} FOR EACH "
                f"ROW EXECUTE FUNCTION console_check_lineage()"
            ),
        ]
    statements += [
        "DROP TRIGGER IF EXISTS console_metadata ON agent_answer_contents",
        (
            "CREATE TRIGGER console_metadata AFTER INSERT OR UPDATE ON agent_answer_contents "
            "FOR EACH ROW EXECUTE FUNCTION console_sync_metadata()"
        ),
    ]
    return statements


def downgrade():
    raise NotImplementedError("使用应用回退，不删除正文与审计事实")
