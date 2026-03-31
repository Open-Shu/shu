"""Add import_progress to knowledge_bases

Revision ID: 008_0006
Revises: 008_0005
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "008_0006"
down_revision = "008_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column("import_progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_bases", "import_progress")
