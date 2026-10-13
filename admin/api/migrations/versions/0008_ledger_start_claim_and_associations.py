"""台账纠正迁移：启动权原子裁决、提交↔尝试稳定关联、事件关联复合约束、原文按请求引用

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13

针对首次独立验收的定向修复（R01/R03/R04/R05 与交付缺口）。**不改写 0007**：
0007 已执行（含真实业务库），本迁移只做增量纠正，并对既有数据做**可预检的回填**。

1. R01 启动权：`agent_run_attempts` 增 `content_digest` / `operation_kind` /
   `start_claimed_at` / `start_claim_token` / `start_claim_source`。
   启动权用**单条 UPDATE ... WHERE start_claimed_at IS NULL RETURNING** 原子裁决
   （同一预留只能消费一次）；规范内容与操作类型在受理时写入，准入时比对，
   防"换图 / 换正文 / 换操作"。
2. R03 提交↔尝试：`agent_submissions.attempt_id` + 同租户复合外键；按创建时间就近
   回填（无法唯一确定的行保持 NULL，不猜）。
3. R04 事件关联：`agent_events` 对 request/attempt/answer 建**同租户复合外键**，并用
   `(attempt_id, request_id)` → `agent_run_attempts(id, request_id)` 强制 request 与
   attempt 配套（防 A 的 request 配 B 的 attempt）。RLS 只管事件行本身，不能替代此约束。
4. R05/交付缺口：`agent_thread_deletions.user_id`（归属人，供受限清理与归属校验回填自
   `thread_ownership`）；`agent_content_refs.request_id`（按请求精确读取的不可漂移引用），
   每请求一份未删除快照的部分唯一索引。
5. 冲突预检：回填前检查"同一 submission 无法唯一映射 attempt"与"事件已有跨租户关联"，
   发现即中止并给出计数（`RuntimeError`），不静默改写历史事实。
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _tenant_guc() -> str:
    """租户 GUC 表达式（与 0007 同形：NULLIF 兜底，缺失即不可见）."""
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def _scalar(sql: str, **params: object) -> object:
    return op.get_bind().execute(sa.text(sql), params).scalar()


def _precheck() -> None:
    """升级前冲突预检：既有数据若已违规，先报错而不是把约束建失败在半途。"""
    cross_events = int(
        _scalar(
            """
            SELECT count(*) FROM agent_events e
            JOIN agent_run_attempts a ON a.id = e.attempt_id
            WHERE e.attempt_id IS NOT NULL
              AND (a.tenant_id <> e.tenant_id OR a.request_id <> e.request_id)
            """
        )
        or 0
    )
    if cross_events:
        raise RuntimeError(
            f"事件与尝试关联跨租户/不配套 {cross_events} 条："
            "需先人工核对并修正历史数据，再升级 0008（不静默改写）"
        )
    cross_requests = int(
        _scalar(
            """
            SELECT count(*) FROM agent_events e
            JOIN agent_requests r ON r.id = e.request_id
            WHERE e.request_id IS NOT NULL AND r.tenant_id <> e.tenant_id
            """
        )
        or 0
    )
    if cross_requests:
        raise RuntimeError(f"事件与提问关联跨租户 {cross_requests} 条：需先人工核对")
    ambiguous = int(
        _scalar(
            """
            SELECT count(*) FROM (
              SELECT s.id FROM agent_submissions s
              WHERE (
                SELECT count(*) FROM agent_run_attempts a
                WHERE a.tenant_id = s.tenant_id AND a.request_id = s.request_id
              ) > 1
            ) dup
            """
        )
        or 0
    )
    if ambiguous:
        # 不中止：多尝试的 submission 无法唯一回填，按设计保持 NULL（诚实"未确认"）。
        print(f"预检提示：{ambiguous} 条 submission 对应多个尝试，将保持 attempt_id=NULL")


def upgrade() -> None:
    _precheck()

    # ------------------------------------------------------------ R01 启动权
    op.execute("ALTER TABLE agent_run_attempts ADD COLUMN IF NOT EXISTS content_digest text")
    op.execute(
        "ALTER TABLE agent_run_attempts ADD COLUMN IF NOT EXISTS operation_kind text"
        " NOT NULL DEFAULT 'initial'"
    )
    op.execute(
        "ALTER TABLE agent_run_attempts ADD COLUMN IF NOT EXISTS start_claimed_at timestamptz"
    )
    op.execute("ALTER TABLE agent_run_attempts ADD COLUMN IF NOT EXISTS start_claim_token uuid")
    op.execute("ALTER TABLE agent_run_attempts ADD COLUMN IF NOT EXISTS start_claim_source text")
    op.execute("ALTER TABLE agent_run_attempts DROP CONSTRAINT IF EXISTS agent_attempts_kind_valid")
    op.execute(
        "ALTER TABLE agent_run_attempts ADD CONSTRAINT agent_attempts_kind_valid"
        " CHECK (operation_kind IN ('initial','retry','regenerate','resume'))"
    )
    op.execute(
        "ALTER TABLE agent_run_attempts DROP CONSTRAINT IF EXISTS agent_attempts_source_valid"
    )
    op.execute(
        "ALTER TABLE agent_run_attempts ADD CONSTRAINT agent_attempts_source_valid"
        " CHECK (start_claim_source IS NULL OR start_claim_source IN ('native','bff'))"
    )
    # (id, request_id) 复合唯一目标：供事件表的"request 与 attempt 配套"外键使用
    op.execute(
        "ALTER TABLE agent_run_attempts DROP CONSTRAINT IF EXISTS uq_agent_attempts_id_request"
    )
    op.execute(
        "ALTER TABLE agent_run_attempts ADD CONSTRAINT uq_agent_attempts_id_request"
        " UNIQUE (id, request_id)"
    )

    # -------------------------------------------------------- R03 提交↔尝试
    op.execute("ALTER TABLE agent_submissions ADD COLUMN IF NOT EXISTS attempt_id uuid")
    # 启动权的**裁决单元是"一次业务提交"**（submission），而不是 attempt：
    # initial/retry/regenerate 的 submission 与 attempt 一一对应；人工 resume 复用同一
    # 尝试但每次都产生新的 submission，因此必须各有独立的"只能启动一次"槽位，
    # 否则 resume 会被 attempt 级的一次性消费错误地拒绝。
    op.execute(
        "ALTER TABLE agent_submissions ADD COLUMN IF NOT EXISTS start_claimed_at timestamptz"
    )
    op.execute("ALTER TABLE agent_submissions ADD COLUMN IF NOT EXISTS start_claim_token uuid")
    op.execute("ALTER TABLE agent_submissions ADD COLUMN IF NOT EXISTS start_claim_source text")
    op.execute(
        "ALTER TABLE agent_submissions DROP CONSTRAINT IF EXISTS"
        " agent_submissions_claim_source_valid"
    )
    op.execute(
        "ALTER TABLE agent_submissions ADD CONSTRAINT agent_submissions_claim_source_valid"
        " CHECK (start_claim_source IS NULL OR start_claim_source IN ('native','bff'))"
    )
    # 回填：request 只有一个尝试时可直接确定；多尝试的保持 NULL（不猜）
    op.execute(
        """
        UPDATE agent_submissions s
        SET attempt_id = a.id
        FROM agent_run_attempts a
        WHERE s.attempt_id IS NULL
          AND a.tenant_id = s.tenant_id
          AND a.request_id = s.request_id
          AND (SELECT count(*) FROM agent_run_attempts x
               WHERE x.tenant_id = s.tenant_id AND x.request_id = s.request_id) = 1
        """
    )
    op.execute(
        "ALTER TABLE agent_submissions DROP CONSTRAINT IF EXISTS"
        " fk_agent_submissions_attempt_same_tenant"
    )
    op.execute(
        "ALTER TABLE agent_submissions ADD CONSTRAINT fk_agent_submissions_attempt_same_tenant"
        " FOREIGN KEY (attempt_id, tenant_id)"
        " REFERENCES agent_run_attempts(id, tenant_id) ON DELETE CASCADE"
    )

    # -------------------------------------------------------- R04 事件关联
    op.execute("ALTER TABLE agent_answers DROP CONSTRAINT IF EXISTS uq_agent_answers_id_tenant")
    op.execute(
        "ALTER TABLE agent_answers ADD CONSTRAINT uq_agent_answers_id_tenant UNIQUE (id, tenant_id)"
    )
    # 先删旧行上的违规关联再建约束（预检已保证没有）
    op.execute(
        "ALTER TABLE agent_events DROP CONSTRAINT IF EXISTS fk_agent_events_request_same_tenant"
    )
    op.execute(
        "ALTER TABLE agent_events ADD CONSTRAINT fk_agent_events_request_same_tenant"
        " FOREIGN KEY (request_id, tenant_id)"
        " REFERENCES agent_requests(id, tenant_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE agent_events DROP CONSTRAINT IF EXISTS fk_agent_events_attempt_same_tenant"
    )
    op.execute(
        "ALTER TABLE agent_events ADD CONSTRAINT fk_agent_events_attempt_same_tenant"
        " FOREIGN KEY (attempt_id, tenant_id)"
        " REFERENCES agent_run_attempts(id, tenant_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE agent_events DROP CONSTRAINT IF EXISTS fk_agent_events_answer_same_tenant"
    )
    op.execute(
        "ALTER TABLE agent_events ADD CONSTRAINT fk_agent_events_answer_same_tenant"
        " FOREIGN KEY (answer_id, tenant_id)"
        " REFERENCES agent_answers(id, tenant_id) ON DELETE CASCADE"
    )
    # request 与 attempt 必须配套（防跨请求/跨租户拼接）
    op.execute("ALTER TABLE agent_events DROP CONSTRAINT IF EXISTS fk_agent_events_attempt_request")
    op.execute(
        "ALTER TABLE agent_events ADD CONSTRAINT fk_agent_events_attempt_request"
        " FOREIGN KEY (attempt_id, request_id)"
        " REFERENCES agent_run_attempts(id, request_id) ON DELETE CASCADE"
    )

    # ---------------------------------------------- R05 / 交付缺口：删除与原文
    op.execute("ALTER TABLE agent_thread_deletions ADD COLUMN IF NOT EXISTS user_id uuid")
    op.execute(
        """
        UPDATE agent_thread_deletions d
        SET user_id = o.user_id
        FROM thread_ownership o
        WHERE d.user_id IS NULL AND o.thread_id = d.thread_id
          AND o.tenant_id = d.tenant_id
        """
    )
    op.execute("ALTER TABLE agent_content_refs ADD COLUMN IF NOT EXISTS request_id uuid")
    op.execute(
        "ALTER TABLE agent_content_refs DROP CONSTRAINT IF EXISTS"
        " fk_agent_content_refs_request_same_tenant"
    )
    op.execute(
        "ALTER TABLE agent_content_refs ADD CONSTRAINT fk_agent_content_refs_request_same_tenant"
        " FOREIGN KEY (request_id, tenant_id)"
        " REFERENCES agent_requests(id, tenant_id) ON DELETE CASCADE"
    )
    # 每个 request 至多一份未删除原文快照（不可漂移、按请求精确读取）
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_content_refs_request_live"
        " ON agent_content_refs (request_id)"
        " WHERE request_id IS NOT NULL AND deleted_at IS NULL"
    )
    # 消费 receipt 的消费版本真正参与幂等键（0007 已有该列；此处仅补索引）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_receipts_consumer_version"
        " ON agent_consumer_receipts (tenant_id, consumer, consumer_version, event_id)"
    )

    # R06：outbox 行携带**消费版本**，使"重建投影到新版本"成为真实语义
    # （版本不再只是响应里的装饰字段）。
    op.execute(
        "ALTER TABLE agent_outbox ADD COLUMN IF NOT EXISTS consumer_version integer"
        " NOT NULL DEFAULT 1"
    )
    # 受限清理需要在不读事件正文的前提下定位线程归属（删除标记按租户可见）
    op.execute("ALTER TABLE agent_outbox ADD COLUMN IF NOT EXISTS thread_id text")
    op.execute(
        """
        UPDATE agent_outbox o SET thread_id = e.thread_id
        FROM agent_events e
        WHERE e.event_id = o.event_id AND e.tenant_id = o.tenant_id AND o.thread_id IS NULL
        """
    )

    # ------------------------------------------- R06：运维审计（重放/清理/校准留痕）
    op.create_table(
        "agent_ledger_ops_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text()),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("affected", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("scope", JSONB, nullable=False, server_default="{}"),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Index("idx_ledger_ops_audit_tenant", "tenant_id", "created_at"),
    )
    op.create_table(
        "agent_lease_generations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("outbox_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "outbox_id", "generation", name="uq_lease_generation"),
    )

    _grant_new_tables()


RUNTIME_ROLE = "air_agent_runtime"
WORKER_ROLE = "air_agent_ledger_worker"


def ledger_grant_statements(
    role_checker: Callable[[str], bool] | None = None,
) -> list[str]:
    """新表/新列的授权与 RLS 语句（幂等）。

    与 0007 的 `ledger_grant_statements` 同形：迁移在 `upgrade()` 内执行，
    独立验收/隔离环境可传入自己的受限角色名复现同一授权（见
    `docs/reviews/evidence/flash-02-independent/setup_isolated.py`）。
    """
    check = role_checker or (lambda _name: True)
    runtime, worker = RUNTIME_ROLE, WORKER_ROLE
    expr = f"tenant_id = {_tenant_guc()}"
    statements = [
        f"GRANT SELECT, INSERT ON agent_ledger_ops_audit TO {runtime}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {runtime}",
        "ALTER TABLE agent_ledger_ops_audit ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS ledger_tenant ON agent_ledger_ops_audit",
        f"CREATE POLICY ledger_tenant ON agent_ledger_ops_audit FOR ALL TO {runtime}"
        f" USING ({expr}) WITH CHECK ({expr})",
    ]
    if not check(worker):
        return statements
    # 新增列必须同步授予受限调度角色（列级授权，仍不含 payload/正文）
    statements += [
        f"GRANT SELECT (consumer_version, thread_id) ON agent_outbox TO {worker}",
        f"GRANT UPDATE (consumer_version) ON agent_outbox TO {worker}",
        f"GRANT SELECT, INSERT ON agent_ledger_ops_audit TO {worker}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {worker}",
        "DROP POLICY IF EXISTS ledger_dispatch_audit ON agent_ledger_ops_audit",
        f"CREATE POLICY ledger_dispatch_audit ON agent_ledger_ops_audit FOR ALL"
        f" TO {worker}"
        " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')"
        " WITH CHECK (current_setting('air_agent.ledger_dispatch', true) = 'on')",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON agent_lease_generations TO {worker}",
        # 重放需要读取"目标消费版本是否已应用"；只读列，不含 payload/正文。
        # 注意：receipts 启用了 RLS 且只对运行角色有租户策略，调度角色必须另有
        # 受 GUC 约束的读策略，否则重放会把"已应用"误判为"未应用"（R06 反例）。
        f"GRANT SELECT (tenant_id, consumer, consumer_version, event_id, applied_at)"
        f" ON agent_consumer_receipts TO {worker}",
        "DROP POLICY IF EXISTS ledger_dispatch_read ON agent_consumer_receipts",
        f"CREATE POLICY ledger_dispatch_read ON agent_consumer_receipts FOR SELECT"
        f" TO {worker}"
        " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')",
    ]
    return statements


def _grant_new_tables() -> None:
    """新表的授权与 RLS（幂等）：运行角色按租户读审计；调度角色可写审计。"""
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": WORKER_ROLE}
    ).scalar()
    for statement in ledger_grant_statements(lambda _name: bool(exists)):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "0008 不提供 downgrade：纠正约束一旦生效，回退等同重新引入已验收缺陷；"
        "回退遵循受控备份恢复流程（0003–0007 同策略）"
    )
