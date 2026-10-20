"""受保护的知识引用存储（change add-shared-knowledge-retrieval-tool 6.2）.

只保存非秘密引用映射与受控短摘录；完整原文仍由知识服务按每次读取重新授权。
请求角色为非 owner 的 air_agent_runtime，FORCE RLS + 事务本地 air_agent.* GUC
与同企业线程墓碑共同隔离。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_knowledge_citations"
down_revision = "0022_retire_mcp_bindings"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"


def upgrade() -> None:
    op.create_table(
        "knowledge_citations",
        sa.Column("citation_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("record_id", sa.String(128), nullable=False),
        sa.Column("evidence_index", sa.Integer(), nullable=False),
        sa.Column("workspace", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(256)),
        sa.Column("doc_name", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.String(256)),
        sa.Column("kind", sa.String(64)),
        sa.Column("source_version", sa.Text()),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("location_json", sa.Text()),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("query_sha256", sa.String(64)),
        sa.Column("outcome", sa.String(32)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "thread_id",
            "record_id",
            "evidence_index",
            "source_sha256",
            name="knowledge_citations_source_uk",
        ),
    )
    op.create_index(
        "idx_knowledge_citations_owner",
        "knowledge_citations",
        ["tenant_id", "user_id", "thread_id"],
    )
    op.create_index(
        "idx_knowledge_citations_expiry",
        "knowledge_citations",
        ["expires_at"],
    )
    op.execute("ALTER TABLE knowledge_citations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_citations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY knowledge_citations_owner ON knowledge_citations FOR ALL TO {RUNTIME_ROLE} "
        "USING (tenant_id = current_setting('air_agent.tenant_id', true) "
        "AND user_id = current_setting('air_agent.user_id', true)) "
        "WITH CHECK (tenant_id = current_setting('air_agent.tenant_id', true) "
        "AND user_id = current_setting('air_agent.user_id', true))"
    )
    op.execute(
        f"CREATE POLICY knowledge_citations_thread_deleted ON knowledge_citations "
        f"AS RESTRICTIVE FOR ALL TO {RUNTIME_ROLE} "
        "USING (NOT EXISTS (SELECT 1 FROM agent_thread_deletions d "
        "WHERE d.tenant_id::text = knowledge_citations.tenant_id "
        "AND d.thread_id = knowledge_citations.thread_id)) "
        "WITH CHECK (NOT EXISTS (SELECT 1 FROM agent_thread_deletions d "
        "WHERE d.tenant_id::text = knowledge_citations.tenant_id "
        "AND d.thread_id = knowledge_citations.thread_id))"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_citations TO {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    # 引用映射是删除/恢复的审计事实，不做破坏性回退。
    raise RuntimeError("不自动丢弃知识引用映射；如需回退请使用已验证的应用版本")
