"""Tenant-isolated, content-addressed Basic QA MCP embedding cache."""

from alembic import op

revision = "0030_bqa_mcp_index"
down_revision = "0029_insight_retention"
branch_labels = None
depends_on = None


def upgrade():
    """Add cache tables without adding extensions or request-path owner access."""
    op.execute("""
        CREATE TABLE bqa_mcp_index_documents (
            tenant_id uuid NOT NULL,
            scope_key text NOT NULL,
            model_key text NOT NULL,
            document_key text NOT NULL,
            tool_id text NOT NULL,
            schema_version text NOT NULL,
            embedding double precision[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, scope_key, model_key, document_key),
            CHECK (cardinality(embedding)>0)
        )
    """)
    op.execute("""
        CREATE TABLE bqa_mcp_index_generations (
            tenant_id uuid NOT NULL,
            scope_key text NOT NULL,
            model_key text NOT NULL,
            catalog_version text NOT NULL,
            documents jsonb NOT NULL,
            published_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, scope_key, model_key, catalog_version),
            CHECK (jsonb_typeof(documents)='array')
        )
    """)
    for table in ("bqa_mcp_index_documents", "bqa_mcp_index_generations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO air_agent_runtime")
        op.execute(
            f"CREATE POLICY tenant_scope ON {table} TO air_agent_runtime "
            "USING (tenant_id=NULLIF(current_setting('air_agent.tenant_id',true),'')::uuid) "
            "WITH CHECK (tenant_id=NULLIF(current_setting('air_agent.tenant_id',true),'')::uuid)"
        )


def downgrade():
    """Remove only this rebuildable cache, never business or conversation data."""
    op.execute("DROP TABLE bqa_mcp_index_generations")
    op.execute("DROP TABLE bqa_mcp_index_documents")
