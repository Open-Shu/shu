"""Migration 009: SHU-761 tenant isolation infrastructure (all-at-once).

Combines what the spec calls Stages A-C of the rollout into one revision —
Shu is silo-only with one active production environment and no
observe-between-stages window to gain from a phased rollout. Stage E
(composite FKs) and Stage F (RLS enablement + billing_state restructure)
will be appended to this same file when those tasks land.

Sections, in apply order:

  A. ``tenants`` catalog table + one seed row matching the deployment mode.
  B. ``tenant_id`` column on every tenant-scoped table, indexed, with FK to
     ``tenants(id)`` validated NOT-VALID-then-VALIDATE for self-hosted /
     silo backfill safety. DEFAULT literal per deployment mode keeps the
     ALTER metadata-only on PG 11+.
  C. ``shu_admin`` (BYPASSRLS) and ``shu_app`` (no BYPASSRLS) roles, grants,
     unique constraints on the SD-function lookup columns, and the
     SECURITY DEFINER family of pre-auth tenant resolvers.

Revision: 009_tenant_isolation
Revises: 008
Create Date: 2026-05-15
"""

import os

import shu.auth.models  # noqa: F401 - register User on Base.metadata
import shu.models  # noqa: F401 - register every model on Base.metadata
import sqlalchemy as sa
from alembic import op
from shu.core.config import SELF_HOSTED_TENANT_UUID, DeploymentMode, get_settings_instance
from shu.core.database import Base

revision = "009_tenant_isolation"
down_revision = "008"
branch_labels = None
depends_on = None

# Tables that grow large in self-hosted installs and so warrant CONCURRENTLY
# index creation — listed explicitly so the set is reviewable rather than
# inferred from row counts at migration time.
_LARGE_TABLES = frozenset({"document_chunks"})


def _tenant_scoped_table_names() -> list[str]:
    # Walking metadata rather than maintaining a hand-curated list keeps the
    # migration in sync as new tenant-scoped models arrive.
    return sorted(name for name, table in Base.metadata.tables.items() if "tenant_id" in table.columns)


def _composite_fk_inventory() -> list[tuple[str, str, str, str]]:
    """Return (child_table, child_col, parent_table, parent_col) tuples.

    Only includes FKs where both ends are tenant-scoped; self-referential FKs
    and FKs to global tables (llm_providers, plugin_definitions, etc.) are
    excluded since composite tenant matching only makes sense between two
    tenant-stamped rows.
    """
    tenant_scoped = set(_tenant_scoped_table_names())
    result: list[tuple[str, str, str, str]] = []
    for child_name, child_table in Base.metadata.tables.items():
        if child_name not in tenant_scoped:
            continue
        for col in child_table.columns:
            for fk in col.foreign_keys:
                parent_name = fk.column.table.name
                if parent_name == child_name:
                    continue  # self-referential
                if parent_name not in tenant_scoped:
                    continue  # FK to global table
                result.append((child_name, col.name, parent_name, fk.column.name))
    return sorted(result)


def _resolve_default(mode: DeploymentMode, tenant_id: str | None) -> str | None:
    if mode is DeploymentMode.SELF_HOSTED:
        return SELF_HOSTED_TENANT_UUID
    if mode is DeploymentMode.SILO:
        return tenant_id
    if mode is DeploymentMode.MULTI_TENANT:
        return None
    raise RuntimeError(f"Unknown deployment mode: {mode!r}")


def _read_password(env_var: str, default: str) -> str:
    # CREATE ROLE PASSWORD doesn't accept bind parameters, so we interpolate.
    # Doubling single quotes is the standard Postgres string-literal escape.
    return os.environ.get(env_var, default).replace("'", "''")


# Section C SQL kept as a single string so the function bodies (which include
# $$ delimiters) read naturally alongside their grants.
_SD_FUNCTIONS_SQL = r"""
-- tenant_for_user_id: authenticated request, post-JWT-decode lookup.
CREATE OR REPLACE FUNCTION tenant_for_user_id(p_user_id text)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$ SELECT tenant_id FROM users WHERE id = p_user_id $$;
ALTER FUNCTION tenant_for_user_id(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_user_id(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_user_id(text) TO shu_app;

-- tenant_for_email: login / password-reset request / SSO callback.
CREATE OR REPLACE FUNCTION tenant_for_email(p_email text)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$ SELECT tenant_id FROM users WHERE email = p_email $$;
ALTER FUNCTION tenant_for_email(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_email(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_email(text) TO shu_app;

-- tenant_for_reset_token: argument is the sha256 hex digest, never the
-- plaintext. INTO STRICT raises NO_DATA_FOUND on miss and TOO_MANY_ROWS on
-- collision; the caller treats both as "no valid reset session".
CREATE OR REPLACE FUNCTION tenant_for_reset_token(p_token_hash text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$
DECLARE
    result text;
BEGIN
    SELECT tenant_id INTO STRICT result
    FROM password_reset_token
    WHERE token_hash = p_token_hash;
    RETURN result;
END;
$$;
ALTER FUNCTION tenant_for_reset_token(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_reset_token(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_reset_token(text) TO shu_app;

-- tenant_for_verification_token: same sha256-hash convention as reset_token.
CREATE OR REPLACE FUNCTION tenant_for_verification_token(p_hash text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$
DECLARE
    result text;
BEGIN
    SELECT tenant_id INTO STRICT result
    FROM users
    WHERE email_verification_token_hash = p_hash;
    RETURN result;
END;
$$;
ALTER FUNCTION tenant_for_verification_token(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_verification_token(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_verification_token(text) TO shu_app;

-- tenant_for_stripe_customer: webhook arrives with the customer id and no
-- Shu user context. Plain SQL is fine since billing_state has at most one
-- row per stripe_customer_id (UNIQUE partial index added below).
CREATE OR REPLACE FUNCTION tenant_for_stripe_customer(p_customer_id text)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$ SELECT tenant_id FROM billing_state WHERE stripe_customer_id = p_customer_id $$;
ALTER FUNCTION tenant_for_stripe_customer(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_stripe_customer(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_stripe_customer(text) TO shu_app;
"""


def upgrade() -> None:
    settings = get_settings_instance()
    tables = _tenant_scoped_table_names()

    # =========================================================================
    # A. tenants catalog table + seed row
    # =========================================================================
    op.execute(
        "CREATE TABLE tenants ("
        "id text PRIMARY KEY, "
        "created_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )

    seed_id = _resolve_default(settings.deployment_mode, settings.tenant_id)
    if seed_id is not None:
        # ON CONFLICT DO NOTHING keeps re-runs against partially-seeded DBs idempotent.
        op.execute(f"INSERT INTO tenants (id) VALUES ('{seed_id}') ON CONFLICT (id) DO NOTHING")

    # =========================================================================
    # B. tenant_id columns + indexes + FK constraints
    # =========================================================================
    column_default = _resolve_default(settings.deployment_mode, settings.tenant_id)

    for table_name in tables:
        if column_default is not None:
            # SELF_HOSTED_TENANT_UUID is hardcoded; SHU_TENANT_ID is UUID-validated
            # at Settings load — so neither can carry a quote or semicolon. DDL
            # DEFAULT clauses don't accept bind params anyway.
            op.execute(
                f"ALTER TABLE {table_name} ADD COLUMN tenant_id text NOT NULL DEFAULT '{column_default}'"
            )
        else:
            op.execute(f"ALTER TABLE {table_name} ADD COLUMN tenant_id text NOT NULL")

    for table_name in tables:
        if table_name not in _LARGE_TABLES:
            op.create_index(f"ix_{table_name}_tenant_id", table_name, ["tenant_id"])

    for table_name in tables:
        constraint_name = f"{table_name}_tenant_id_fk"
        op.execute(
            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
            f"FOREIGN KEY (tenant_id) REFERENCES tenants(id) NOT VALID"
        )
        op.execute(f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name}")

    # CONCURRENTLY needs autocommit — alembic's autocommit_block() ends the
    # current transaction, runs the body in autocommit, opens a fresh tx after.
    with op.get_context().autocommit_block():
        for table_name in sorted(_LARGE_TABLES & set(tables)):
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table_name}_tenant_id ON {table_name} (tenant_id)"
            )

    # =========================================================================
    # C. Roles + grants + unique lookup constraints + SECURITY DEFINER functions
    # =========================================================================
    admin_pw = _read_password("SHU_ADMIN_DB_PASSWORD", "shu_admin_dev")
    app_pw = _read_password("SHU_APP_DB_PASSWORD", "shu_app_dev")

    # CREATE ROLE has no IF NOT EXISTS. Roles are cluster-wide and persist
    # past DB drops, so DO blocks keep re-runs idempotent.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shu_admin') THEN
                CREATE ROLE shu_admin WITH LOGIN BYPASSRLS PASSWORD '{admin_pw}';
            END IF;
        END $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shu_app') THEN
                CREATE ROLE shu_app WITH LOGIN PASSWORD '{app_pw}';
            END IF;
        END $$;
        """
    )

    db_name = op.get_bind().execute(sa.text("SELECT current_database()")).scalar()

    op.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO shu_app')
    op.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO shu_admin')
    op.execute("GRANT USAGE ON SCHEMA public TO shu_app")
    op.execute("GRANT USAGE ON SCHEMA public TO shu_admin")

    for table_name in tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_name} TO shu_app")
    op.execute("GRANT SELECT ON tenants TO shu_app")

    # Sequences cover Identity columns and legacy autoincrement PKs.
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO shu_app")

    # Future objects created by shu_admin in later migrations auto-inherit
    # the right grants — no need to chase them table-by-table.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE shu_admin IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shu_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE shu_admin IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO shu_app"
    )

    # BYPASSRLS gives shu_admin a read path past RLS but not table privileges.
    # The SD functions below read these tables under shu_admin ownership.
    op.execute("GRANT SELECT ON users TO shu_admin")
    op.execute("GRANT SELECT ON password_reset_token TO shu_admin")
    op.execute("GRANT SELECT ON billing_state TO shu_admin")

    # Unique constraints on SD-function lookup columns — the functions return
    # a single row via WHERE col = $1, so the schema must enforce that.
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")
    op.execute("CREATE UNIQUE INDEX ix_password_reset_token_token_hash ON password_reset_token (token_hash)")

    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_verification_token_hash "
        "ON users (email_verification_token_hash) "
        "WHERE email_verification_token_hash IS NOT NULL"
    )

    op.execute(
        "CREATE UNIQUE INDEX ix_billing_state_stripe_customer_id "
        "ON billing_state (stripe_customer_id) "
        "WHERE stripe_customer_id IS NOT NULL"
    )

    op.execute(_SD_FUNCTIONS_SQL)

    # =========================================================================
    # D. Composite (tenant_id, parent_id) FKs — tasks 13.2 + 13.3
    #
    # Adds UNIQUE(tenant_id, id) on every parent referenced by a tenant-scoped
    # child FK, then a composite child FK (tenant_id, child_col) pointing at
    # parent (tenant_id, id). This makes it structurally impossible for a child
    # row to reference a parent in a different tenant — Postgres refuses the
    # insert. The existing single-column FK stays in place alongside.
    # =========================================================================
    inventory = _composite_fk_inventory()

    parent_uniques = sorted({(parent, parent_col) for _, _, parent, parent_col in inventory})
    for parent, parent_col in parent_uniques:
        op.execute(
            f"ALTER TABLE {parent} ADD CONSTRAINT {parent}_tenant_id_{parent_col}_unique "
            f"UNIQUE (tenant_id, {parent_col})"
        )

    for child, child_col, parent, parent_col in inventory:
        constraint_name = f"{child}_{child_col}_tenant_fk"
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {constraint_name} "
            f"FOREIGN KEY (tenant_id, {child_col}) REFERENCES {parent}(tenant_id, {parent_col}) NOT VALID"
        )
        op.execute(f"ALTER TABLE {child} VALIDATE CONSTRAINT {constraint_name}")

    # =========================================================================
    # E. billing_state restructure (task 4.3) + RLS enablement (task 14.1)
    #
    # The billing restructure lifts the singleton pin so we can hold one row
    # per tenant: drop the id=1 CHECK, switch id to Identity (START WITH 2 so
    # the existing singleton row at id=1 doesn't collide with the sequence's
    # first emission), and add UNIQUE(tenant_id) to encode the per-tenant
    # invariant. Then RLS turns on for every tenant-scoped table and the
    # tenant_isolation policy starts filtering.
    # =========================================================================
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT billing_state_singleton")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id DROP DEFAULT")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (START WITH 2)")
    op.execute("ALTER TABLE billing_state ADD CONSTRAINT billing_state_one_per_tenant UNIQUE (tenant_id)")

    for table_name in tables:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        # FORCE so even the table owner can't bypass the policy — only roles
        # with BYPASSRLS (shu_admin) get through.
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table_name} AS PERMISSIVE FOR ALL TO shu_app "
            f"USING (tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    tables = _tenant_scoped_table_names()
    inventory = _composite_fk_inventory()

    # Reverse section E (RLS + billing restructure)
    for table_name in tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_one_per_tenant")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id DROP IDENTITY IF EXISTS")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id SET DEFAULT 1")
    # Drop-then-add keeps the downgrade idempotent — the upgrade might have
    # failed before Section E ran, in which case the singleton CHECK is still
    # there from migration 008 and a bare ADD CONSTRAINT would conflict.
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_singleton")
    op.execute("ALTER TABLE billing_state ADD CONSTRAINT billing_state_singleton CHECK (id = 1)")

    # Reverse section D (composite FKs first, then parent uniques)
    for child, child_col, _parent, _parent_col in inventory:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {child}_{child_col}_tenant_fk")

    parent_uniques = sorted({(parent, parent_col) for _, _, parent, parent_col in inventory})
    for parent, parent_col in parent_uniques:
        op.execute(f"ALTER TABLE {parent} DROP CONSTRAINT IF EXISTS {parent}_tenant_id_{parent_col}_unique")

    # Reverse section C
    op.execute("DROP FUNCTION IF EXISTS tenant_for_stripe_customer(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_verification_token(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_reset_token(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_email(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_user_id(text)")

    op.execute("DROP INDEX IF EXISTS ix_billing_state_stripe_customer_id")
    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")
    op.execute(
        "CREATE INDEX ix_users_email_verification_token_hash "
        "ON users (email_verification_token_hash) "
        "WHERE email_verification_token_hash IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")
    op.execute("CREATE INDEX ix_password_reset_token_token_hash ON password_reset_token (token_hash)")

    op.execute("REVOKE SELECT ON billing_state FROM shu_admin")
    op.execute("REVOKE SELECT ON password_reset_token FROM shu_admin")
    op.execute("REVOKE SELECT ON users FROM shu_admin")

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE shu_admin IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM shu_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE shu_admin IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM shu_app"
    )
    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM shu_app")
    op.execute("REVOKE SELECT ON tenants FROM shu_app")
    for table_name in tables:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table_name} FROM shu_app")

    db_name = op.get_bind().execute(sa.text("SELECT current_database()")).scalar()
    op.execute("REVOKE USAGE ON SCHEMA public FROM shu_admin")
    op.execute("REVOKE USAGE ON SCHEMA public FROM shu_app")
    op.execute(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM shu_admin')
    op.execute(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM shu_app')

    op.execute("DROP OWNED BY shu_app")
    op.execute("DROP ROLE IF EXISTS shu_app")
    op.execute("DROP OWNED BY shu_admin")
    op.execute("DROP ROLE IF EXISTS shu_admin")

    # Reverse section B
    for table_name in tables:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {table_name}_tenant_id_fk")
    for table_name in tables:
        op.execute(f"DROP INDEX IF EXISTS ix_{table_name}_tenant_id")
    for table_name in tables:
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS tenant_id")

    # Reverse section A
    op.execute("DROP TABLE tenants")
