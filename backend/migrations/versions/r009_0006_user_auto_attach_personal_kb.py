"""Add auto_attach_personal_kb column and documents (kb, created_at) index.

Revision ID: r009_0006
Revises: r009_0005
Create Date: 2026-06-01

SHU-817 — two small, related schema additions for Personal Knowledge v1.5,
combined into one migration:

* ``user_preferences.auto_attach_personal_kb`` — per-user toggle for whether the
  Personal Knowledge KB is auto-attached to new chats. Defaults TRUE so the
  existing behaviour (always auto-attach, per SHU-742) is preserved for current
  users; NOT NULL with a server-side default so existing rows backfill to TRUE.
* ``ix_documents_kb_created_at`` — composite (knowledge_base_id, created_at)
  index. The in-chat document list paginates newest-first within a single KB
  (WHERE knowledge_base_id = :kb ORDER BY created_at DESC LIMIT/OFFSET); the
  existing single-column knowledge_base_id index satisfies the filter but forces
  a sort, while this composite index lets Postgres serve the ordered page
  directly. Matches Document.__table_args__.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0006"
down_revision = "r009_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the auto-attach column (default TRUE) and the KB/created_at index (idempotent)."""
    op.execute(
        """
        ALTER TABLE user_preferences
            ADD COLUMN IF NOT EXISTS auto_attach_personal_kb BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documents_kb_created_at
            ON documents (knowledge_base_id, created_at);
        """
    )


def downgrade() -> None:
    """Drop the index and the auto-attach column (idempotent)."""
    op.execute("DROP INDEX IF EXISTS ix_documents_kb_created_at;")
    op.execute(
        """
        ALTER TABLE user_preferences
            DROP COLUMN IF EXISTS auto_attach_personal_kb;
        """
    )
