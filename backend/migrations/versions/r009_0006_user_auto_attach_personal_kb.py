"""Add auto_attach_personal_kb column to user_preferences.

Revision ID: r009_0006
Revises: r009_0005
Create Date: 2026-06-01

SHU-817 — per-user toggle for whether the Personal Knowledge KB is
auto-attached to new chats. Defaults TRUE so the existing behaviour (always
auto-attach, per SHU-742) is preserved for current users; the column is NOT
NULL with a server-side default, so existing rows backfill to TRUE.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0006"
down_revision = "r009_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add auto_attach_personal_kb column, default TRUE (idempotent)."""
    op.execute(
        """
        ALTER TABLE user_preferences
            ADD COLUMN IF NOT EXISTS auto_attach_personal_kb BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )


def downgrade() -> None:
    """Drop the auto_attach_personal_kb column (idempotent)."""
    op.execute(
        """
        ALTER TABLE user_preferences
            DROP COLUMN IF EXISTS auto_attach_personal_kb;
        """
    )
