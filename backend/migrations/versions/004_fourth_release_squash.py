"""Migration 004: Fourth Release Squash (r004_0001..r004_0003)

This migration condenses the fourth release development migrations into one.

It drops legacy llm model token columns, removes the cross-session memory flag,
and removes the legacy source_types table and related foreign keys while
refreshing provider type seed data.

Replaces: r004_0001_fix_openai_provider_parameters,
          r004_0002_drop_cross_session_memory_flag,
          r004_0003_drop_source_types_table
"""

from typing import Any

from alembic import op
import sqlalchemy as sa

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None
replaces = ("r004_0001", "r004_0002", "r004_0003")


def _column_exists(inspector: Any, table_name: str, column_name: str) -> bool:
    try:
        return any(col["name"] == column_name for col in inspector.get_columns(table_name))
    except Exception:
        return False


def _drop_source_types_fks(bind) -> None:
    """Drop any foreign key constraints that reference source_types."""
    inspector = sa.inspect(bind)
    for table_name in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table_name):
            if fk.get("referred_table") == "source_types":
                constraint_name = fk.get("name")
                if constraint_name:
                    op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _recreate_source_types_table(bind) -> None:
    """Best-effort recreation of legacy source_types table for downgrade paths."""
    inspector = sa.inspect(bind)
    if inspector.has_table("source_types"):
        return

    op.create_table(
        "source_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("configuration_schema", sa.JSON(), nullable=True),
        sa.Column("default_config", sa.JSON(), nullable=True),
        sa.Column("requires_authentication", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_sync", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_webhooks", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("supported_file_types", sa.JSON(), nullable=True),
        sa.Column("max_file_size", sa.String(20), nullable=True),
        sa.Column("supports_incremental_sync", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_deletion_detection", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_metadata_extraction", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def _recreate_source_types_fks(bind) -> None:
    """Recreate foreign keys from *source_type columns back to source_types."""
    inspector = sa.inspect(bind)

    for table_name in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "source_type" not in columns:
            continue

        existing_fks = inspector.get_foreign_keys(table_name)
        if any(fk.get("referred_table") == "source_types" for fk in existing_fks):
            continue

        op.create_foreign_key(
            f"{table_name}_source_type_fkey",
            source_table=table_name,
            referent_table="source_types",
            local_cols=["source_type"],
            remote_cols=["name"],
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop legacy llm_models token columns if present.
    with op.batch_alter_table("llm_models") as batch_op:
        if _column_exists(inspector, "llm_models", "context_window"):
            batch_op.drop_column("context_window")
        if _column_exists(inspector, "llm_models", "max_output_tokens"):
            batch_op.drop_column("max_output_tokens")

    # Refresh provider type seed data to align with updated defaults.
    upsert_llm_provider_type_definitions(op)

    # Remove cross-session memory flag from user_preferences if present.
    with op.batch_alter_table("user_preferences") as batch_op:
        if _column_exists(inspector, "user_preferences", "enable_cross_session_memory_by_default"):
            batch_op.drop_column("enable_cross_session_memory_by_default")

    # Drop foreign keys and the legacy source_types table.
    _drop_source_types_fks(bind)

    if inspector.has_table("source_types"):
        op.drop_table("source_types")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Recreate legacy source_types table and foreign keys.
    _recreate_source_types_table(bind)
    _recreate_source_types_fks(bind)

    with op.batch_alter_table("user_preferences") as batch_op:
        if not _column_exists(inspector, "user_preferences", "enable_cross_session_memory_by_default"):
            batch_op.add_column(
                sa.Column(
                    "enable_cross_session_memory_by_default",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )

    with op.batch_alter_table("llm_models") as batch_op:
        if not _column_exists(inspector, "llm_models", "context_window"):
            batch_op.add_column(
                sa.Column(
                    "context_window",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("4000"),
                )
            )
        if not _column_exists(inspector, "llm_models", "max_output_tokens"):
            batch_op.add_column(
                sa.Column(
                    "max_output_tokens",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("4000"),
                )
            )
