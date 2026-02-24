"""Migration 007_0004: Add one_liner column to document_chunks table.

Adds a condensed summary field (~50-80 chars) for agent scanning during
agentic search. One-liners enable agents to scan and select chunks without
reading full content.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists

# revision identifiers, used by Alembic.
revision = "007_0004"
down_revision = "007_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add one_liner column to document_chunks table."""
    inspector = sa.inspect(op.get_bind())
    add_column_if_not_exists(
        inspector,
        "document_chunks",
        sa.Column("one_liner", sa.Text, nullable=True),
    )


def downgrade() -> None:
    """Remove one_liner column from document_chunks table."""
    inspector = sa.inspect(op.get_bind())
    drop_column_if_exists(inspector, "document_chunks", "one_liner")
