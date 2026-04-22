"""Add is_system_managed column to llm_providers.

Introduces provider provenance tracking (SHU-705). The flag distinguishes
Shu-seeded providers (whose usage is billable and whose rows are locked
against customer-facing mutation) from customer-added BYOK providers.

Existing rows receive FALSE via the column default; operator adjusts seeded
rows via a runbook step outside this migration's scope.

Revision ID: 008_0010
Revises: 008_0009
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists

# revision identifiers, used by Alembic.
revision = "008_0010"
down_revision = "008_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    add_column_if_not_exists(
        inspector,
        "llm_providers",
        sa.Column(
            "is_system_managed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_providers", "is_system_managed")
