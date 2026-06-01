"""Add composite (knowledge_base_id, created_at) index to documents.

Revision ID: r009_0007
Revises: r009_0006
Create Date: 2026-06-01

SHU-817 (F1) — the in-chat document list paginates newest-first within a single
KB (WHERE knowledge_base_id = :kb ORDER BY created_at DESC LIMIT/OFFSET). The
existing single-column knowledge_base_id index satisfies the filter but forces a
sort within the KB's rows; this composite index lets Postgres serve the ordered
page directly (forward or backward scan). Matches Document.__table_args__.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0007"
down_revision = "r009_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the composite KB/created_at index (idempotent)."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documents_kb_created_at
            ON documents (knowledge_base_id, created_at);
        """
    )


def downgrade() -> None:
    """Drop the composite KB/created_at index (idempotent)."""
    op.execute("DROP INDEX IF EXISTS ix_documents_kb_created_at;")
