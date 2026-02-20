"""Migration 007_0001: Add must_change_password column to users table.

Part of the Change Password feature (SHU-565). Adds a boolean flag that forces
users to change their password on next login after an admin-initiated reset.

Requirements: 5.1, 5.2
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists

# revision identifiers, used by Alembic.
revision = "007_0001"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add must_change_password column to users table."""
    inspector = sa.inspect(op.get_bind())
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("must_change_password", sa.Boolean, server_default="false", nullable=False),
    )


def downgrade() -> None:
    """Remove must_change_password column from users table."""
    inspector = sa.inspect(op.get_bind())
    drop_column_if_exists(inspector, "users", "must_change_password")
