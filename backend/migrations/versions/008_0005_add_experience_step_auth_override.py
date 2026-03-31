"""Migration 008_0005: Add auth_override column to experience_steps

Adds a nullable JSON column ``auth_override`` to ``experience_steps`` so that
individual steps can opt into Domain-Wide Delegation or other non-default auth
modes.

Part of SHU-426: Domain-Wide Delegation for Experience Steps.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists

# revision identifiers, used by Alembic.
revision = "008_0005"
down_revision = "008_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add auth_override column to experience_steps table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    add_column_if_not_exists(
        inspector,
        "experience_steps",
        sa.Column("auth_override", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    """Remove auth_override column from experience_steps table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    drop_column_if_exists(inspector, "experience_steps", "auth_override")
