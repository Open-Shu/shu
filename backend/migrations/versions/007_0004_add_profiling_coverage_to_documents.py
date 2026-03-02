"""Migration 007_0004: Add profiling_coverage_percent column to documents.

Tracks the percentage of chunks that were successfully profiled for a document.
This enables downstream systems to make decisions based on profiling completeness.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "007_0004"
down_revision = "007_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add profiling_coverage_percent column to documents table."""
    op.add_column(
        "documents",
        sa.Column("profiling_coverage_percent", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Remove profiling_coverage_percent column from documents table."""
    op.drop_column("documents", "profiling_coverage_percent")
