"""Widen llm_usage cost columns to DECIMAL(16,9)

OpenRouter returns per-request costs with up to 9 decimal places.
The previous DECIMAL(10,6) truncated small costs (e.g., single-token
embeddings at $0.000000015). This migration preserves full precision
for accurate usage-based billing via Stripe Meters.

Revision ID: 008_0008
Revises: 008_0007
Create Date: 2026-04-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "008_0008"
down_revision = "008_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column_name in ("input_cost", "output_cost", "total_cost"):
        op.alter_column(
            "llm_usage",
            column_name,
            type_=sa.DECIMAL(16, 9),
            existing_type=sa.DECIMAL(10, 6),
            existing_nullable=False,
            existing_server_default=sa.text("0"),
        )


def downgrade() -> None:
    for column_name in ("input_cost", "output_cost", "total_cost"):
        op.alter_column(
            "llm_usage",
            column_name,
            type_=sa.DECIMAL(10, 6),
            existing_type=sa.DECIMAL(16, 9),
            existing_nullable=False,
            existing_server_default=sa.text("0"),
        )
