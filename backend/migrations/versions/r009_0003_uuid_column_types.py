"""Convert tenants.id and every tenant_id column to native `uuid`.

Revision ID: r009_0003
Revises: r009_0002
Create Date: 2026-05-22

Narrow scope: only the columns SHU-761 actually introduced and reads in
its RLS hot path. Other UUID-storing columns across the schema (every
``id`` column on every BaseModel-inheriting table, every ``String(36)``
FK column) stay as ``text`` / ``varchar`` for now — flipping those is a
deliberate type-uniformity sweep we may do later, but it's not what the
reviewer's specific pushback on 009 was about.

What this migration touches:

* ``tenants.id`` (the parent of every ``tenant_id`` FK).
* ``tenant_id`` on each of the 39 tenant-scoped tables enumerated below.

Why uuid here:

* The reviewer flagged ``tenants.id text PRIMARY KEY`` in 009 because:
  - storage is ~2.5x smaller as native uuid (16 vs ~40 bytes per cell),
  - comparisons are a single 128-bit op vs byte-by-byte string compare,
  - the column type itself enforces UUID shape on insert.
* The savings show up most on ``tenant_id`` indexes, which are scanned
  by every RLS-policied query in the database.
* RLS predicates change from ``tenant_id = current_setting(...)`` to
  ``tenant_id = current_setting(...)::uuid``. One cast site per policy,
  fired once per query at policy-evaluation time — not per row.

Strategy:

1. Snapshot every FK on ``tenants`` and on every tenant-scoped table via
   ``pg_get_constraintdef()`` — captures the canonical Postgres-rendered
   SQL so we can round-trip without hand-listing each constraint.
2. Drop all snapshotted FKs and the ``tenant_isolation`` RLS policy on
   every tenant-scoped table. Columns can't change type while either
   binds them.
3. ``ALTER tenants.id`` to uuid (after ``DROP DEFAULT`` — see below).
4. ``ALTER`` each ``tenant_id`` to uuid (after ``DROP DEFAULT``).
5. Restore FKs from the snapshot. Postgres re-validates against the new
   uuid columns on the way back in.
6. Recreate RLS policies with the ``::uuid`` cast on the GUC.

DROP DEFAULT step: 009 added every ``tenant_id`` column with a text
``DEFAULT '<placeholder-uuid>'`` so ``ADD COLUMN NOT NULL`` was
metadata-only on PG 11+. Postgres won't auto-cast that text default to
uuid, so we shed it first. App code always supplies ``tenant_id``
explicitly via the ``tenant_context`` ContextVar, so the default isn't
needed post-migration.

ORM-side changes that go in the same PR (NOT in this migration file):
* ``TenantScopedMixin.tenant_id`` flips from ``String`` to
  ``UUID(as_uuid=False)`` so Python keeps round-tripping strings (no
  code churn on comparisons or JSON serialization).
* ``Tenant.id`` flips the same way.

Downgrade casts uuid back to ``text``. Note that this does NOT restore
the original ``varchar(36)`` constraint on any column — the original
``tenant_id`` columns were already declared as raw ``text`` by 009, so
the downgrade is a faithful inverse for *these* columns only. Columns
this migration never touches are unaffected.

Policy: idempotent only on a clean re-run after a successful upgrade;
not partial-re-runnable (temp tables drop at session end). Use
``alembic downgrade`` to revert, not partial re-application.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0003"
down_revision = "r009_0002"
branch_labels = None
depends_on = None


_TENANTS_TABLE = "tenants"

# Mirror of 009's `_TENANT_SCOPED_TABLES`. If 009 ever picks up a new
# tenant-scoped table, this list grows in lockstep — same drift hazard
# 009 already documents.
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


def _touched_tables_sql() -> str:
    """Postgres array literal of the tables we snapshot / restore FKs on."""
    tables = (_TENANTS_TABLE,) + _TENANT_SCOPED_TABLES
    return ",".join(f"'{t}'" for t in tables)


def upgrade() -> None:
    """tenants.id + every tenant_id column → uuid."""

    # 0. Guard the bare ALTERs below against a missing / already-converted
    #    schema (DB_MIGRATION_POLICY §Policy — never issue an unguarded ALTER):
    #    * tenants.id absent  → 009_00011 (this migration's schema prerequisite)
    #      never ran. With 009_00011 re-slotted ahead of r009_0002 that can't
    #      happen on a forward upgrade; if it does the chain is broken, so fail
    #      loud with a
    #      pointer rather than the opaque `relation "tenants" does not exist`.
    #    * tenants.id already uuid → the conversion ran before; re-run is a
    #      no-op (makes the migration re-runnable after a stamp-only recovery).
    bind = op.get_bind()
    id_type = bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'id'"
        ),
        {"t": _TENANTS_TABLE},
    ).scalar()
    if id_type is None:
        raise RuntimeError(
            f'relation "{_TENANTS_TABLE}" / its id column is missing — migration '
            "009_00011 (tenant_isolation) must run before r009_0003. The revision "
            "chain is 008 → r009_0001 → 009_00011 → r009_0002 → r009_0003; check "
            "down_revisions."
        )
    if id_type == "uuid":
        return

    # 1. Snapshot every FK touching one of our tables. Captures both the
    #    composite FKs from 009 (the (col, tenant_id) → (id, tenant_id)
    #    shape) and the simple tenant_id → tenants.id FKs.
    op.execute(
        """
        CREATE TEMP TABLE _r009_0003_fk_snapshot (
            table_name text NOT NULL,
            constraint_name text NOT NULL,
            definition text NOT NULL
        ) ON COMMIT DROP
        """
    )
    op.execute(
        f"""
        INSERT INTO _r009_0003_fk_snapshot (table_name, constraint_name, definition)
        SELECT cls.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        LEFT JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
        WHERE con.contype = 'f'
          AND (cls.relname = ANY (ARRAY[{_touched_tables_sql()}])
               OR ref_cls.relname = ANY (ARRAY[{_touched_tables_sql()}]))
        """
    )

    # 2. Drop all snapshotted FKs and every tenant_isolation policy.
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT table_name, constraint_name FROM _r009_0003_fk_snapshot LOOP
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                               r.table_name, r.constraint_name);
            END LOOP;
        END $$
        """
    )
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    # 3. Flip tenants.id.
    op.execute(f"ALTER TABLE {_TENANTS_TABLE} ALTER COLUMN id DROP DEFAULT")
    op.execute(
        f"ALTER TABLE {_TENANTS_TABLE} ALTER COLUMN id TYPE uuid USING id::uuid"
    )

    # 4. Flip every tenant_id column. DROP DEFAULT first to shed the
    #    009-era text placeholder; app code always supplies tenant_id
    #    explicitly so the default isn't re-added.
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE uuid USING tenant_id::uuid"
        )

    # 5. Restore FKs verbatim from the snapshot.
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT table_name, constraint_name, definition FROM _r009_0003_fk_snapshot LOOP
                EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I %s',
                               r.table_name, r.constraint_name, r.definition);
            END LOOP;
        END $$
        """
    )

    # 6. Recreate RLS policies with the ::uuid cast on the GUC. The GUC
    #    itself stays text (set_config takes text), so the cast happens
    #    once per query at policy-evaluation time, not per row.
    for table in _TENANT_SCOPED_TABLES:
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """
        )


def downgrade() -> None:
    """Symmetric reverse: uuid → text on the same column set."""

    op.execute(
        """
        CREATE TEMP TABLE _r009_0003_fk_snapshot (
            table_name text NOT NULL,
            constraint_name text NOT NULL,
            definition text NOT NULL
        ) ON COMMIT DROP
        """
    )
    op.execute(
        f"""
        INSERT INTO _r009_0003_fk_snapshot (table_name, constraint_name, definition)
        SELECT cls.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        LEFT JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
        WHERE con.contype = 'f'
          AND (cls.relname = ANY (ARRAY[{_touched_tables_sql()}])
               OR ref_cls.relname = ANY (ARRAY[{_touched_tables_sql()}]))
        """
    )

    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT table_name, constraint_name FROM _r009_0003_fk_snapshot LOOP
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                               r.table_name, r.constraint_name);
            END LOOP;
        END $$
        """
    )
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.execute(f"ALTER TABLE {_TENANTS_TABLE} ALTER COLUMN id DROP DEFAULT")
    op.execute(
        f"ALTER TABLE {_TENANTS_TABLE} ALTER COLUMN id TYPE text USING id::text"
    )
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE text USING tenant_id::text"
        )

    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT table_name, constraint_name, definition FROM _r009_0003_fk_snapshot LOOP
                EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I %s',
                               r.table_name, r.constraint_name, r.definition);
            END LOOP;
        END $$
        """
    )

    # Restore the pre-r009_0003 RLS policy shape (no ::uuid cast).
    for table in _TENANT_SCOPED_TABLES:
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            AS PERMISSIVE FOR ALL TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
            """
        )
