"""Widen trigger_type column and create experience_dependencies table

Revision ID: 008_0007
Revises: 008_0006
Create Date: 2026-04-07
"""

import sqlalchemy as sa
from alembic import op

revision = "008_0007"
down_revision = "008_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "experiences",
        "trigger_type",
        existing_type=sa.String(20),
        type_=sa.String(50),
        existing_nullable=False,
    )

    op.create_table(
        "experience_dependencies",
        sa.Column(
            "aggregate_experience_id",
            sa.String(36),
            sa.ForeignKey("experiences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dependency_experience_id",
            sa.String(36),
            sa.ForeignKey("experiences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("aggregate_experience_id", "dependency_experience_id"),
    )
    op.create_index(
        "idx_exp_deps_dependency",
        "experience_dependencies",
        ["dependency_experience_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_exp_deps_dependency", table_name="experience_dependencies")
    op.drop_table("experience_dependencies")

    op.alter_column(
        "experiences",
        "trigger_type",
        existing_type=sa.String(50),
        type_=sa.String(20),
        existing_nullable=False,
    )
