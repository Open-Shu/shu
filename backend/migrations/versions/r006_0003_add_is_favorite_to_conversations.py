"""Migration r006_0003: Add is_favorite to conversations

This migration adds the is_favorite column to the conversations table
to support conversation favoriting functionality.

Adds:
- is_favorite boolean column to conversations table (default False)
- Index on is_favorite for efficient filtering
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import (
    column_exists,
    index_exists,
)

# revision identifiers, used by Alembic.
revision = "r006_0003"
down_revision = "r006_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add is_favorite column and index to conversations table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Add is_favorite column if it doesn't exist
    if not column_exists(inspector, "conversations", "is_favorite"):
        op.add_column(
            "conversations",
            sa.Column(
                "is_favorite",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        )

    # Create index on is_favorite if it doesn't exist
    if not index_exists(inspector, "conversations", "ix_conversations_is_favorite"):
        op.create_index(
            "ix_conversations_is_favorite",
            "conversations",
            ["is_favorite"],
        )


def downgrade() -> None:
    """Remove is_favorite column and index from conversations table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop index if it exists
    if index_exists(inspector, "conversations", "ix_conversations_is_favorite"):
        op.drop_index("ix_conversations_is_favorite", "conversations")

    # Drop column if it exists
    if column_exists(inspector, "conversations", "is_favorite"):
        op.drop_column("conversations", "is_favorite")
