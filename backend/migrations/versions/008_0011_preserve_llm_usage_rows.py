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

Revision ID: 008_0011
Revises: 008_0010
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, column_exists

# revision identifiers, used by Alembic.
revision = "008_0011"
down_revision = "008_0010"
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

    # 3. Replace the provider_id FK. Inspect the current FK on
    # provider_id → llm_providers (there is exactly one; we know the name
    # is llm_usage_provider_id_fkey but we look it up so a re-run of this
    # migration is a no-op when the target state is already in place).
    existing_fk = next(
        (
            fk
            for fk in inspector.get_foreign_keys("llm_usage")
            if fk.get("referred_table") == "llm_providers"
            and "provider_id" in (fk.get("constrained_columns") or [])
        ),
        None,
    )
    current_ondelete = (existing_fk.get("options", {}).get("ondelete") or "").upper() if existing_fk else None

    if current_ondelete != "SET NULL":
        existing_fk_name = existing_fk.get("name") if existing_fk is not None else None
        if existing_fk_name:
            op.drop_constraint(existing_fk_name, "llm_usage", type_="foreignkey")
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

    # Downgrade target: provider_id NOT NULL, FK with ON DELETE CASCADE.
    # Rows with NULL provider_id would violate the NOT NULL constraint —
    # leave them untouched and let the ALTER fail loud if any exist (there
    # is no safe way to reattach them to a deleted provider).
    existing_fk = next(
        (
            fk
            for fk in inspector.get_foreign_keys("llm_usage")
            if fk.get("referred_table") == "llm_providers"
            and "provider_id" in (fk.get("constrained_columns") or [])
        ),
        None,
    )
    current_ondelete = (existing_fk.get("options", {}).get("ondelete") or "").upper() if existing_fk else None

    if current_ondelete != "CASCADE":
        existing_fk_name = existing_fk.get("name") if existing_fk is not None else None
        if existing_fk_name:
            op.drop_constraint(existing_fk_name, "llm_usage", type_="foreignkey")
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
