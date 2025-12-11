"""Migration 003: Third Release Squash (r003_0001..r003_0003)

This migration condenses the third release migrations into a single step.

We remove unused conversation columns, and add metadata cols to the table, along with one for an indexed summary.
Finally, we also insert a system setting field that contains the current side call model information.

Replaces: r003_0001_remove_conversation_provider_prompt,
          r003_0002_add_side_call_configuration,
          r003_0003_conversation_summary
"""

import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None
replaces = ("r003_0001", "r003_0002", "r003_0003")


def upgrade() -> None:
    bind = op.get_bind()

    # Remove legacy provider/model columns from conversations.
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("provider_id")
        batch_op.drop_column("system_prompt")
        batch_op.drop_column("model_id")

    # Add conversation metadata and summary fields.
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("meta", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("summary_text", sa.Text(), nullable=True))

    # Ensure trigram support exists before creating the summary index.
    has_trgm = False
    try:
        res = bind.execute(sa.text("SELECT true FROM pg_extension WHERE extname='pg_trgm'"))
        has_trgm = bool(res.scalar())
    except Exception:
        has_trgm = False

    if not has_trgm:
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            res = bind.execute(sa.text("SELECT true FROM pg_extension WHERE extname='pg_trgm'"))
            has_trgm = bool(res.scalar())
        except Exception:
            has_trgm = False

    if has_trgm:
        op.create_index(
            "ix_conversations_summary_text_trgm",
            "conversations",
            ["summary_text"],
            postgresql_using="gin",
            postgresql_ops={"summary_text": "gin_trgm_ops"},
        )
    else:
        print("Skipping trigram index on conversations.summary_text (pg_trgm not available)")

    # Seed the side-call model configuration system setting if missing.
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM system_settings WHERE key = 'side_call_model_config_id' LIMIT 1"
        )
    ).scalar()

    if not result:
        bind.execute(
            sa.text(
                """
                INSERT INTO system_settings (key, value, created_at, updated_at)
                VALUES (
                    'side_call_model_config_id',
                    :value,
                    NOW(),
                    NOW()
                )
                """
            ),
            {"value": json.dumps({})},
        )
        print("Added side_call_model_config_id system setting")


def downgrade() -> None:
    try:
        op.drop_index(
            "ix_conversations_summary_text_trgm",
            table_name="conversations",
        )
    except Exception:
        pass

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("summary_text")
        batch_op.drop_column("meta")

    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM system_settings WHERE key = 'side_call_model_config_id'")
    )
    print("Removed side_call_model_config_id system setting")

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("system_prompt", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("provider_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("model_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_conversations_provider_id",
            "llm_providers",
            ["provider_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_conversations_model_id",
            "llm_models",
            ["model_id"],
            ["id"],
            ondelete="SET NULL",
        )
