"""Widen llm_usage cost columns and add billing_state tables.

1. Widen llm_usage cost columns to DECIMAL(16,9)
   OpenRouter returns per-request costs with up to 9 decimal places.
   The previous DECIMAL(10,6) truncated small costs (e.g., single-token
   embeddings at $0.000000015). This migration preserves full precision
   for accurate usage-based billing via Stripe Meters.

2. Create billing_state singleton table (replaces system_settings["billing"])
   Typed columns + row-level locking eliminates the read-modify-write race
   condition that exists when concurrent Stripe webhook events each read the
   same JSON blob and clobber each other's field updates.

3. Create billing_state_audit append-only audit log

All operations are idempotent — safe to run on a database that already has
some or all of these changes applied.

Revision ID: 008_0008
Revises: 008_0007
Create Date: 2026-04-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "008_0008"
down_revision = "008_0007"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _column_numeric_scale(conn: sa.engine.Connection, table: str, column: str) -> int | None:
    """Return the numeric_scale for a NUMERIC/DECIMAL column, or None if not found."""
    row = conn.execute(
        sa.text(
            "SELECT numeric_scale FROM information_schema.columns "
            "WHERE table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _table_exists(conn: sa.engine.Connection, table: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_name = :tbl"
            ")"
        ),
        {"tbl": table},
    ).scalar()
    return bool(row)


def _index_exists(conn: sa.engine.Connection, index: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT EXISTS("
            "  SELECT 1 FROM pg_indexes WHERE indexname = :idx"
            ")"
        ),
        {"idx": index},
    ).scalar()
    return bool(row)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Widen llm_usage cost columns (idempotent: skip if already wide)
    # ------------------------------------------------------------------
    for column_name in ("input_cost", "output_cost", "total_cost"):
        current_scale = _column_numeric_scale(conn, "llm_usage", column_name)
        if current_scale != 9:
            op.alter_column(
                "llm_usage",
                column_name,
                type_=sa.DECIMAL(16, 9),
                existing_type=sa.DECIMAL(10, 6),
                existing_nullable=False,
                existing_server_default=sa.text("0"),
            )

    # ------------------------------------------------------------------
    # 2. Create billing_state singleton table
    # ------------------------------------------------------------------
    if not _table_exists(conn, "billing_state"):
        op.create_table(
            "billing_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            # Stripe identity
            sa.Column("stripe_customer_id", sa.Text(), nullable=True),
            sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
            sa.Column("billing_email", sa.Text(), nullable=True),
            # Subscription lifecycle
            sa.Column(
                "subscription_status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "current_period_start",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "current_period_end",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "quantity",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            # Usage metering bookkeeping
            sa.Column(
                "last_reported_total",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "last_reported_period_start",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
            ),
            # Payment lifecycle
            sa.Column(
                "payment_failed_at",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
            ),
            # User limit enforcement
            sa.Column(
                "user_limit_enforcement",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'soft'"),
            ),
            # Optimistic locking
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            # Constraints
            sa.CheckConstraint("id = 1", name="billing_state_singleton"),
            sa.CheckConstraint(
                "user_limit_enforcement IN ('soft', 'hard', 'none')",
                name="billing_state_enforcement_check",
            ),
        )

    # ------------------------------------------------------------------
    # 3. Create billing_state_audit append-only log
    # ------------------------------------------------------------------
    if not _table_exists(conn, "billing_state_audit"):
        op.create_table(
            "billing_state_audit",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column(
                "changed_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("changed_by", sa.Text(), nullable=True),
            sa.Column("field_name", sa.Text(), nullable=False),
            sa.Column("old_value", JSONB(), nullable=True),
            sa.Column("new_value", JSONB(), nullable=True),
            sa.Column("stripe_event_id", sa.Text(), nullable=True),
        )

    if not _index_exists(conn, "idx_billing_state_audit_changed_at"):
        op.create_index(
            "idx_billing_state_audit_changed_at",
            "billing_state_audit",
            ["changed_at"],
        )

    if not _index_exists(conn, "idx_billing_state_audit_stripe_event_id"):
        op.create_index(
            "idx_billing_state_audit_stripe_event_id",
            "billing_state_audit",
            ["stripe_event_id"],
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    conn = op.get_bind()

    # Drop billing_state_audit indexes and table
    if _index_exists(conn, "idx_billing_state_audit_stripe_event_id"):
        op.drop_index("idx_billing_state_audit_stripe_event_id", table_name="billing_state_audit")
    if _index_exists(conn, "idx_billing_state_audit_changed_at"):
        op.drop_index("idx_billing_state_audit_changed_at", table_name="billing_state_audit")
    if _table_exists(conn, "billing_state_audit"):
        op.drop_table("billing_state_audit")

    # Drop billing_state table
    if _table_exists(conn, "billing_state"):
        op.drop_table("billing_state")

    # Narrow llm_usage cost columns back to DECIMAL(10,6)
    for column_name in ("input_cost", "output_cost", "total_cost"):
        current_scale = _column_numeric_scale(conn, "llm_usage", column_name)
        if current_scale != 6:
            op.alter_column(
                "llm_usage",
                column_name,
                type_=sa.DECIMAL(10, 6),
                existing_type=sa.DECIMAL(16, 9),
                existing_nullable=False,
                existing_server_default=sa.text("0"),
            )
