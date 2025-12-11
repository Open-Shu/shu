"""Simplify provider type definitions to adapter metadata only.

Revision ID: r005_0001
Revises: 004
Create Date: 2025-11-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "r005_0001"
down_revision = "004"
branch_labels = None
depends_on = None


def _drop_column_if_exists(bind, table: str, column: str) -> None:
    inspector = sa.inspect(bind)
    cols = {c.get("name") for c in inspector.get_columns(table)}
    if column in cols:
        op.drop_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()

    # Add provider_adapter_name and backfill from key
    inspector = sa.inspect(bind)
    cols = {c.get("name") for c in inspector.get_columns("llm_provider_type_definitions")}
    if "provider_adapter_name" not in cols:
        op.add_column(
            "llm_provider_type_definitions",
            sa.Column("provider_adapter_name", sa.String(length=100), nullable=True),
        )
        op.execute("UPDATE llm_provider_type_definitions SET provider_adapter_name = key")
        op.alter_column(
            "llm_provider_type_definitions",
            "provider_adapter_name",
            existing_type=sa.String(length=100),
            nullable=False,
        )

    # Drop legacy data-driven columns
    for col in (
        "base_url_template",
        "endpoints",
        "auth",
        "parameter_mapping",
        "streaming",
        "notes",
    ):
        _drop_column_if_exists(bind, "llm_provider_type_definitions", col)

    upsert_llm_provider_type_definitions(op)


def downgrade() -> None:
    bind = op.get_bind()

    # Recreate dropped columns as nullable to allow downgrade paths.
    for col, coltype in (
        ("base_url_template", sa.Text()),
        ("endpoints", sa.JSON()),
        ("auth", sa.JSON()),
        ("parameter_mapping", sa.JSON()),
        ("streaming", sa.String(length=50)),
        ("notes", sa.JSON()),
    ):
        inspector = sa.inspect(bind)
        cols = {c.get("name") for c in inspector.get_columns("llm_provider_type_definitions")}
        if col not in cols:
            op.add_column(
                "llm_provider_type_definitions",
                sa.Column(col, coltype, nullable=True),
            )

    _drop_column_if_exists(bind, "llm_provider_type_definitions", "provider_adapter_name")
