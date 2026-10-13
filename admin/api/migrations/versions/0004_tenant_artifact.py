"""归档存储租户维度：tool_artifact_storage 增加 tenant_id 与索引

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12

背景：`tool_artifact_storage`/`artifact_computations` 原由 Agent 自动建表。本迁移
**收编 DDL 真相源到 Alembic**（任务 6.2）：新库经 0004 直接得到租户列与索引；
存量库幂等 ADD COLUMN。Agent 侧保留 CREATE TABLE IF NOT EXISTS 兜底（供本地
SQLite 测试），生产以本迁移为准——历史 tenant_id 为 NULL 的行按“未知归属不可
读”规则保持不可访问，由迁移工具按 thread_ownership 回填归属（任务 8.x）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _table_exists() -> bool:
    conn = op.get_bind()
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'tool_artifact_storage'"
            )
        ).scalar()
    )


def upgrade() -> None:
    if _table_exists():
        # 存量库（Agent 旧版自动建表）：幂等补齐全部治理列再建索引。
        # 只补 tenant_id 会在"更早的旧表（无 user_id/thread_id/run_id 等列）"
        # 上因索引引用缺列而失败——评审复现过空库分支缺空格缺陷，此处对
        # 旧库分支同样做列级防御（ADD COLUMN IF NOT EXISTS 幂等）。
        legacy_columns = {
            "tenant_id": "TEXT",
            "user_id": "TEXT",
            "thread_id": "TEXT",
            "run_id": "TEXT",
            "parent_artifact_id": "TEXT",
            "kind": "TEXT DEFAULT 'raw'",
            "status": "TEXT DEFAULT 'stored'",
            "expires_at": "TEXT",
            "processing_metadata": "TEXT DEFAULT '{}'",
        }
        for name, declaration in legacy_columns.items():
            op.execute(
                f"ALTER TABLE tool_artifact_storage ADD COLUMN IF NOT EXISTS {name} {declaration}"
            )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_artifact_tenant "
            "ON tool_artifact_storage(tenant_id, user_id, thread_id)"
        )
    else:
        # 新库：完整建表（与 Agent 侧兜底 DDL 保持列一致）
        op.execute(
            """
            CREATE TABLE tool_artifact_storage (
                artifact_id TEXT PRIMARY KEY,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                char_length INTEGER NOT NULL,
                raw_payload TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL,
                tenant_id TEXT,
                user_id TEXT,
                thread_id TEXT,
                run_id TEXT,
                parent_artifact_id TEXT,
                kind TEXT DEFAULT 'raw',
                status TEXT DEFAULT 'stored',
                expires_at TEXT,
                processing_metadata TEXT DEFAULT '{}'
            )
            """
        )
        op.execute(
            "CREATE INDEX idx_tool_artifact_owner ON tool_artifact_storage(user_id, thread_id)"
        )
        op.execute(
            "CREATE INDEX idx_tool_artifact_tenant "
            "ON tool_artifact_storage(tenant_id, user_id, thread_id)"
        )
    op.execute(
        "CREATE TABLE IF NOT EXISTS artifact_computations ("
        "artifact_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at TEXT NOT NULL,"
        " attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (artifact_id, run_id))"
    )


def downgrade() -> None:
    raise NotImplementedError("归档表回滚只允许受控备份恢复（0003 同策略），不自动 DROP")
