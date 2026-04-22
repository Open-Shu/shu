"""Preserve llm_usage rows when providers or models are deleted.

Two schema changes and a backfill, all in one migration to keep the
table consistent at every intermediate state:

1. Change the llm_usage.provider_id FK from ON DELETE CASCADE to
   ON DELETE SET NULL, matching the existing model_id FK behaviour.
   Deleting a provider will no longer wipe its billing/audit history.
2. Make llm_usage.provider_id nullable so SET NULL is schema-legal.
3. Add provider_name and model_name snapshot columns and backfill them
   from the current FK targets. The columns are populated by the write
   path at INSERT time and never updated on source mutation — they are
   point-in-time audit context that survives FK deletion.

Idempotent — safe to re-run.

Revision ID: 008_0010
Revises: 008_0009
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, column_exists

# revision identifiers, used by Alembic.
revision = "008_0010"
down_revision = "008_0009"
branch_labels = None
depends_on = None


FK_NAME = "llm_usage_provider_id_fkey"


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Add snapshot columns (nullable — backfill populates existing rows).
    add_column_if_not_exists(
        inspector,
        "llm_usage",
        sa.Column("provider_name", sa.String(length=255), nullable=True),
    )
    add_column_if_not_exists(
        inspector,
        "llm_usage",
        sa.Column("model_name", sa.String(length=255), nullable=True),
    )

    # 2. Backfill — safe to re-run because existing NULL rows stay NULL if
    # the FK target has already been deleted. Overwrites only when the
    # snapshot column is currently NULL to stay idempotent.
    op.execute(
        """
        UPDATE llm_usage u
           SET provider_name = p.name
          FROM llm_providers p
         WHERE u.provider_id = p.id
           AND u.provider_name IS NULL
        """
    )
    op.execute(
        """
        UPDATE llm_usage u
           SET model_name = m.model_name
          FROM llm_models m
         WHERE u.model_id = m.id
           AND u.model_name IS NULL
        """
    )

    # 3. Drop-and-recreate the provider_id FK with ON DELETE SET NULL.
    # The constraint may not exist under its standard name on some DBs
    # (e.g. if Alembic generated a different name historically), so we
    # check via the inspector and skip gracefully.
    fk_names = {fk["name"] for fk in inspector.get_foreign_keys("llm_usage")}
    if FK_NAME in fk_names:
        op.drop_constraint(FK_NAME, "llm_usage", type_="foreignkey")

    # 4. Make provider_id nullable so SET NULL is schema-legal.
    if column_exists(inspector, "llm_usage", "provider_id"):
        op.alter_column("llm_usage", "provider_id", existing_type=sa.String(), nullable=True)

    op.create_foreign_key(
        FK_NAME,
        "llm_usage",
        "llm_providers",
        ["provider_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Reverse FK first — downgrading to CASCADE requires provider_id to be
    # NOT NULL again. Any rows with NULL provider_id would violate the
    # old constraint; leave them untouched and let the downgrade fail loud
    # if they exist (there is no safe way to reattach them).
    fk_names = {fk["name"] for fk in inspector.get_foreign_keys("llm_usage")}
    if FK_NAME in fk_names:
        op.drop_constraint(FK_NAME, "llm_usage", type_="foreignkey")

    if column_exists(inspector, "llm_usage", "provider_id"):
        op.alter_column("llm_usage", "provider_id", existing_type=sa.String(), nullable=False)

    op.create_foreign_key(
        FK_NAME,
        "llm_usage",
        "llm_providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )

    if column_exists(inspector, "llm_usage", "model_name"):
        op.drop_column("llm_usage", "model_name")
    if column_exists(inspector, "llm_usage", "provider_name"):
        op.drop_column("llm_usage", "provider_name")
