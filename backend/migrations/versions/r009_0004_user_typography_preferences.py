"""Add font_family and font_size_scale columns to user_preferences.

Revision ID: r009_0004
Revises: r009_0003
Create Date: 2026-05-27

SHU-811 — adds per-user typography preferences. Both columns are
nullable; null means "inherit from branding / shipped default" per the
cascade implemented in the frontend ThemeContext. No data backfill is
required.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0004"
down_revision = "r009_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add font_family and font_size_scale columns (idempotent)."""
    op.execute(
        """
        ALTER TABLE user_preferences
            ADD COLUMN IF NOT EXISTS font_family VARCHAR(50),
            ADD COLUMN IF NOT EXISTS font_size_scale VARCHAR(20);
        """
    )


def downgrade() -> None:
    """Drop the typography columns (idempotent)."""
    op.execute(
        """
        ALTER TABLE user_preferences
            DROP COLUMN IF EXISTS font_family,
            DROP COLUMN IF EXISTS font_size_scale;
        """
    )
