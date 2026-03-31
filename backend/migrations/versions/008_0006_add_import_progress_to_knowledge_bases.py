"""Add import_progress to knowledge_bases

Revision ID: 008_0006
Revises: 008_0005
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists

# revision identifiers, used by Alembic.
revision = "008_0006"
down_revision = "008_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    add_column_if_not_exists(
        inspector, "knowledge_bases", sa.Column("import_progress", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    drop_column_if_exists(inspector, "knowledge_bases", "import_progress")
