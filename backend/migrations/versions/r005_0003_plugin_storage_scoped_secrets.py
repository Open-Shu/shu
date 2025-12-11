"""Add scope to plugin_storage and migrate legacy plugin_secret rows.

Revision ID: r005_0003
Revises: r005_0002
Create Date: 2025-12-09

Adds a scope column to plugin_storage to support user and system scoped
plugin data (initially secrets) and migrates legacy AgentMemory
plugin_secret rows into plugin_storage with namespace='secret'.
"""

from alembic import op
import sqlalchemy as sa


revision = "r005_0003"
down_revision = "r005_0002"
branch_labels = None
depends_on = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column)"
        ),
        {"table": table_name, "column": column_name}
    )
    return result.scalar()


def _index_exists(conn, index_name: str) -> bool:
    """Check if an index exists."""
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT FROM pg_indexes WHERE indexname = :name)"),
        {"name": index_name}
    )
    return result.scalar()


def upgrade() -> None:
    connection = op.get_bind()

    # 1) Add scope column with default 'user' so existing rows are user scoped.
    if not _column_exists(connection, "plugin_storage", "scope"):
        op.add_column(
            "plugin_storage",
            sa.Column(
                "scope",
                sa.String(length=10),
                nullable=False,
                server_default="user",
            ),
        )

    # 2) Recreate composite index/unique to include scope.
    # Check if the old index structure exists (without scope) before dropping
    # We detect this by checking if the system scope index exists (added after the old ones are dropped)
    if not _index_exists(connection, "uq_plugin_storage_system_scope_key"):
        # Drop existing lookup index and unique constraint from r005_0002.
        try:
            op.drop_index("ix_plugin_storage_lookup", table_name="plugin_storage")
        except Exception:
            pass  # Index may not exist

        try:
            op.drop_constraint(
                "uq_plugin_storage_scope_key",
                "plugin_storage",
                type_="unique",
            )
        except Exception:
            pass  # Constraint may not exist

        # Recreate with scope as part of the key for user-scoped lookups.
        if not _index_exists(connection, "ix_plugin_storage_lookup"):
            op.create_index(
                "ix_plugin_storage_lookup",
                "plugin_storage",
                ["scope", "user_id", "plugin_name", "namespace", "key"],
            )

        # Check if constraint exists before creating
        constraint_check = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM pg_constraint WHERE conname = 'uq_plugin_storage_scope_key')"
            )
        )
        if not constraint_check.scalar():
            op.create_unique_constraint(
                "uq_plugin_storage_scope_key",
                "plugin_storage",
                ["scope", "user_id", "plugin_name", "namespace", "key"],
            )

        # 3) Ensure system-scoped rows are unique per plugin/namespace/key.
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_plugin_storage_system_scope_key
                ON plugin_storage (plugin_name, namespace, key)
                WHERE scope = 'system'
                """
            )
        )

    # 4) Migrate legacy AgentMemory plugin_secret rows into plugin_storage.
    # These were previously written by the plugin_secrets service using
    # agent_key = f"plugin_secret:{name}".
    # Use ON CONFLICT DO NOTHING for idempotency.

    connection.execute(
        sa.text(
            """
            INSERT INTO plugin_storage (
                id,
                user_id,
                plugin_name,
                namespace,
                key,
                value,
                created_at,
                updated_at,
                scope
            )
            SELECT
                id,
                user_id,
                SUBSTRING(agent_key FROM 15) AS plugin_name,
                'secret' AS namespace,
                key,
                value,
                created_at,
                updated_at,
                'user' AS scope
            FROM agent_memory
            WHERE agent_key LIKE 'plugin_secret:%'
            ON CONFLICT DO NOTHING
            """
        )
    )

    # Delete migrated rows from agent_memory to avoid duplication.
    connection.execute(
        sa.text(
            """
            DELETE FROM agent_memory
            WHERE agent_key LIKE 'plugin_secret:%'
            """
        )
    )


def downgrade() -> None:
    # 1) Migrate namespace='secret' rows back into AgentMemory using the
    # original plugin_secret scope key convention. Both user and system
    # scoped rows are mapped back; scope information is lost because the
    # previous schema had no equivalent concept, but values are preserved.
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO agent_memory (
                id,
                user_id,
                agent_key,
                key,
                value,
                created_at,
                updated_at
            )
            SELECT
                id,
                user_id,
                'plugin_secret:' || plugin_name AS agent_key,
                key,
                value,
                created_at,
                updated_at
            FROM plugin_storage
            WHERE namespace = 'secret'
            """
        )
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM plugin_storage
            WHERE namespace = 'secret'
            """
        )
    )

    # 2) Drop system-scope partial unique index and restore original unique/index.
    op.drop_index("uq_plugin_storage_system_scope_key", table_name="plugin_storage")

    op.drop_constraint(
        "uq_plugin_storage_scope_key",
        "plugin_storage",
        type_="unique",
    )
    op.drop_index("ix_plugin_storage_lookup", table_name="plugin_storage")

    op.create_index(
        "ix_plugin_storage_lookup",
        "plugin_storage",
        ["user_id", "plugin_name", "namespace", "key"],
    )
    op.create_unique_constraint(
        "uq_plugin_storage_scope_key",
        "plugin_storage",
        ["user_id", "plugin_name", "namespace", "key"],
    )

    # 3) Drop scope column.
    op.drop_column("plugin_storage", "scope")
