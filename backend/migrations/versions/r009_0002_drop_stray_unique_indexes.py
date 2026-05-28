"""Drop stray pre-RLS unique indexes on tenant-scoped columns.

Revision ID: r009_0002
Revises: r009_0001
Create Date: 2026-05-21

Before SHU-761, several tenant-scoped tables were created with
``Column(name, unique=True)`` (and matching ``slug`` columns), which
Postgres backed with a UNIQUE constraint named ``<table>_<column>_key``
plus an automatically-named unique INDEX. Migration 009 dropped the
named constraints and added composite ``(tenant_id, <column>)`` UNIQUEs
in their place — but on at least some Postgres versions / migration
histories the unique enforcement also persists as a separately-named
index (e.g. ``ix_access_policies_name``) that 009 never targets.

Symptom: a second tenant trying to use a policy name that another
tenant already uses fails with ``duplicate key value violates unique
constraint "ix_access_policies_name"``, defeating the per-tenant
uniqueness the model documents.

Fix: for every table 009's ``_tenant_scoped_unique_swaps`` touched,
unconditionally drop the legacy ``<table>_<column>_key`` constraint
(no-op when 009's drop already succeeded) AND drop any ``ix_<table>_<col>``
index that may exist, then recreate the latter as non-unique so the
query-time index lookup the model declares via ``index=True`` is still
there. Per-tenant uniqueness is preserved by the composite
``uq_<table>_tenant_<col>`` constraint that 009 added.

Idempotent: every statement uses ``IF EXISTS`` / ``IF NOT EXISTS``, so
re-running on a clean schema is a no-op.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0002"
down_revision = "r009_0001"
branch_labels = None
depends_on = None


# (table, column) — mirror of 009's `_tenant_scoped_unique_swaps`. If 009
# ever adds another table to that list, this list grows in lockstep.
_AFFECTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mcp_server_connections", "name"),
    ("access_policies", "name"),
    ("knowledge_bases", "slug"),
    ("experiences", "slug"),
    ("user_groups", "name"),
)


def upgrade() -> None:
    """Drop the legacy global-unique enforcement; keep a plain index."""
    for table, col in _AFFECTED_COLUMNS:
        index_name = f"ix_{table}_{col}"
        legacy_constraint = f"{table}_{col}_key"
        # Drop the known Postgres-default constraint name first. 009 already
        # attempts this; IF EXISTS makes the second attempt a no-op.
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {legacy_constraint}")

        # Drop contraints
        op.execute(
            f"""
            DO $$
            DECLARE
                con_name text;
                con_table text;
            BEGIN
                SELECT c.conname, t.relname
                  INTO con_name, con_table
                  FROM pg_constraint c
                  JOIN pg_class i ON c.conindid = i.oid
                  JOIN pg_class t ON c.conrelid = t.oid
                 WHERE i.relname = '{index_name}'
                 LIMIT 1;
                IF con_name IS NOT NULL THEN
                    RAISE NOTICE 'r009_0002: dropping constraint % on % (backed by %)',
                        con_name, con_table, '{index_name}';
                    EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                                   con_table, con_name);
                END IF;
            END
            $$;
            """
        )
        # Now the index can be dropped without CASCADE — any constraint
        # owning it was removed by the block above.
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        # Recreate as a plain (non-unique) index to keep query lookups on
        # the column cheap. The model declares `index=True` so this is the
        # shape the ORM expects on the live table.
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({col})")


def downgrade() -> None:
    """Recreating the legacy global-unique indexes is intentionally not done.

    The original `unique=True` was a pre-RLS bug — it prevented two tenants
    from picking the same name, which contradicts the per-tenant uniqueness
    that ``uq_<table>_tenant_<col>`` enforces. Downgrade only drops the plain
    index this migration created; it does not restore the broken constraint.
    """
    for table, col in _AFFECTED_COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_{col}")
