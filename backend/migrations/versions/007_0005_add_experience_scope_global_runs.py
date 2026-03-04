"""Migration 007_0005: Add scope column to experiences and make user_id nullable in experience_runs.

Introduces global experiences — experiences that run once and share their result with all users.
- experiences.scope (VARCHAR 20, NOT NULL, default 'user') distinguishes per-user vs global experiences.
- experience_runs.user_id becomes nullable; global runs store user_id = NULL.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "007_0005"
down_revision = "007_0004"
branch_labels = None
depends_on = None

SCOPE_INDEX = "idx_experiences_scope"


def upgrade() -> None:
    """Add scope column to experiences; allow nullable user_id in experience_runs."""
    # Add scope column with server default so existing rows get 'user'
    op.add_column(
        "experiences",
        sa.Column("scope", sa.String(20), nullable=False, server_default="user"),
    )
    op.create_index(SCOPE_INDEX, "experiences", ["scope"])

    # Make experience_runs.user_id nullable for global runs (user_id = NULL)
    op.alter_column("experience_runs", "user_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Restore NOT NULL on user_id; remove scope column and index."""
    # Restore NOT NULL — note: any global runs (user_id IS NULL) must be removed first
    op.alter_column("experience_runs", "user_id", existing_type=sa.String(), nullable=False)

    op.drop_index(SCOPE_INDEX, table_name="experiences")
    op.drop_column("experiences", "scope")
