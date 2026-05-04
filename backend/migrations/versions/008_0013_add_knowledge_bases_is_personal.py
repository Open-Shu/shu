"""Add is_personal column to knowledge_bases.

Distinguishes user-owned "Personal Knowledge" KBs (auto-provisioned from the
in-chat brain icon flow, SHU-742) from regular user-created or system KBs.
The flag lets the create service apply Personal-specific defaults (e.g.,
Full Document Escalation enabled) and the frontend find a user's Personal
KB by flag rather than by name pattern (since names are now derived from
the user's identity).

Existing rows receive FALSE via the column default.

Revision ID: 008_0013
Revises: 008_0012
Create Date: 2026-05-04
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists

# revision identifiers, used by Alembic.
revision = "008_0013"
down_revision = "008_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    add_column_if_not_exists(
        inspector,
        "knowledge_bases",
        sa.Column(
            "is_personal",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "is_personal")
