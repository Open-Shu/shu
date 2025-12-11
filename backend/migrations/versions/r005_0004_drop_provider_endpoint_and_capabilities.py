"""Remove deprecated provider endpoint/capability columns.

Revision ID: r005_0004
Revises: r005_0003
Create Date: 2025-12-12
"""

import json

from alembic import op
import sqlalchemy as sa

from migrations.seed_data.llm_provider_types import upsert_llm_provider_type_definitions


revision = "r005_0004"
down_revision = "r005_0003"
branch_labels = None
depends_on = None


LEGACY_PROVIDER_TYPE_MAPPING = {
    # old_provider_type -> new_provider_type
    "openai-responses": "openai",
    "openai-compatible": "generic_completions",
    "azure": "openai",
    "grok": "xai",
}


def upgrade() -> None:
    conn = op.get_bind()

    # Migrate providers using deprecated provider types to their modern equivalents
    for old_type, new_type in LEGACY_PROVIDER_TYPE_MAPPING.items():
        conn.execute(
            sa.text("UPDATE llm_providers SET provider_type = :new_type WHERE provider_type = :old_type"),
            {"old_type": old_type, "new_type": new_type}
        )

    # Remove deprecated provider type definitions that are no longer used
    conn.execute(
        sa.text("DELETE FROM llm_provider_type_definitions WHERE key = ANY(:keys)"),
        {"keys": list(LEGACY_PROVIDER_TYPE_MAPPING.keys())}
    )

    # Check which deprecated columns still exist (idempotent for partial runs)
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("llm_providers")}
    deprecated_columns = {"api_endpoint", "supports_streaming", "supports_functions", "supports_vision"}
    columns_to_migrate = deprecated_columns & existing_columns

    if columns_to_migrate:
        # Build dynamic SELECT with only existing columns
        select_cols = ["id", "config"] + sorted(columns_to_migrate)
        result = conn.execute(sa.text(f"SELECT {', '.join(select_cols)} FROM llm_providers"))

        for row in result:
            row_dict = dict(zip(select_cols, row))
            provider_id = row_dict["id"]
            existing_config = row_dict["config"]

            # Parse existing config or start fresh
            if existing_config:
                if isinstance(existing_config, str):
                    config = json.loads(existing_config)
                else:
                    config = dict(existing_config)
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

            # Only set if not already present in config
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

            # Update the provider's config
            conn.execute(
                sa.text("UPDATE llm_providers SET config = :config WHERE id = :id"),
                {"config": json.dumps(config), "id": provider_id}
            )

        # Drop only the columns that still exist
        for column in columns_to_migrate:
            op.drop_column("llm_providers", column)

    upsert_llm_provider_type_definitions(op)


def downgrade() -> None:
    # Add columns back
    op.add_column("llm_providers", sa.Column("supports_vision", sa.Boolean(), nullable=True))
    op.add_column("llm_providers", sa.Column("supports_functions", sa.Boolean(), nullable=True))
    op.add_column("llm_providers", sa.Column("supports_streaming", sa.Boolean(), nullable=True))
    op.add_column("llm_providers", sa.Column("api_endpoint", sa.Text(), nullable=True))

    # Migrate data back from config JSON to columns
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT id, config FROM llm_providers"))

    for row in result:
        provider_id = row[0]
        existing_config = row[1]

        if not existing_config:
            continue

        if isinstance(existing_config, str):
            config = json.loads(existing_config)
        else:
            config = dict(existing_config)

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
