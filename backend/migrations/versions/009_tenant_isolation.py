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

Revision: 009
Revises: 008
Create Date: 2026-05-15
"""

import os

import sqlalchemy as sa
from alembic import op

from shu.core.config import SELF_HOSTED_TENANT_UUID, DeploymentMode, get_settings_instance

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

# Frozen tenant-scoped table inventory for this migration.
#
# DELIBERATELY NOT computed from Base.metadata at apply time: a future model
# addition would either (a) crash this migration on a fresh install because
# the table being ALTERed doesn't exist yet at this revision, or (b) silently
# leave the new table unprotected on already-migrated DBs that ran 009 before
# the model was added. Either way, migration behavior would depend on when it
# was applied relative to model edits.
#
# The companion test in tests/unit/migrations/test_stage_a_table_inventory.py
# diffs this list against the live Base.metadata and fails CI on drift — that's
# the forcing function that makes "adding a tenant-scoped model without a
# matching follow-on migration" impossible to merge.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "access_policies",
    "access_policy_bindings",
    "access_policy_statements",
    "agent_memory",
    "attachments",
    "billing_state",
    "billing_state_audit",
    "conversations",
    "document_chunks",
    "document_participants",
    "document_projects",
    "document_queries",
    "documents",
    "email_send_log",
    "experience_runs",
    "experience_steps",
    "experiences",
    "knowledge_bases",
    "llm_usage",
    "mcp_server_connections",
    "message_attachments",
    "messages",
    "model_configuration_kb_prompts",
    "model_configuration_knowledge_bases",
    "model_configurations",
    "password_reset_token",
    "plugin_executions",
    "plugin_feeds",
    "plugin_storage",
    "plugin_subscriptions",
    "prompt_assignments",
    "prompts",
    "provider_credentials",
    "provider_identities",
    "system_settings",
    "user_group_memberships",
    "user_groups",
    "user_preferences",
    "users",
)

# Tables that grow large in self-hosted installs and so warrant CONCURRENTLY
# index creation — listed explicitly so the set is reviewable rather than
# inferred from row counts at migration time.
_LARGE_TABLES = frozenset({"document_chunks"})

# Frozen composite-FK inventory for this migration. Same rationale as
# _TENANT_SCOPED_TABLES above: a live walk of Base.metadata at apply time
# would produce different DDL on a fresh install vs. an already-migrated DB
# whenever the model graph picks up a new tenant-scoped FK.
#
# Each tuple is (child_table, child_column, parent_table, parent_column).
# The companion test diffs against the live `compute_composite_fk_inventory()`
# and against `composite_fk_inventory.json`, failing CI on drift.
_COMPOSITE_FKS: tuple[tuple[str, str, str, str], ...] = (
    ("access_policies", "created_by", "users", "id"),
    ("access_policy_bindings", "policy_id", "access_policies", "id"),
    ("access_policy_statements", "policy_id", "access_policies", "id"),
    ("agent_memory", "user_id", "users", "id"),
    ("attachments", "conversation_id", "conversations", "id"),
    ("attachments", "user_id", "users", "id"),
    ("conversations", "model_configuration_id", "model_configurations", "id"),
    ("document_chunks", "document_id", "documents", "id"),
    ("document_chunks", "knowledge_base_id", "knowledge_bases", "id"),
    ("document_participants", "document_id", "documents", "id"),
    ("document_participants", "knowledge_base_id", "knowledge_bases", "id"),
    ("document_projects", "document_id", "documents", "id"),
    ("document_projects", "knowledge_base_id", "knowledge_bases", "id"),
    ("document_queries", "document_id", "documents", "id"),
    ("document_queries", "knowledge_base_id", "knowledge_bases", "id"),
    ("document_queries", "source_chunk_id", "document_chunks", "id"),
    ("documents", "knowledge_base_id", "knowledge_bases", "id"),
    ("experience_runs", "experience_id", "experiences", "id"),
    ("experience_runs", "user_id", "users", "id"),
    ("experience_steps", "experience_id", "experiences", "id"),
    ("experience_steps", "knowledge_base_id", "knowledge_bases", "id"),
    ("experiences", "created_by", "users", "id"),
    ("experiences", "model_configuration_id", "model_configurations", "id"),
    ("experiences", "prompt_id", "prompts", "id"),
    ("knowledge_bases", "owner_id", "users", "id"),
    ("message_attachments", "attachment_id", "attachments", "id"),
    ("message_attachments", "message_id", "messages", "id"),
    ("messages", "conversation_id", "conversations", "id"),
    ("model_configuration_kb_prompts", "knowledge_base_id", "knowledge_bases", "id"),
    ("model_configuration_kb_prompts", "model_configuration_id", "model_configurations", "id"),
    ("model_configuration_kb_prompts", "prompt_id", "prompts", "id"),
    ("model_configuration_knowledge_bases", "knowledge_base_id", "knowledge_bases", "id"),
    ("model_configuration_knowledge_bases", "model_configuration_id", "model_configurations", "id"),
    ("model_configurations", "prompt_id", "prompts", "id"),
    ("password_reset_token", "user_id", "users", "id"),
    ("plugin_executions", "schedule_id", "plugin_feeds", "id"),
    ("plugin_storage", "user_id", "users", "id"),
    ("plugin_subscriptions", "user_id", "users", "id"),
    ("prompt_assignments", "prompt_id", "prompts", "id"),
    ("provider_credentials", "user_id", "users", "id"),
    ("provider_identities", "user_id", "users", "id"),
    ("user_group_memberships", "granted_by", "users", "id"),
    ("user_group_memberships", "group_id", "user_groups", "id"),
    ("user_group_memberships", "user_id", "users", "id"),
    ("user_groups", "created_by", "users", "id"),
    ("user_preferences", "user_id", "users", "id"),
)


def _resolve_default(mode: DeploymentMode, tenant_id: str | None) -> str | None:
    if mode is DeploymentMode.SELF_HOSTED:
        return SELF_HOSTED_TENANT_UUID
    if mode is DeploymentMode.SILO:
        return tenant_id
    if mode is DeploymentMode.MULTI_TENANT:
        return None
    raise RuntimeError(f"Unknown deployment mode: {mode!r}")


def _read_password(env_var: str) -> str:
    # CREATE ROLE PASSWORD doesn't accept bind parameters, so we interpolate.
    # Doubling single quotes is the standard Postgres string-literal escape.
    #
    # Required, not optional: a missing env var would create a LOGIN-capable role
    # with whatever hardcoded fallback we shipped — i.e. a well-known credential
    # for anyone able to reach the DB port. Local dev injects these via
    # `make migrate-local-lab`; production via the deploy pipeline. Either way,
    # absence is a misconfiguration we want to fail on, not paper over.
    value = os.environ.get(env_var)
    if not value:
        raise RuntimeError(
            f"{env_var} is required to create the shu_admin / shu_app roles. "
            "Set it in the deploy environment (or the `make migrate-local-lab` target for dev) "
            "before running this migration."
        )
    return value.replace("'", "''")


# ----------------------------------------------------------------------------
# Idempotency helpers
#
# This migration uses an autocommit_block() partway through (for CONCURRENTLY
# indexes), which commits whatever ran before it. If anything fails AFTER that
# commit, alembic_version stays at the previous revision but the partially-
# committed DDL is on disk. A naive re-run would then trip on "table already
# exists" / "column already exists" / "constraint already exists" / etc.
#
# Every helper below is a no-op if the target object is already in the desired
# state. That makes the migration safe to re-run after any partial failure.
# ----------------------------------------------------------------------------


def _add_constraint_if_missing(table: str, constraint: str, definition: str) -> None:
    """ALTER TABLE ... ADD CONSTRAINT, no-op if a constraint with this name already exists."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{constraint}'
                  AND conrelid = '{table}'::regclass
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {constraint} {definition};
            END IF;
        END $$;
        """
    )


def _replace_policy(table: str, policy: str, body: str) -> None:
    """Drop-and-create the policy so re-runs land on the desired definition.

    ``CREATE POLICY`` fails if the policy exists; ``CREATE OR REPLACE POLICY``
    doesn't exist. Dropping first is the standard idempotent shape.
    """
    op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(f"CREATE POLICY {policy} ON {table} {body}")


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
-- plaintext. Returns NULL on miss so all five tenant_for_* lookups behave
-- the same — an INTO STRICT raise-on-miss here previously bit the route
-- layer because callers couldn't tell "no row" from a real DB outage.
-- Plain SQL returns NULL uniformly; the Python wrapper keeps DBAPIError
-- translation as defense-in-depth if anyone reverts to PL/pgSQL.
CREATE OR REPLACE FUNCTION tenant_for_reset_token(p_token_hash text)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$ SELECT tenant_id FROM password_reset_token WHERE token_hash = p_token_hash $$;
ALTER FUNCTION tenant_for_reset_token(text) OWNER TO shu_admin;
REVOKE ALL ON FUNCTION tenant_for_reset_token(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_for_reset_token(text) TO shu_app;

-- tenant_for_verification_token: same sha256-hash convention as reset_token.
-- Plain SQL so all five tenant_for_* lookups return NULL on miss uniformly.
CREATE OR REPLACE FUNCTION tenant_for_verification_token(p_hash text)
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
STABLE
AS $$ SELECT tenant_id FROM users WHERE email_verification_token_hash = p_hash $$;
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
    tables = list(_TENANT_SCOPED_TABLES)

    # =========================================================================
    # A. tenants catalog table + seed row
    # =========================================================================
    op.execute(
        "CREATE TABLE IF NOT EXISTS tenants ("
        "id text PRIMARY KEY, "
        "created_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )

    seed_id = _resolve_default(settings.deployment_mode, settings.tenant_id)
    if seed_id is not None:
        # ON CONFLICT DO NOTHING keeps re-runs against partially-seeded DBs idempotent.
        op.execute(
            sa.text("INSERT INTO tenants (id) VALUES (:tid) ON CONFLICT (id) DO NOTHING").bindparams(tid=seed_id)
        )

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
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT '{column_default}'"
            )
        else:
            op.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL")

    # system_settings: switch the PK from `(key)` to `(tenant_id, key)` so
    # the same key (e.g. "side_call_model_config_id") can exist independently
    # per tenant. Has to happen AFTER ADD COLUMN tenant_id (above — the new
    # column has to exist before the PK can reference it) and BEFORE the
    # downgrade-symmetric DROP COLUMN runs at downgrade time.
    #
    # Idempotent shape: swap when the existing PK is still single-column
    # (the pre-009 shape) OR when there's no PK at all (a previous run
    # crashed between DROP and ADD). Re-runs after a successful swap see a
    # 2-column PK and skip. Without the ``IS NULL`` branch the recovery
    # path silently leaves the table without a PK — belt-and-suspenders
    # against any future refactor that moves this swap past an autocommit
    # boundary where partial-failure recovery becomes possible.
    op.execute(
        """
        DO $$
        DECLARE pk_col_count int;
        BEGIN
            SELECT array_length(conkey, 1) INTO pk_col_count
            FROM pg_constraint
            WHERE conname = 'system_settings_pkey'
              AND conrelid = 'system_settings'::regclass;
            IF pk_col_count IS NULL OR pk_col_count = 1 THEN
                ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_pkey;
                ALTER TABLE system_settings ADD CONSTRAINT system_settings_pkey
                    PRIMARY KEY (tenant_id, key);
            END IF;
        END $$;
        """
    )

    # Drop column-level UNIQUE constraints that were globally-unique pre-009
    # and replace with composite (tenant_id, col) UNIQUEs. Values like
    # ``mcp_server_connections.name`` and ``user_groups.name`` are
    # per-tenant identifiers — tenant A picking "Engineering" must not
    # block tenant B from using the same. Old constraint names follow the
    # Postgres default for column-unique (``<table>_<column>_key``);
    # ``IF EXISTS`` covers re-runs after the swap landed once.
    _tenant_scoped_unique_swaps = (
        # (table, column, old_constraint, new_constraint)
        ("mcp_server_connections", "name", "mcp_server_connections_name_key", "uq_mcp_server_connections_tenant_name"),
        ("access_policies", "name", "access_policies_name_key", "uq_access_policies_tenant_name"),
        ("knowledge_bases", "slug", "knowledge_bases_slug_key", "uq_knowledge_bases_tenant_slug"),
        ("experiences", "slug", "experiences_slug_key", "uq_experiences_tenant_slug"),
        ("user_groups", "name", "user_groups_name_key", "uq_user_groups_tenant_name"),
    )
    for table_name, col, old_constraint, new_constraint in _tenant_scoped_unique_swaps:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {old_constraint}")
        _add_constraint_if_missing(
            table_name, new_constraint, f"UNIQUE (tenant_id, {col})"
        )

    # plugin_storage uniqueness pre-009 was (scope, user_id, plugin_name,
    # namespace, key) — globally unique. With the table tenant-scoped, two
    # tenants writing the same (plugin_name, namespace, key) at scope='system'
    # would collide on INSERT even though RLS hides the other row from reads.
    # Add tenant_id to both the named UNIQUE and the partial system-scope
    # index, dropping the legacy shapes if present.
    op.execute("ALTER TABLE plugin_storage DROP CONSTRAINT IF EXISTS uq_plugin_storage_scope_key")
    op.execute("DROP INDEX IF EXISTS ix_plugin_storage_lookup")
    op.execute("DROP INDEX IF EXISTS uq_plugin_storage_system_scope_key")
    _add_constraint_if_missing(
        "plugin_storage",
        "uq_plugin_storage_tenant_scope_key",
        "UNIQUE (tenant_id, scope, user_id, plugin_name, namespace, key)",
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_plugin_storage_lookup "
        "ON plugin_storage (tenant_id, scope, user_id, plugin_name, namespace, key)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_plugin_storage_system_scope_key "
        "ON plugin_storage (tenant_id, plugin_name, namespace, key) "
        "WHERE scope = 'system'"
    )

    for table_name in tables:
        if table_name not in _LARGE_TABLES:
            # IF NOT EXISTS so a partial-failure re-run doesn't fail on the
            # subset of indexes that were committed last time.
            op.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_tenant_id ON {table_name} (tenant_id)"
            )

    for table_name in tables:
        constraint_name = f"{table_name}_tenant_id_fk"
        # ON DELETE RESTRICT matches the ``ondelete="RESTRICT"`` declared on
        # TenantScopedMixin (models/base.py). Without it, Postgres defaults
        # to NO ACTION, which is similar but not identical (NO ACTION is
        # deferrable). Keep model + DB constraint in lockstep so a future
        # reader inspecting either sees the same behavior.
        _add_constraint_if_missing(
            table_name,
            constraint_name,
            "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT NOT VALID",
        )
        # VALIDATE CONSTRAINT on an already-validated constraint is a no-op,
        # so this is safe to re-run unconditionally.
        op.execute(f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name}")

    # CONCURRENTLY needs autocommit — alembic's autocommit_block() ends the
    # current transaction, runs the body in autocommit, opens a fresh tx after.
    #
    # If a previous run was interrupted (process kill, OOM, network blip)
    # during a CREATE INDEX CONCURRENTLY, Postgres leaves the index in the
    # catalog with ``pg_index.indisvalid = false``. ``IF NOT EXISTS`` would
    # then silently skip the rebuild on re-run — the migration completes
    # "successfully" with a non-functional index. Drop any invalid sibling
    # first so the recreate has a clean slate.
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for table_name in sorted(_LARGE_TABLES & set(tables)):
            idx_name = f"ix_{table_name}_tenant_id"
            invalid = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :name AND NOT i.indisvalid"
                ),
                {"name": idx_name},
            ).first()
            if invalid is not None:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name} ON {table_name} (tenant_id)"
            )

    # =========================================================================
    # C. Roles + grants + unique lookup constraints + SECURITY DEFINER functions
    # =========================================================================
    admin_pw = _read_password("SHU_ADMIN_DB_PASSWORD")
    app_pw = _read_password("SHU_APP_DB_PASSWORD")

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

    # Global tables — RLS-exempt but still touched by request-path code, so
    # shu_app needs the right grants or routine requests will 'permission
    # denied' after cutover. Verbs are the minimum each table actually needs:
    #   llm_providers, llm_models — admin UI provider/model CRUD (DML)
    #   plugin_definitions        — plugin registry sync on startup writes rows (DML)
    #   llm_provider_type_definitions — read-only at runtime; seeded by migrations (SELECT)
    # If a future global table is added, its grants belong alongside its
    # CREATE TABLE migration, not here. (``system_settings`` is tenant-scoped
    # — its grants come from the per-table loop above.)
    for table_name in ("llm_providers", "llm_models", "plugin_definitions"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table_name} TO shu_app")
    op.execute("GRANT SELECT ON llm_provider_type_definitions TO shu_app")

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

    # Catalog / tenant-provisioning writes — shu_admin owns operator-only
    # operations that need to write across tenants: seeding the ``tenants``
    # catalog, creating the first admin user for a new tenant, stubbing the
    # per-tenant ``billing_state`` row. These are CLI / control-plane paths
    # (e.g. ``backend/scripts/seed_tenant.py``), not request-path code.
    #
    # SELECT is included alongside the write verbs because ``INSERT ... ON
    # CONFLICT DO NOTHING`` (the idempotent shape these scripts use) needs
    # SELECT on the conflict target to check existence — INSERT alone errors
    # with "permission denied for table".
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO shu_admin")
    op.execute("GRANT INSERT, UPDATE ON users TO shu_admin")
    op.execute("GRANT INSERT, UPDATE ON billing_state TO shu_admin")

    # shu_app needs to read ``alembic_version`` because the API's startup
    # schema-baseline check (``verify_schema_version`` in main.py) queries
    # it before serving traffic. Alembic creates the table itself the first
    # time a migration runs, so no GRANT happens automatically — we add it
    # here, gated on the table existing (skipped if alembic was never used).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_class WHERE relname = 'alembic_version') THEN
                GRANT SELECT ON alembic_version TO shu_app;
                GRANT SELECT ON alembic_version TO shu_admin;
            END IF;
        END $$;
        """
    )

    # Unique constraints on SD-function lookup columns — the functions return
    # a single row via WHERE col = $1, so the schema must enforce that.
    #
    # DROP IF EXISTS + CREATE UNIQUE INDEX IF NOT EXISTS: the DROP handles the
    # pre-009 non-unique index of the same name; the IF NOT EXISTS on the
    # CREATE handles partial-failure re-runs where the unique index already
    # landed last time.
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_password_reset_token_token_hash "
        "ON password_reset_token (token_hash)"
    )

    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_verification_token_hash "
        "ON users (email_verification_token_hash) "
        "WHERE email_verification_token_hash IS NOT NULL"
    )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_state_stripe_customer_id "
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
    inventory = list(_COMPOSITE_FKS)

    parent_uniques = sorted({(parent, parent_col) for _, _, parent, parent_col in inventory})
    for parent, parent_col in parent_uniques:
        _add_constraint_if_missing(
            parent,
            f"{parent}_tenant_id_{parent_col}_unique",
            f"UNIQUE (tenant_id, {parent_col})",
        )

    for child, child_col, parent, parent_col in inventory:
        constraint_name = f"{child}_{child_col}_tfk"
        _add_constraint_if_missing(
            child,
            constraint_name,
            f"FOREIGN KEY (tenant_id, {child_col}) REFERENCES {parent}(tenant_id, {parent_col}) NOT VALID",
        )
        # VALIDATE CONSTRAINT on already-valid is a no-op; safe to re-run.
        op.execute(f"ALTER TABLE {child} VALIDATE CONSTRAINT {constraint_name}")

    # =========================================================================
    # E. billing_state restructure (task 4.3) + RLS enablement (task 14.1)
    #
    # ``tenant_id`` IS the primary key — one row per tenant by definition; no
    # separate surrogate ``id`` needed. The pre-009 shape pinned ``id = 1``
    # via a CHECK constraint and a separate UNIQUE(tenant_id); we drop the
    # CHECK, drop the legacy ``id`` column, drop the now-redundant UNIQUE,
    # and promote tenant_id to PK. Then RLS turns on for every tenant-scoped
    # table and the tenant_isolation policy starts filtering.
    # =========================================================================
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_singleton")
    # Drop any UNIQUE(tenant_id) constraint left over from an earlier version
    # of this migration. Once tenant_id is PK, the PK enforces uniqueness on
    # its own and a separate UNIQUE is redundant.
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_one_per_tenant")
    # Swap the PK from `id` to `tenant_id`. Guard so a re-run after the swap
    # is a no-op.
    op.execute(
        """
        DO $$
        DECLARE pk_def text;
        BEGIN
            SELECT pg_get_constraintdef(oid) INTO pk_def
            FROM pg_constraint
            WHERE conrelid = 'billing_state'::regclass AND contype = 'p';
            IF pk_def IS NULL OR pk_def NOT LIKE '%(tenant_id)%' THEN
                ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_pkey;
                ALTER TABLE billing_state ADD CONSTRAINT billing_state_pkey
                    PRIMARY KEY (tenant_id);
            END IF;
        END $$;
        """
    )
    # Drop the legacy id column. ``IF EXISTS`` keeps re-runs idempotent after
    # the column is gone. Sequence backing the column (if any) is dropped
    # with the column.
    op.execute("ALTER TABLE billing_state DROP COLUMN IF EXISTS id")

    for table_name in tables:
        # ENABLE / FORCE are idempotent in Postgres — no guard needed.
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        # FORCE so even the table owner can't bypass the policy — only roles
        # with BYPASSRLS (shu_admin) get through.
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        # CREATE POLICY would fail on a re-run; drop-then-create is the
        # standard idempotent shape and also lets us pick up policy-body
        # changes on re-run.
        _replace_policy(
            table_name,
            "tenant_isolation",
            (
                "AS PERMISSIVE FOR ALL TO shu_app "
                "USING (tenant_id = current_setting('app.tenant_id', true)) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
            ),
        )


def downgrade() -> None:
    tables = list(_TENANT_SCOPED_TABLES)
    inventory = list(_COMPOSITE_FKS)

    # Reverse section E (RLS + billing restructure)
    for table_name in tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    # Restore the legacy ``id = 1`` singleton shape. Downgrade fails noisily
    # if more than one tenant has a billing_state row — collapsing them into
    # a single id=1 would silently drop customer data.
    op.execute(
        """
        DO $$
        DECLARE row_count int;
        BEGIN
            SELECT COUNT(*) INTO row_count FROM billing_state;
            IF row_count > 1 THEN
                RAISE EXCEPTION 'Cannot downgrade billing_state restructure: % rows present (need <= 1 to fit the pre-009 singleton shape)', row_count;
            END IF;
        END $$;
        """
    )
    # Drop the post-009-original UNIQUE on tenant_id if a previous deploy
    # of 009 left it in place — Section B's ``DROP COLUMN tenant_id`` below
    # would otherwise fail (UNIQUE references the column we're about to drop).
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_one_per_tenant")
    op.execute("ALTER TABLE billing_state ADD COLUMN IF NOT EXISTS id INTEGER")
    op.execute("UPDATE billing_state SET id = 1 WHERE id IS NULL")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id SET NOT NULL")
    op.execute("ALTER TABLE billing_state ALTER COLUMN id SET DEFAULT 1")
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_pkey")
    op.execute("ALTER TABLE billing_state ADD CONSTRAINT billing_state_pkey PRIMARY KEY (id)")
    # NOTE: we intentionally do NOT add ``billing_state_one_per_tenant`` (UNIQUE
    # on tenant_id) back here. Pre-009 had no tenant_id column at all, and
    # Section B downgrade will ``DROP COLUMN tenant_id`` shortly — a UNIQUE
    # referencing the column would block that drop without CASCADE.
    #
    # Drop-then-add keeps the downgrade idempotent — the upgrade might have
    # failed before Section E ran, in which case the singleton CHECK is still
    # there from migration 008 and a bare ADD CONSTRAINT would conflict.
    op.execute("ALTER TABLE billing_state DROP CONSTRAINT IF EXISTS billing_state_singleton")
    op.execute("ALTER TABLE billing_state ADD CONSTRAINT billing_state_singleton CHECK (id = 1)")

    # Reverse section D (composite FKs first, then parent uniques)
    for child, child_col, _parent, _parent_col in inventory:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {child}_{child_col}_tfk")

    parent_uniques = sorted({(parent, parent_col) for _, _, parent, parent_col in inventory})
    for parent, parent_col in parent_uniques:
        op.execute(f"ALTER TABLE {parent} DROP CONSTRAINT IF EXISTS {parent}_tenant_id_{parent_col}_unique")

    # Reverse section C
    op.execute("DROP FUNCTION IF EXISTS tenant_for_stripe_customer(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_verification_token(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_reset_token(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_email(text)")
    op.execute("DROP FUNCTION IF EXISTS tenant_for_user_id(text)")

    op.execute("DROP INDEX IF EXISTS uq_billing_state_stripe_customer_id")
    op.execute("DROP INDEX IF EXISTS ix_users_email_verification_token_hash")
    op.execute(
        "CREATE INDEX ix_users_email_verification_token_hash "
        "ON users (email_verification_token_hash) "
        "WHERE email_verification_token_hash IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_password_reset_token_token_hash")
    op.execute("CREATE INDEX ix_password_reset_token_token_hash ON password_reset_token (token_hash)")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_class WHERE relname = 'alembic_version') THEN
                REVOKE SELECT ON alembic_version FROM shu_app;
                REVOKE SELECT ON alembic_version FROM shu_admin;
            END IF;
        END $$;
        """
    )
    op.execute("REVOKE INSERT, UPDATE ON billing_state FROM shu_admin")
    op.execute("REVOKE INSERT, UPDATE ON users FROM shu_admin")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON tenants FROM shu_admin")
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
    op.execute("REVOKE SELECT ON llm_provider_type_definitions FROM shu_app")
    for table_name in ("llm_providers", "llm_models", "plugin_definitions"):
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table_name} FROM shu_app")
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

    # system_settings: restore the original single-column PK on `key` so the
    # subsequent ``DROP COLUMN tenant_id`` doesn't trip on a PK that
    # references it. Symmetric with the upgrade's PK swap.
    op.execute("ALTER TABLE system_settings DROP CONSTRAINT IF EXISTS system_settings_pkey")
    op.execute("ALTER TABLE system_settings ADD CONSTRAINT system_settings_pkey PRIMARY KEY (key)")

    # Reverse the composite-unique swaps: drop the (tenant_id, col)
    # constraints and restore the original single-column UNIQUEs.
    _tenant_unique_swaps_reverse = (
        ("mcp_server_connections", "name", "mcp_server_connections_name_key", "uq_mcp_server_connections_tenant_name"),
        ("access_policies", "name", "access_policies_name_key", "uq_access_policies_tenant_name"),
        ("knowledge_bases", "slug", "knowledge_bases_slug_key", "uq_knowledge_bases_tenant_slug"),
        ("experiences", "slug", "experiences_slug_key", "uq_experiences_tenant_slug"),
        ("user_groups", "name", "user_groups_name_key", "uq_user_groups_tenant_name"),
    )
    for table_name, col, old_constraint, new_constraint in _tenant_unique_swaps_reverse:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {new_constraint}")
        op.execute(
            f"ALTER TABLE {table_name} ADD CONSTRAINT {old_constraint} UNIQUE ({col})"
        )

    # Reverse plugin_storage uniqueness swap: drop the tenant-aware shapes
    # and restore the original global UNIQUE + lookup index + system-scope
    # partial index.
    op.execute("ALTER TABLE plugin_storage DROP CONSTRAINT IF EXISTS uq_plugin_storage_tenant_scope_key")
    op.execute("DROP INDEX IF EXISTS ix_plugin_storage_lookup")
    op.execute("DROP INDEX IF EXISTS uq_plugin_storage_system_scope_key")
    op.execute(
        "ALTER TABLE plugin_storage ADD CONSTRAINT uq_plugin_storage_scope_key "
        "UNIQUE (scope, user_id, plugin_name, namespace, key)"
    )
    op.execute(
        "CREATE INDEX ix_plugin_storage_lookup "
        "ON plugin_storage (scope, user_id, plugin_name, namespace, key)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_plugin_storage_system_scope_key "
        "ON plugin_storage (plugin_name, namespace, key) WHERE scope = 'system'"
    )

    for table_name in tables:
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS tenant_id")

    # Reverse section A
    op.execute("DROP TABLE tenants")
