"""Add composite index on llm_usage for per-user time-windowed queries.

Revision ID: r009_0007
Revises: r009_0006
Create Date: 2026-06-04

SHU-844 — the per-user "My Usage" dashboard aggregates llm_usage filtered by
user_id (tenant isolation comes from RLS) and windowed on created_at. The live
table has only ix_llm_usage_tenant_id; the model declares index=True on user_id
but the index was never actually created (model/migration drift, see SHU-844
notes). This adds the composite index that backs both the per-user model
breakdown (WHERE user_id = :uid AND created_at >= :start) and the
date_trunc('day', created_at) daily rollup.

Built CONCURRENTLY because llm_usage is a hot, ever-growing table — a plain
CREATE INDEX would hold an ACCESS EXCLUSIVE lock and block writes for the whole
build. Mirrors the CONCURRENTLY + invalid-index-guard pattern in 009.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "r009_0007"
down_revision = "r009_0006"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_llm_usage_tenant_user_created"
_TABLE = "llm_usage"
_COLUMNS = "(tenant_id, user_id, created_at)"


def upgrade() -> None:
    """Create the composite index CONCURRENTLY (idempotent).

    CONCURRENTLY needs autocommit — alembic's autocommit_block() ends the
    current transaction, runs the body outside a tx, then opens a fresh one.
    An interrupted CREATE INDEX CONCURRENTLY leaves an index in the catalog
    with pg_index.indisvalid = false; IF NOT EXISTS would silently skip the
    rebuild and the migration would "succeed" with a non-functional index, so
    drop any invalid sibling first.
    """
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        invalid = conn.execute(
            text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND i.indisvalid = false"
            ),
            {"name": _INDEX_NAME},
        ).first()
        if invalid is not None:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME} ON {_TABLE} {_COLUMNS}")


def downgrade() -> None:
    """Drop the composite index CONCURRENTLY (idempotent)."""
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
