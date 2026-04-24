"""Add deactivation_scheduled_at column to users.

Records the period-end at which a user was marked for deactivation when a
seat-decrease was requested while still mid-cycle (SHU-730). The webhook
rollover handler reads this column on `invoice.paid` /
`billing_reason=subscription_cycle` and flips `is_active=False` for rows
whose `deactivation_scheduled_at <= now()`.

A partial index covers the rollover SELECT without paying the cost of
indexing the ~99% of rows whose value is NULL.

Revision ID: 008_0011
Revises: 008_0010
Create Date: 2026-04-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.helpers import add_column_if_not_exists, index_exists

# revision identifiers, used by Alembic.
revision = "008_0011"
down_revision = "008_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("deactivation_scheduled_at", TIMESTAMP(timezone=True), nullable=True),
    )

    if not index_exists(inspector, "users", "ix_users_deactivation_scheduled"):
        op.create_index(
            "ix_users_deactivation_scheduled",
            "users",
            ["deactivation_scheduled_at"],
            postgresql_where=sa.text("deactivation_scheduled_at IS NOT NULL"),
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_deactivation_scheduled")
    op.drop_column("users", "deactivation_scheduled_at")
