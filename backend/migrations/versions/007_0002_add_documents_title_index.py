"""Add index on documents.title and backfill KB stats.

Revision ID: 007_0002
Revises: 007_0001_add_must_change_password
Create Date: 2026-02-23

This migration:
1. Adds an index on documents.title for efficient ILIKE search
2. Backfills knowledge_bases.document_count and total_chunks from actual data

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "007_0002"
down_revision = "007_0001_add_must_change_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add index on documents.title and backfill KB stats."""
    # Step 1: Add title index for efficient ILIKE search
    op.create_index("idx_documents_title", "documents", ["title"])

    # Step 2: Backfill document_count for all knowledge bases
    op.execute("""
        UPDATE knowledge_bases kb
        SET document_count = COALESCE(
            (SELECT COUNT(*) FROM documents d WHERE d.knowledge_base_id = kb.id),
            0
        )
    """)

    # Step 3: Backfill total_chunks for all knowledge bases
    op.execute("""
        UPDATE knowledge_bases kb
        SET total_chunks = COALESCE(
            (SELECT COUNT(*) FROM document_chunks dc WHERE dc.knowledge_base_id = kb.id),
            0
        )
    """)


def downgrade() -> None:
    """Remove documents.title index. Stats are not reverted (no data loss)."""
    op.drop_index("idx_documents_title", table_name="documents")
