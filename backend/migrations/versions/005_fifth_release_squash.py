"""Migration 005: Fifth Release Squash (r005_0001..r005_0004)

This migration condenses the fifth release development migrations into one.

Changes:
- Simplifies llm_provider_type_definitions: adds provider_adapter_name, drops
  legacy data-driven columns (base_url_template, endpoints, auth, etc.)
- Creates plugin_storage table for plugin runtime state (cursors, secrets, storage)
- Adds scope column to plugin_storage for user/system scoped data
- Migrates legacy provider types to modern equivalents
- Drops deprecated llm_providers columns (api_endpoint, supports_*)

Replaces: r005_0001_provider_type_minimal_columns,
          r005_0002_plugin_storage_table,
          r005_0003_plugin_storage_scoped_secrets,
          r005_0004_drop_provider_endpoint_and_capabilities
"""

import json
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None
replaces = ("r005_0001", "r005_0002", "r005_0003", "r005_0004")


# Provider type migrations from r005_0004
LEGACY_PROVIDER_TYPE_MAPPING = {
    "openai-responses": "openai",
    "openai-compatible": "generic_completions",
    "azure": "openai",
    "grok": "xai",
}


def _column_exists(inspector: Any, table_name: str, column_name: str) -> bool:
    try:
        return any(col["name"] == column_name for col in inspector.get_columns(table_name))
    except Exception:
        return False


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"),
        {"name": table_name}
    )
    return result.scalar()


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT FROM pg_indexes WHERE indexname = :name)"),
        {"name": index_name}
    )
    return result.scalar()


def _drop_column_if_exists(bind, table: str, column: str) -> None:
    inspector = sa.inspect(bind)
    if _column_exists(inspector, table, column):
        op.drop_column(table, column)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: llm_provider_type_definitions changes (from r005_0001)
    # ========================================================================
    if not _column_exists(inspector, "llm_provider_type_definitions", "provider_adapter_name"):
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

    for col in ("base_url_template", "endpoints", "auth", "parameter_mapping", "streaming", "notes"):
        _drop_column_if_exists(conn, "llm_provider_type_definitions", col)

    # ========================================================================
    # Part 2: Create plugin_storage table (from r005_0002 + r005_0003)
    # Net schema includes scope column from the start
    # Must be idempotent to handle stamped databases from intermediate states
    # ========================================================================
    if not _table_exists(conn, "plugin_storage"):
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
            sa.Column("scope", sa.String(10), nullable=False, server_default="user"),
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

        # Create indexes (final state from r005_0003)
        op.create_index("ix_plugin_storage_user_id", "plugin_storage", ["user_id"])
        op.create_index("ix_plugin_storage_plugin_name", "plugin_storage", ["plugin_name"])
        op.create_index("ix_plugin_storage_namespace", "plugin_storage", ["namespace"])
        op.create_index("ix_plugin_storage_key", "plugin_storage", ["key"])
        op.create_index(
            "ix_plugin_storage_lookup",
            "plugin_storage",
            ["scope", "user_id", "plugin_name", "namespace", "key"],
        )
        op.create_unique_constraint(
            "uq_plugin_storage_scope_key",
            "plugin_storage",
            ["scope", "user_id", "plugin_name", "namespace", "key"],
        )
        # Partial unique index for system-scoped entries
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_plugin_storage_system_scope_key
                ON plugin_storage (plugin_name, namespace, key)
                WHERE scope = 'system'
                """
            )
        )
    else:
        # Table exists - handle case where DB was stamped from r005_0002 (before scope column)
        # or a partial run that added scope but didn't complete index recreation.
        # Use uq_plugin_storage_system_scope_key as the completion marker since it's created last.
        if not _index_exists(conn, "uq_plugin_storage_system_scope_key"):
            # Refresh inspector to get current plugin_storage columns
            inspector = sa.inspect(conn)

            # Drop old indexes/constraints that don't include scope (may or may not exist)
            if _index_exists(conn, "ix_plugin_storage_lookup"):
                op.drop_index("ix_plugin_storage_lookup", table_name="plugin_storage")
            conn.execute(sa.text(
                "ALTER TABLE plugin_storage DROP CONSTRAINT IF EXISTS uq_plugin_storage_scope_key"
            ))

            # Add scope column if missing (r005_0002 state)
            if not _column_exists(inspector, "plugin_storage", "scope"):
                op.add_column(
                    "plugin_storage",
                    sa.Column("scope", sa.String(10), nullable=False, server_default="user"),
                )

            # Recreate indexes/constraints with scope
            op.create_index(
                "ix_plugin_storage_lookup",
                "plugin_storage",
                ["scope", "user_id", "plugin_name", "namespace", "key"],
            )
            op.create_unique_constraint(
                "uq_plugin_storage_scope_key",
                "plugin_storage",
                ["scope", "user_id", "plugin_name", "namespace", "key"],
            )
            # Partial unique index for system-scoped entries (completion marker)
            op.execute(
                sa.text(
                    """
                    CREATE UNIQUE INDEX uq_plugin_storage_system_scope_key
                    ON plugin_storage (plugin_name, namespace, key)
                    WHERE scope = 'system'
                    """
                )
            )

    # ========================================================================
    # Part 3: Migrate data from agent_memory to plugin_storage
    # Combined from r005_0002 and r005_0003
    # ========================================================================
    # Migrate tool_storage entries (cursor and storage namespaces)
    conn.execute(sa.text("""
        INSERT INTO plugin_storage (id, user_id, plugin_name, namespace, key, value, created_at, updated_at, scope)
        SELECT
            id,
            user_id,
            SUBSTRING(agent_key FROM 14) as plugin_name,
            CASE WHEN key LIKE 'cursor:%' THEN 'cursor' ELSE 'storage' END as namespace,
            CASE WHEN key LIKE 'cursor:%' THEN SUBSTRING(key FROM 8) ELSE key END as key,
            value,
            created_at,
            updated_at,
            'user' as scope
        FROM agent_memory
        WHERE agent_key LIKE 'tool_storage:%'
        ON CONFLICT DO NOTHING
    """))

    # Migrate tool_secret entries (namespace='secret')
    conn.execute(sa.text("""
        INSERT INTO plugin_storage (id, user_id, plugin_name, namespace, key, value, created_at, updated_at, scope)
        SELECT
            id,
            user_id,
            SUBSTRING(agent_key FROM 13) as plugin_name,
            'secret' as namespace,
            key,
            value,
            created_at,
            updated_at,
            'user' as scope
        FROM agent_memory
        WHERE agent_key LIKE 'tool_secret:%'
        ON CONFLICT DO NOTHING
    """))

    # Migrate legacy plugin_secret entries (from r005_0003)
    conn.execute(sa.text("""
        INSERT INTO plugin_storage (id, user_id, plugin_name, namespace, key, value, created_at, updated_at, scope)
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
    """))

    # Delete migrated rows from agent_memory
    conn.execute(sa.text("""
        DELETE FROM agent_memory
        WHERE agent_key LIKE 'tool_storage:%'
           OR agent_key LIKE 'tool_secret:%'
           OR agent_key LIKE 'plugin_secret:%'
    """))

    # ========================================================================
    # Part 4: Migrate deprecated provider types and drop columns (from r005_0004)
    # ========================================================================
    # Migrate providers using deprecated provider types to their modern equivalents
    for old_type, new_type in LEGACY_PROVIDER_TYPE_MAPPING.items():
        conn.execute(
            sa.text("UPDATE llm_providers SET provider_type = :new_type WHERE provider_type = :old_type"),
            {"old_type": old_type, "new_type": new_type}
        )

    # Remove deprecated provider type definitions
    conn.execute(
        sa.text("DELETE FROM llm_provider_type_definitions WHERE key = ANY(:keys)"),
        {"keys": list(LEGACY_PROVIDER_TYPE_MAPPING.keys())}
    )

    # Migrate deprecated columns to config JSON before dropping
    # Refresh inspector to get current llm_providers columns
    inspector = sa.inspect(conn)
    deprecated_columns = {"api_endpoint", "supports_streaming", "supports_functions", "supports_vision"}
    existing_columns = {col["name"] for col in inspector.get_columns("llm_providers")}
    columns_to_migrate = deprecated_columns & existing_columns

    if columns_to_migrate:
        select_cols = ["id", "config"] + sorted(columns_to_migrate)
        result = conn.execute(sa.text(f"SELECT {', '.join(select_cols)} FROM llm_providers"))

        for row in result:
            row_dict = dict(zip(select_cols, row))
            provider_id = row_dict["id"]
            existing_config = row_dict["config"]

            if existing_config:
                config = json.loads(existing_config) if isinstance(existing_config, str) else dict(existing_config)
            else:
                config = {}

            # Migrate api_endpoint to config
            api_endpoint = row_dict.get("api_endpoint")
            if api_endpoint and not config.get("get_api_base_url"):
                config["get_api_base_url"] = api_endpoint

            # Migrate capabilities to config
            capabilities = config.get("get_capabilities", {})
            if not isinstance(capabilities, dict):
                capabilities = {}

            supports_streaming = row_dict.get("supports_streaming")
            supports_functions = row_dict.get("supports_functions")
            supports_vision = row_dict.get("supports_vision")

            if "streaming" not in capabilities and supports_streaming is not None:
                capabilities["streaming"] = {"value": bool(supports_streaming), "label": "Supports Streaming"}
            if "tools" not in capabilities and supports_functions is not None:
                capabilities["tools"] = {"value": bool(supports_functions), "label": "Supports Tool Calling"}
            if "vision" not in capabilities and supports_vision is not None:
                capabilities["vision"] = {"value": bool(supports_vision), "label": "Supports Vision"}

            if capabilities:
                config["get_capabilities"] = capabilities

            conn.execute(
                sa.text("UPDATE llm_providers SET config = :config WHERE id = :id"),
                {"config": json.dumps(config), "id": provider_id}
            )

        for column in columns_to_migrate:
            op.drop_column("llm_providers", column)

    # Refresh provider type seed data
    upsert_llm_provider_type_definitions(op)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 4 reverse: Restore deprecated llm_providers columns
    # ========================================================================
    deprecated_columns = [
        ("supports_vision", sa.Boolean()),
        ("supports_functions", sa.Boolean()),
        ("supports_streaming", sa.Boolean()),
        ("api_endpoint", sa.Text()),
    ]
    for col_name, col_type in deprecated_columns:
        if not _column_exists(inspector, "llm_providers", col_name):
            op.add_column("llm_providers", sa.Column(col_name, col_type, nullable=True))

    # Migrate data back from config JSON to columns
    result = conn.execute(sa.text("SELECT id, config FROM llm_providers"))

    for row in result:
        provider_id = row[0]
        existing_config = row[1]

        if not existing_config:
            continue

        config = json.loads(existing_config) if isinstance(existing_config, str) else dict(existing_config)
        api_endpoint = config.get("get_api_base_url")
        capabilities = config.get("get_capabilities", {})

        supports_streaming = capabilities.get("streaming", {}).get("value")
        supports_functions = capabilities.get("tools", {}).get("value")
        supports_vision = capabilities.get("vision", {}).get("value")

        conn.execute(
            sa.text("""
                UPDATE llm_providers
                SET api_endpoint = :api_endpoint,
                    supports_streaming = :supports_streaming,
                    supports_functions = :supports_functions,
                    supports_vision = :supports_vision
                WHERE id = :id
            """),
            {
                "id": provider_id,
                "api_endpoint": api_endpoint,
                "supports_streaming": supports_streaming,
                "supports_functions": supports_functions,
                "supports_vision": supports_vision,
            }
        )

    # ========================================================================
    # Part 3 reverse: Migrate data back to agent_memory
    # ========================================================================
    # Migrate storage and cursor entries back (ON CONFLICT for idempotency)
    conn.execute(sa.text("""
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
        ON CONFLICT (id) DO NOTHING
    """))

    # Migrate secret entries back (both tool_secret and plugin_secret patterns)
    conn.execute(sa.text("""
        INSERT INTO agent_memory (id, user_id, agent_key, key, value, created_at, updated_at)
        SELECT
            id,
            user_id,
            'plugin_secret:' || plugin_name as agent_key,
            key,
            value,
            created_at,
            updated_at
        FROM plugin_storage
        WHERE namespace = 'secret'
        ON CONFLICT (id) DO NOTHING
    """))

    # ========================================================================
    # Part 2 reverse: Drop plugin_storage table
    # ========================================================================
    if _table_exists(conn, "plugin_storage"):
        op.drop_table("plugin_storage")

    # ========================================================================
    # Part 1 reverse: Restore llm_provider_type_definitions columns
    # ========================================================================
    # Refresh inspector for llm_provider_type_definitions checks
    inspector = sa.inspect(conn)
    for col, coltype in (
        ("base_url_template", sa.Text()),
        ("endpoints", sa.JSON()),
        ("auth", sa.JSON()),
        ("parameter_mapping", sa.JSON()),
        ("streaming", sa.String(length=50)),
        ("notes", sa.JSON()),
    ):
        if not _column_exists(inspector, "llm_provider_type_definitions", col):
            op.add_column(
                "llm_provider_type_definitions",
                sa.Column(col, coltype, nullable=True),
            )

    _drop_column_if_exists(conn, "llm_provider_type_definitions", "provider_adapter_name")
