"""企业身份域：租户/部门/成员/角色作用域/可撤销会话/MCP 绑定/线程归属/迁移台账 + RLS

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12

要点（openspec change add-tenant-identity-access 设计 §2/§5）：
- 同企业一致性全部下沉为复合外键，应用层绕不过（部门父子、成员部门、
  成员角色、成员关注城市、成员级 MCP 绑定）。
- 平台角色 tenant_id 为 NULL；membership_roles 的复合 FK 永不匹配 NULL，
  因此成员只能被授予企业角色。
- RLS 只对运行角色 air_agent_runtime 生效（非 owner、非 BYPASSRLS）；
  上下文由事务本地 GUC（air_agent.tenant_id / air_agent.user_id /
  air_agent.session_id / air_agent.platform / air_agent.login）设置，
  GUC 缺失时策略求值为 false —— 失败关闭。
- 迁移执行角色（owner）与运行角色分离；migration_ledger 不授权给运行角色。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")

RUNTIME_ROLE = "air_agent_runtime"


def _guc_tenant() -> str:
    # NULLIF 兜底：GUC 被设为空串（未选择企业的会话）时求值为 NULL → 策略不通过，
    # 而不是 `''::uuid` 抛转换错误——“失败关闭”必须是不可见，不是报错。
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def upgrade() -> None:
    # ------------------------------------------------------------- users 扩展
    op.add_column(
        "users",
        sa.Column("auth_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # ------------------------------------------------------------- tenants
    op.create_table(
        "tenants",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("auth_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(r"code ~ '^[a-z0-9][a-z0-9_-]{1,62}$'", name="tenants_code_ascii"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="tenants_status_valid"),
    )

    # ------------------------------------------------------------- departments
    op.create_table(
        "departments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("dept_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_departments_id_tenant"),
        sa.ForeignKeyConstraint(
            ["parent_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="RESTRICT",
            name="fk_departments_parent_same_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "dept_code", name="uq_departments_tenant_code"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="departments_no_self_parent"
        ),
    )
    op.create_index("idx_departments_tenant", "departments", ["tenant_id"])
    # 无环校验：延迟约束触发器沿 parent 链上溯，出现环即中止事务。
    # （CHECK 只能挡自环，两节点以上的环必须由触发器在提交前发现）
    op.execute(
        """
        CREATE FUNCTION departments_reject_cycle() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            seen uuid[] := '{}';
            cursor_id uuid;
        BEGIN
            cursor_id := NEW.parent_id;
            WHILE cursor_id IS NOT NULL LOOP
                IF cursor_id = NEW.id OR cursor_id = ANY (seen) THEN
                    RAISE EXCEPTION '部门父子关系形成环';
                END IF;
                seen := seen || cursor_id;
                SELECT parent_id INTO cursor_id FROM departments WHERE id = cursor_id;
            END LOOP;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_departments_no_cycle
        AFTER INSERT OR UPDATE OF parent_id ON departments
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION departments_reject_cycle();
        """
    )

    # --------------------------------------------------- tenant_memberships
    op.create_table(
        "tenant_memberships",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("department_id", UUID),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_memberships_id_tenant"),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="RESTRICT",
            name="fk_memberships_department_same_tenant",
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="memberships_status_valid"),
    )
    op.create_index("idx_memberships_user", "tenant_memberships", ["user_id"])
    op.create_index("idx_memberships_tenant", "tenant_memberships", ["tenant_id"])

    # ------------------------------------------------------------- roles 作用域化
    # 旧的表级 unique(code) 收敛为“按作用域唯一”：平台角色全局唯一、企业角色同企业唯一
    op.drop_constraint("roles_code_key", "roles", type_="unique")
    op.add_column(
        "roles", sa.Column("scope", sa.Text(), nullable=False, server_default=sa.text("'platform'"))
    )
    op.add_column(
        "roles", sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"))
    )
    op.create_check_constraint(
        "roles_scope_tenant_consistent",
        "roles",
        "(scope = 'platform' AND tenant_id IS NULL)"
        " OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
    )
    op.create_unique_constraint("uq_roles_id_tenant", "roles", ["id", "tenant_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_roles_platform_code ON roles (code) WHERE scope = 'platform'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_roles_tenant_code ON roles (tenant_id, code) WHERE scope = 'tenant'"
    )
    op.create_index("idx_roles_tenant", "roles", ["tenant_id"])

    # --------------------------------------------------- membership_roles
    op.create_table(
        "membership_roles",
        sa.Column(
            "membership_id",
            UUID,
            sa.ForeignKey("tenant_memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("granted_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            name="fk_membership_roles_membership_same_tenant",
            ondelete="CASCADE",
        ),
        # 平台角色 tenant_id 为 NULL，此复合 FK 永不匹配 → 成员只能被授予企业角色
        sa.ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            name="fk_membership_roles_role_same_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_membership_roles_role", "membership_roles", ["role_id"])

    # --------------------------------------------------- 成员关注城市
    op.create_table(
        "membership_followed_regions",
        sa.Column(
            "membership_id",
            UUID,
            sa.ForeignKey("tenant_memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("region_code", sa.Text(), sa.ForeignKey("regions.code"), primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            name="fk_membership_followed_same_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_membership_followed_membership", "membership_followed_regions", ["membership_id"]
    )

    # --------------------------------------------------- auth_sessions
    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("active_tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("ctx_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("user_auth_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("idx_sessions_expiry", "auth_sessions", ["expires_at"])

    # --------------------------------------------------- mcp_identity_bindings
    op.create_table(
        "mcp_identity_bindings",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("membership_id", UUID),
        sa.Column("server_key", sa.Text(), nullable=False),
        sa.Column("external_subject", sa.Text(), nullable=False),
        sa.Column("credential_ref", sa.Text(), nullable=False),
        sa.Column(
            "protocol_version", sa.Text(), nullable=False, server_default=sa.text("'unverified'")
        ),
        sa.Column("auth_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            name="fk_bindings_membership_same_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="bindings_status_valid"),
    )
    op.create_index("idx_bindings_tenant", "mcp_identity_bindings", ["tenant_id", "server_key"])
    # 企业默认绑定与成员覆盖绑定的唯一性（部分唯一索引，NULL 语义由谓词区分）
    op.execute(
        "CREATE UNIQUE INDEX ux_bindings_tenant_server_default"
        " ON mcp_identity_bindings (tenant_id, server_key)"
        " WHERE membership_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_bindings_tenant_server_member"
        " ON mcp_identity_bindings (tenant_id, server_key, membership_id)"
        " WHERE membership_id IS NOT NULL"
    )

    # --------------------------------------------------- thread_ownership
    op.create_table(
        "thread_ownership",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'native'")),
        sa.Column("migration_version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "source IN ('native', 'migrated')", name="thread_ownership_source_valid"
        ),
    )
    op.create_index("idx_thread_ownership_tenant", "thread_ownership", ["tenant_id", "user_id"])

    # --------------------------------------------------- migration_ledger
    op.create_table(
        "migration_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("manifest_fingerprint", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("conflict_code", sa.Text()),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actor", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "manifest_fingerprint", "entity_type", "source_key", name="uq_ledger_fingerprint_entity"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'skipped', 'conflict', 'quarantined')",
            name="ledger_status_valid",
        ),
    )
    op.create_index("idx_ledger_status", "migration_ledger", ["status"])

    # ------------------------------------------------------------- audit 企业维度
    op.add_column(
        "audit_logs", sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="SET NULL"))
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "actor_membership_id",
            UUID,
            sa.ForeignKey("tenant_memberships.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column("platform_scope", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("audit_logs", sa.Column("request_id", sa.Text()))
    op.create_index("idx_audit_tenant", "audit_logs", ["tenant_id", "created_at"])

    # ------------------------------------------------------------- permissions 可授予范围
    op.add_column(
        "permissions",
        sa.Column(
            "grantable_to_tenants", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )

    _apply_rls_and_grants()


def _apply_rls_and_grants() -> None:
    """RLS 策略与运行角色授权。

    只对 air_agent_runtime 生效；owner（迁移角色）不受影响。策略引用事务本地
    GUC：缺失时 `current_setting(..., true)` 返回 NULL，与 uuid 比较结果为
    NULL → 策略不通过（RLS 把 NULL 视为 false），实现失败关闭。
    """
    bind = op.get_bind()
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": RUNTIME_ROLE}
    ).scalar()
    if not role_exists:
        raise RuntimeError(
            f"运行角色 {RUNTIME_ROLE} 不存在。请先执行 scripts/bootstrap_tenant_db.py "
            "（创建角色并设置密码），再运行 alembic upgrade。"
        )

    tenant_scoped = [
        "departments",
        "tenant_memberships",
        "membership_roles",
        "membership_followed_regions",
        "mcp_identity_bindings",
        "thread_ownership",
    ]
    g = _guc_tenant()

    op.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")
    for table in [
        "tenants",
        "regions",
        "permissions",
        "roles",
        "role_permissions",
        "user_roles",
        "users",
        "user_followed_regions",
        "audit_logs",
        "auth_sessions",
        *tenant_scoped,
    ]:
        op.execute(f"GRANT SELECT ON {table} TO {RUNTIME_ROLE}")
    # migration_ledger 只归迁移/预检工具（owner）使用，不授权运行角色
    op.execute(
        f"GRANT INSERT, UPDATE ON tenants, departments, tenant_memberships, membership_roles,"
        f" membership_followed_regions, mcp_identity_bindings, thread_ownership, auth_sessions,"
        f" roles, role_permissions, user_roles, audit_logs TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT DELETE ON departments, tenant_memberships, membership_roles,"
        f" membership_followed_regions, mcp_identity_bindings, thread_ownership, auth_sessions,"
        f" roles, role_permissions, user_roles TO {RUNTIME_ROLE}"
    )
    # 账号创建/删除是平台动作：授权给运行角色，由 RLS user_insert/user_delete
    # （仅平台上下文）兜底。
    op.execute(f"GRANT INSERT, DELETE ON users TO {RUNTIME_ROLE}")
    # 列级授权：运行连接只能改自己的敏感列（自我改密走本人上下文）
    op.execute(
        # updated_at 由 ORM onupdate 自动维护，必须在列权限内
        f"GRANT UPDATE (display_name, last_login_at, password_hash, auth_revision, updated_at)"
        f" ON users TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT INSERT ON user_followed_regions, membership_followed_regions, audit_logs"
        f" TO {RUNTIME_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}")

    # ---------------------------------------------------------------- RLS
    for table in tenant_scoped:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING (tenant_id = {g} OR current_setting('air_agent.platform', true) = 'on')"
            f" WITH CHECK (tenant_id = {g} OR current_setting('air_agent.platform', true) = 'on')"
        )

    # 本人的成员行必须可读（登录/企业列表在“未选择企业”时还没有 tenant GUC）；
    # 写入仍只允许当前企业或平台上下文。
    op.execute("DROP POLICY tenant_isolation ON tenant_memberships")
    op.execute(
        f"CREATE POLICY tenant_memberships_read ON tenant_memberships FOR SELECT TO {RUNTIME_ROLE}"
        f" USING (tenant_id = {g}"
        " OR current_setting('air_agent.platform', true) = 'on'"
        " OR user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid)"
    )
    # 成员角色关联：本人成员行的授权（角色码）在"未选择企业"路径（登录/me）可读；
    # 其余仍按企业作用域。
    op.execute("ALTER TABLE membership_roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY membership_roles_read_self ON membership_roles"
        f" FOR SELECT TO {RUNTIME_ROLE}"
        " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on'"
        " OR EXISTS (SELECT 1 FROM tenant_memberships m"
        " WHERE m.id = membership_roles.membership_id"
        " AND m.user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid))"
    )
    op.execute(
        f"CREATE POLICY membership_roles_write ON membership_roles FOR INSERT TO {RUNTIME_ROLE}"
        f" WITH CHECK (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
    )
    op.execute(
        f"CREATE POLICY membership_roles_write_all ON membership_roles FOR ALL TO {RUNTIME_ROLE}"
        f" USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
    )
    op.execute(
        f"CREATE POLICY tenant_memberships_write ON tenant_memberships FOR ALL TO {RUNTIME_ROLE}"
        f" USING (tenant_id = {g} OR current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (tenant_id = {g} OR current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    # 成员能看到自己所属企业（用于 /auth/tenants 列表与 me 显示）；写只允许平台上下文
    op.execute(
        f"CREATE POLICY tenant_read_self ON tenants FOR SELECT TO {RUNTIME_ROLE}"
        f" USING (id = {_guc_tenant()}"
        f" OR current_setting('air_agent.platform', true) = 'on'"
        f" OR EXISTS (SELECT 1 FROM tenant_memberships m"
        f" WHERE m.tenant_id = tenants.id"
        f" AND m.user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        f" AND m.status = 'active'))"
    )
    op.execute(
        f"CREATE POLICY tenant_write_platform ON tenants FOR ALL TO {RUNTIME_ROLE}"
        f" USING (current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    # SELECT：本人 / 平台 / 登录流程（login GUC 仅在登录事务内） / 本企业成员
    op.execute(
        f"CREATE POLICY user_read ON users FOR SELECT TO {RUNTIME_ROLE}"
        f" USING (id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        f" OR current_setting('air_agent.platform', true) = 'on'"
        f" OR current_setting('air_agent.login', true) = 'on'"
        f" OR EXISTS (SELECT 1 FROM tenant_memberships m"
        f" WHERE m.user_id = users.id AND m.tenant_id = {_guc_tenant()}))"
    )
    # INSERT：仅平台上下文（运行角色不允许自助注册）
    op.execute(
        f"CREATE POLICY user_insert ON users FOR INSERT TO {RUNTIME_ROLE}"
        f" WITH CHECK (current_setting('air_agent.platform', true) = 'on')"
    )
    # UPDATE：本人（受限列权限兜底）或平台
    op.execute(
        f"CREATE POLICY user_update ON users FOR UPDATE TO {RUNTIME_ROLE}"
        f" USING (id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        f" OR current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        f" OR current_setting('air_agent.platform', true) = 'on')"
    )
    # DELETE：仅平台
    op.execute(
        f"CREATE POLICY user_delete ON users FOR DELETE TO {RUNTIME_ROLE}"
        f" USING (current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY session_scoped ON auth_sessions FOR ALL TO air_agent_runtime"
        " USING (id = NULLIF(current_setting('air_agent.session_id', true), '')::uuid"
        " OR user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
        " WITH CHECK (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY roles_scoped ON roles FOR ALL TO {RUNTIME_ROLE}"
        " USING (scope = 'platform' OR tenant_id = "
        + g
        + " OR current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (current_setting('air_agent.platform', true) = 'on'"
        f" OR (scope = 'tenant' AND tenant_id = {g}))"
    )

    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY role_permissions_scoped ON role_permissions FOR ALL TO air_agent_runtime"
        " USING (current_setting('air_agent.platform', true) = 'on'"
        " OR EXISTS (SELECT 1 FROM roles r WHERE r.id = role_permissions.role_id"
        " AND r.scope = 'tenant'"
        " AND r.tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid))"
        " WITH CHECK (current_setting('air_agent.platform', true) = 'on'"
        " OR EXISTS (SELECT 1 FROM roles r WHERE r.id = role_permissions.role_id"
        " AND r.scope = 'tenant'"
        " AND r.tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid))"
    )

    op.execute("ALTER TABLE user_roles ENABLE ROW LEVEL SECURITY")
    # 旧全局授权不再进入租户视野：只有平台上下文可读、可写
    op.execute(
        "CREATE POLICY user_roles_platform ON user_roles FOR ALL TO air_agent_runtime"
        " USING (current_setting('air_agent.platform', true) = 'on')"
        " WITH CHECK (current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE permissions ENABLE ROW LEVEL SECURITY")
    # 权限定义是全局目录数据（非租户数据）：所有已认证解析路径可读，
    # 修改仅平台/引导上下文。“租户只能授予 grantable 子集”由角色接口强制。
    op.execute(
        f"CREATE POLICY permissions_read ON permissions FOR SELECT TO {RUNTIME_ROLE} USING (true)"
    )
    op.execute(
        f"CREATE POLICY permissions_write_platform ON permissions FOR ALL TO {RUNTIME_ROLE}"
        " USING (current_setting('air_agent.platform', true) = 'on')"
        f" WITH CHECK (current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE user_followed_regions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY followed_user_scoped ON user_followed_regions FOR ALL TO air_agent_runtime"
        " USING (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
        " WITH CHECK (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
        " OR current_setting('air_agent.platform', true) = 'on')"
    )

    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    # 租户只见本企业审计；tenant_id IS NULL 的旧审计属于受限历史域（平台/操作人本人可见）
    op.execute(
        f"CREATE POLICY audit_read ON audit_logs FOR SELECT TO {RUNTIME_ROLE}"
        f" USING (tenant_id = {g}"
        " OR current_setting('air_agent.platform', true) = 'on'"
        " OR actor_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid)"
    )
    # 写入约束：企业审计必须落在当前企业上下文；会话类审计（登录/登出/切换）
    # 允许 tenant_id IS NULL 且 actor 为本人；平台操作走 platform 上下文。
    # 不允许“借本人可见性”向任意企业插入审计行。
    op.execute(
        f"CREATE POLICY audit_insert ON audit_logs FOR INSERT TO {RUNTIME_ROLE}"
        f" WITH CHECK (tenant_id = {g}"
        " OR current_setting('air_agent.platform', true) = 'on'"
        " OR (tenant_id IS NULL"
        " AND actor_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid))"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0003 不提供 downgrade：企业身份域回滚使用受保护备份+前向修复（见 change 迁移方案 §9）"
    )
