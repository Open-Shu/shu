"""SHU-730 schema changes: deactivation flag + drop cached seat counts.

1. Add ``deactivation_scheduled_at`` to ``users``. Records the cycle a user
   was marked for deactivation when a seat-decrease was requested mid-cycle.
   The rollover handler flips ``is_active=False`` for flagged rows on
   ``invoice.paid`` / ``billing_reason=subscription_cycle``. A partial index
   covers the rollover SELECT without paying to index the ~99% NULL rows.

2. Drop ``billing_state.quantity`` (and ``target_quantity`` if a manual
   ALTER TABLE put it there during dev). Stripe is now the source of truth
   for seat counts — the cached columns introduced races on every webhook
   delivery and are no longer read or written.

Revision ID: 008_0012
Revises: 008_0011
Create Date: 2026-04-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists, index_exists

# revision identifiers, used by Alembic.
revision = "008_0012"
down_revision = "008_0011"
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

    drop_column_if_exists(inspector, "billing_state", "quantity")
    drop_column_if_exists(inspector, "billing_state", "target_quantity")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_deactivation_scheduled")
    op.drop_column("users", "deactivation_scheduled_at")
    op.add_column(
        "billing_state",
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
