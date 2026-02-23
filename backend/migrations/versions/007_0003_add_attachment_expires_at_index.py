"""Migration 007_0003: Add index on attachments.expires_at for cleanup queries.

The AttachmentCleanupService queries expired attachments periodically. Without
an index, this becomes a full table scan as the attachments table grows.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "007_0003"
down_revision = "007_0002"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_attachments_expires_at"


def upgrade() -> None:
    """Add index on attachments.expires_at for efficient cleanup queries."""
    op.create_index(INDEX_NAME, "attachments", ["expires_at"], if_not_exists=True)


def downgrade() -> None:
    """Remove the expires_at index."""
    op.drop_index(INDEX_NAME, table_name="attachments", if_exists=True)
