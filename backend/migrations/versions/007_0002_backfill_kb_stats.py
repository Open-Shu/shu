"""backfill KB stats.

Revision ID: 007_0002
Revises: 007_0001_add_must_change_password
Create Date: 2026-02-23

This migration:
Backfills knowledge_bases.document_count and total_chunks from actual data

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "007_0002"
down_revision = "007_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Backfill document_count for all knowledge bases
    op.execute("""
        UPDATE knowledge_bases kb
        SET document_count = COALESCE(
            (SELECT COUNT(*) FROM documents d WHERE d.knowledge_base_id = kb.id),
            0
        )
    """)

    # Step 2: Backfill total_chunks for all knowledge bases
    op.execute("""
        UPDATE knowledge_bases kb
        SET total_chunks = COALESCE(
            (SELECT COUNT(*) FROM document_chunks dc WHERE dc.knowledge_base_id = kb.id),
            0
        )
    """)
