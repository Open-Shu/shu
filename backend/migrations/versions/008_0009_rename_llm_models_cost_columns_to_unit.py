"""Rename llm_models cost_per_input_token / cost_per_output_token to _unit.

The OCR model type stores per-page rates in the same columns as chat/embedding
models store per-token rates; `llm_models.model_type` already disambiguates the
unit (chat/embedding -> per-token, ocr -> per-page). Rename the columns to
`cost_per_input_unit` / `cost_per_output_unit` so the schema no longer claims
the rate is per-token.

Idempotent — safe to re-run.

Revision ID: 008_0009
Revises: 008_0008
Create Date: 2026-04-17
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import column_exists

# revision identifiers, used by Alembic.
revision = "008_0009"
down_revision = "008_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if column_exists(inspector, "llm_models", "cost_per_input_token"):
        op.alter_column(
            "llm_models",
            "cost_per_input_token",
            new_column_name="cost_per_input_unit",
        )

    if column_exists(inspector, "llm_models", "cost_per_output_token"):
        op.alter_column(
            "llm_models",
            "cost_per_output_token",
            new_column_name="cost_per_output_unit",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if column_exists(inspector, "llm_models", "cost_per_input_unit"):
        op.alter_column(
            "llm_models",
            "cost_per_input_unit",
            new_column_name="cost_per_input_token",
        )

    if column_exists(inspector, "llm_models", "cost_per_output_unit"):
        op.alter_column(
            "llm_models",
            "cost_per_output_unit",
            new_column_name="cost_per_output_token",
        )
