"""Migration r006_0005: Add document pipeline status field

This migration adds the status and error_message columns to the documents table
for tracking document progress through the async ingestion pipeline.

Changes:
- Add status column (String(20), default='ready', not null)
- Add error_message column (Text, nullable)
- Create index ix_documents_status for efficient status queries

Idempotency guarantees:
- Upgrade: Safe to run multiple times. Skips if columns/indexes already exist.
- Downgrade: Safe to run multiple times. Only drops if columns/indexes exist.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import (
    add_column_if_not_exists,
    drop_column_if_exists,
    index_exists,
)

# revision identifiers, used by Alembic.
revision = "r006_0005"
down_revision = "r006_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add status and error_message columns to documents table.

    Idempotent: Safe to run multiple times.
    - If columns already exist, skips column creation.
    - If index already exists, skips index creation.
    - Ensures existing documents have 'ready' status.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Add status column if not exists
    # Default to 'ready' for existing documents (they are already processed)
    add_column_if_not_exists(
        inspector,
        "documents",
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
    )

    # Add error_message column if not exists
    add_column_if_not_exists(
        inspector,
        "documents",
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    # Create index if not exists
    if not index_exists(inspector, "documents", "ix_documents_status"):
        op.create_index("ix_documents_status", "documents", ["status"])

    # Ensure existing documents have 'ready' status (idempotent)
    # This handles any edge cases where status might be NULL or empty
    op.execute("UPDATE documents SET status = 'ready' WHERE status IS NULL OR status = ''")


def downgrade() -> None:
    """Remove status and error_message columns from documents table.

    Idempotent: Safe to run multiple times.
    - If index doesn't exist, skips index drop.
    - If columns don't exist, skips column drops.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop index if exists
    if index_exists(inspector, "documents", "ix_documents_status"):
        op.drop_index("ix_documents_status", "documents")

    # Drop columns if exist
    drop_column_if_exists(inspector, "documents", "error_message")
    drop_column_if_exists(inspector, "documents", "status")
