"""Add plugin_storage table and migrate data from agent_memory.

Revision ID: r005_0002
Revises: r005_0001
Create Date: 2025-12-08

Separates plugin runtime state (cursors, secrets, storage) from agent memory.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "r005_0002"
down_revision = "r005_0001"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"),
        {"name": table_name}
    )
    return result.scalar()


def upgrade() -> None:
    connection = op.get_bind()

    # Check if table already exists (idempotent for partial runs)
    if _table_exists(connection, "plugin_storage"):
        # Table already exists, skip DDL
        pass
    else:
        # Create plugin_storage table
        op.create_table(
            "plugin_storage",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("plugin_name", sa.String(100), nullable=False),
            sa.Column("namespace", sa.String(50), nullable=False),
            sa.Column("key", sa.String(200), nullable=False),
            sa.Column("value", sa.JSON, nullable=True),
            sa.Column(
                "created_at",
                TIMESTAMP(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                TIMESTAMP(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
        )

        # Create indexes
        op.create_index("ix_plugin_storage_user_id", "plugin_storage", ["user_id"])
        op.create_index("ix_plugin_storage_plugin_name", "plugin_storage", ["plugin_name"])
        op.create_index("ix_plugin_storage_namespace", "plugin_storage", ["namespace"])
        op.create_index("ix_plugin_storage_key", "plugin_storage", ["key"])
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

    # Migrate data from agent_memory (only if source rows exist and not already migrated)
    # Use INSERT ... ON CONFLICT DO NOTHING for idempotency

    # Migrate tool_storage entries
    # Cursor entries have key LIKE 'cursor:%' - extract to namespace='cursor'
    # Other entries get namespace='storage'
    connection.execute(sa.text("""
        INSERT INTO plugin_storage (id, user_id, plugin_name, namespace, key, value, created_at, updated_at)
        SELECT
            id,
            user_id,
            SUBSTRING(agent_key FROM 14) as plugin_name,
            CASE WHEN key LIKE 'cursor:%' THEN 'cursor' ELSE 'storage' END as namespace,
            CASE WHEN key LIKE 'cursor:%' THEN SUBSTRING(key FROM 8) ELSE key END as key,
            value,
            created_at,
            updated_at
        FROM agent_memory
        WHERE agent_key LIKE 'tool_storage:%'
        ON CONFLICT DO NOTHING
    """))

    # Migrate tool_secret entries (namespace='secret')
    connection.execute(sa.text("""
        INSERT INTO plugin_storage (id, user_id, plugin_name, namespace, key, value, created_at, updated_at)
        SELECT
            id,
            user_id,
            SUBSTRING(agent_key FROM 13) as plugin_name,
            'secret' as namespace,
            key,
            value,
            created_at,
            updated_at
        FROM agent_memory
        WHERE agent_key LIKE 'tool_secret:%'
        ON CONFLICT DO NOTHING
    """))

    # Delete migrated rows from agent_memory
    connection.execute(sa.text("""
        DELETE FROM agent_memory
        WHERE agent_key LIKE 'tool_storage:%' OR agent_key LIKE 'tool_secret:%'
    """))


def downgrade() -> None:
    # Migrate data back to agent_memory before dropping the table
    connection = op.get_bind()

    # Migrate storage entries back (including cursor namespace -> tool_storage with cursor: prefix)
    connection.execute(sa.text("""
        INSERT INTO agent_memory (id, user_id, agent_key, key, value, created_at, updated_at)
        SELECT
            id,
            user_id,
            'tool_storage:' || plugin_name as agent_key,
            CASE WHEN namespace = 'cursor' THEN 'cursor:' || key ELSE key END as key,
            value,
            created_at,
            updated_at
        FROM plugin_storage
        WHERE namespace IN ('storage', 'cursor')
    """))

    # Migrate secret entries back
    connection.execute(sa.text("""
        INSERT INTO agent_memory (id, user_id, agent_key, key, value, created_at, updated_at)
        SELECT
            id,
            user_id,
            'tool_secret:' || plugin_name as agent_key,
            key,
            value,
            created_at,
            updated_at
        FROM plugin_storage
        WHERE namespace = 'secret'
    """))

    # Drop the table
    op.drop_table("plugin_storage")

