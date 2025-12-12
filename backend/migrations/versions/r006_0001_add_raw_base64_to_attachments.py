"""Add raw_base64 column to attachments table.

Revision ID: r006_0001
Revises: 005
Create Date: 2025-12-12
"""

from alembic import op
import sqlalchemy as sa


revision = "r006_0001"
down_revision = "r005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("raw_base64", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("attachments", "raw_base64")
