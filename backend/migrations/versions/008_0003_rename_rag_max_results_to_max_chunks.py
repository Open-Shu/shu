"""Migration 008_0003: Rename rag_max_results to rag_max_chunks.

Renames the knowledge_bases.rag_max_results column to rag_max_chunks for clarity.
This aligns with the terminology used throughout the codebase (we retrieve chunks,
not results).

Related: SHU-631
"""

import sqlalchemy as sa
from alembic import op

revision = "008_0003"
down_revision = "008_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename rag_max_results column to rag_max_chunks."""
    op.alter_column(
        "knowledge_bases",
        "rag_max_results",
        new_column_name="rag_max_chunks",
    )


def downgrade() -> None:
    """Rename rag_max_chunks column back to rag_max_results."""
    op.alter_column(
        "knowledge_bases",
        "rag_max_chunks",
        new_column_name="rag_max_results",
    )
