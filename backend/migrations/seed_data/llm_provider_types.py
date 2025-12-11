"""
Shared defaults and upsert helper for llm_provider_type_definitions.

Usage (inside an Alembic upgrade function):

    from ._seed_llm_provider_types import upsert_llm_provider_type_definitions
    upsert_llm_provider_type_definitions(op)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

import sqlalchemy as sa

PROVIDER_TYPE_DEFINITION_DEFAULTS: Iterable[Mapping[str, object]] = (
    {"key": "openai", "display_name": "OpenAI", "provider_adapter_name": "openai", "is_active": True},
    {"key": "anthropic", "display_name": "Anthropic", "provider_adapter_name": "anthropic", "is_active": True},
    {"key": "lm_studio", "display_name": "LM Studio", "provider_adapter_name": "lm_studio", "is_active": True},
    {"key": "ollama", "display_name": "Ollama", "provider_adapter_name": "ollama", "is_active": True},
    {"key": "xai", "display_name": "xAI", "provider_adapter_name": "xai", "is_active": True},
    {"key": "perplexity", "display_name": "Perplexity", "provider_adapter_name": "perplexity", "is_active": True},
    {"key": "gemini", "display_name": "Gemini", "provider_adapter_name": "gemini", "is_active": True},
    {"key": "generic_completions", "display_name": "Generic Completions", "provider_adapter_name": "generic_completions", "is_active": True},
)


def upsert_llm_provider_type_definitions(
    op,
    *,
    definitions: Optional[Iterable[Mapping[str, object]]] = None,
) -> None:
    """Upsert the canonical provider type definitions (minimal metadata)."""

    try:
        rows = list(definitions or PROVIDER_TYPE_DEFINITION_DEFAULTS)
        if not rows:
            return

        bind = op.get_bind()
        now = datetime.now(timezone.utc)

        existing_ids = dict(
            bind.execute(sa.text("SELECT key, id FROM llm_provider_type_definitions")).fetchall()
        )

        values_sql = []
        params = {}
        for idx, row in enumerate(rows):
            key = row["key"]
            prefix = f"p{idx}"
            record_id = existing_ids.get(key) or str(uuid.uuid4())

            values_sql.append(
                f"(:{prefix}_id, :{prefix}_key, :{prefix}_display_name, :{prefix}_provider_adapter_name, :{prefix}_is_active, :{prefix}_created_at, :{prefix}_updated_at)"
            )
            params[f"{prefix}_id"] = record_id
            params[f"{prefix}_key"] = key
            params[f"{prefix}_display_name"] = row["display_name"]
            params[f"{prefix}_provider_adapter_name"] = row["provider_adapter_name"]
            params[f"{prefix}_is_active"] = row.get("is_active", True)
            params[f"{prefix}_created_at"] = now
            params[f"{prefix}_updated_at"] = now

        sql = sa.text(
            f"""
            INSERT INTO llm_provider_type_definitions
                (id, key, display_name, provider_adapter_name, is_active, created_at, updated_at)
            VALUES {", ".join(values_sql)}
            ON CONFLICT (key) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                provider_adapter_name = EXCLUDED.provider_adapter_name,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at;
            """
        )

        bind.execute(sql, params)

        print("Upserted llm_provider_type_definitions defaults")
    except Exception as exc:
        print(f"Skipping provider type definition upsert: {exc}")
