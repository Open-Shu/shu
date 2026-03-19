"""Migration 008_0003: Empty placeholder migration.

This is an intentionally empty migration used to maintain revision ordering.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "008_0003"
down_revision = "008_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
