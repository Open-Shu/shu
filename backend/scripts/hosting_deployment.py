#!/usr/bin/env python3
"""Shu Hosted Deployment Provisioning Script.

Seeds OpenRouter LLM providers, models, and side caller configuration
for hosted deployments. Reads all configuration from environment variables
and is fully idempotent.

Environment variables:
    SHU_OPENROUTER_API_KEY     OpenRouter API key (required — skips if absent)
    SHU_LLM_ENCRYPTION_KEY     Fernet key for encrypting API keys (required)
    SHU_SEED_MODELS            JSON array of models, e.g.
                               '[{"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet"}]'
    SHU_SEED_SIDE_CALLER       Model ID to designate as the side caller
    SHU_SEED_PROFILING_MODEL   Model ID to designate for document profiling
    SHU_DATABASE_URL           PostgreSQL connection URL

Usage:
    python scripts/hosting_deployment.py seed
    python scripts/hosting_deployment.py seed --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import psycopg2
from cryptography.fernet import Fernet

LOG_PREFIX = "[hosting]"

# For quick local testing, otherwise use the `--database-url` parameter
DEFAULT_HOST = "localhost"
DEFAULT_PORT = "5432"
DEFAULT_USER = "shu"
DEFAULT_PASSWORD = "password" # pragma: allowlist secret
DEFAULT_DATABASE = "shu"

OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"

OPENROUTER_PROVIDER_CONFIG = {
    "get_api_base_url": OPENROUTER_API_BASE_URL,
    "get_capabilities": {
        "streaming": {"value": True, "label": "Supports Streaming"},
        "tools": {"value": True, "label": "Supports Tool Calling"},
        "vision": {"value": True, "label": "Supports Vision"},
    },
}

SEED_PROVIDERS = (
    {"name": "OpenAI", "provider_type": "openai"},
    {"name": "Anthropic", "provider_type": "anthropic"},
)


def _normalize_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


def _get_database_url(url_override: str | None = None) -> str:
    base_url = url_override or os.getenv("SHU_DATABASE_URL")
    if base_url:
        return _normalize_url(base_url)
    return f"postgresql://{DEFAULT_USER}:{DEFAULT_PASSWORD}@{DEFAULT_HOST}:{DEFAULT_PORT}/{DEFAULT_DATABASE}"


def _connect(url: str) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def _seed_single_provider(cur, name: str, provider_type: str, api_key_encrypted: str) -> str | None:
    """Seed a single provider row. Returns the provider ID, or None on failure."""
    cur.execute("SELECT id FROM llm_providers WHERE name = %s", (name,))
    existing = cur.fetchone()
    if existing:
        print(f"{LOG_PREFIX} Provider '{name}' already exists, skipping", flush=True)
        return existing[0]

    cur.execute(
        "SELECT key FROM llm_provider_type_definitions WHERE key = %s",
        (provider_type,),
    )
    if not cur.fetchone():
        print(
            f"{LOG_PREFIX} Provider type '{provider_type}' not found — run migrations first",
            file=sys.stderr,
        )
        return None

    provider_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO llm_providers
            (id, name, provider_type, api_key_encrypted, config, is_active,
             rate_limit_rpm, rate_limit_tpm, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, 0, 0, now(), now())
        """,
        (provider_id, name, provider_type, api_key_encrypted, json.dumps(OPENROUTER_PROVIDER_CONFIG)),
    )
    print(f"{LOG_PREFIX} Created provider '{name}' (id={provider_id})", flush=True)
    return provider_id


def _provider_id_for_model(model_id: str, provider_ids: dict[str, str]) -> str | None:
    if model_id.startswith("anthropic/"):
        return provider_ids.get("anthropic")
    return provider_ids.get("openai")


DEFAULT_FUNCTIONALITIES = {
    "supports_streaming": True,
    "supports_functions": True,
    "supports_vision": True,
}


def _parse_models(models_json: str) -> list[dict[str, str]]:
    """Parse SHU_SEED_MODELS JSON array.

    Expected format: [{"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet"}, ...]
    """
    try:
        models = json.loads(models_json)
    except json.JSONDecodeError as e:
        print(f"{LOG_PREFIX} Failed to parse SHU_SEED_MODELS as JSON: {e}", file=sys.stderr)
        return []

    if not isinstance(models, list):
        print(f"{LOG_PREFIX} SHU_SEED_MODELS must be a JSON array", file=sys.stderr)
        return []

    return models


def _ensure_llm_model(cur, model_id: str, provider_id: str) -> None:
    """Ensure an llm_models row exists for the given model."""
    cur.execute(
        "SELECT id FROM llm_models WHERE model_name = %s AND provider_id = %s",
        (model_id, provider_id),
    )
    if cur.fetchone():
        print(f"{LOG_PREFIX} Model '{model_id}' already exists, skipping", flush=True)
        return

    display_name = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    model_row_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO llm_models
            (id, provider_id, model_name, display_name, model_type,
             supports_streaming, supports_functions, supports_vision,
             is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'chat', true, true, true, true, now(), now())
        """,
        (model_row_id, provider_id, model_id, display_name),
    )
    print(f"{LOG_PREFIX} Created model '{model_id}'", flush=True)


def _seed_models(cur, models_json: str, provider_ids: dict[str, str]) -> None:
    """Seed llm_models rows and model_configurations for user-facing models.

    Models that are designated as side caller or profiling get an llm_models row
    but no model_configurations row — those are created by _seed_designated_model.
    """
    models = _parse_models(models_json)
    if not models:
        return

    for entry in models:
        model_id = entry.get("id", "").strip()
        display_name = entry.get("name", "").strip()

        if not model_id:
            print(f"{LOG_PREFIX} Model entry missing 'id', skipping: {entry}", flush=True)
            continue
        if not display_name:
            print(f"{LOG_PREFIX} Model entry missing 'name', skipping: {entry}", flush=True)
            continue

        provider_id = _provider_id_for_model(model_id, provider_ids)
        if not provider_id:
            print(f"{LOG_PREFIX} No provider for model '{model_id}', skipping", flush=True)
            continue

        _ensure_llm_model(cur, model_id, provider_id)

        # Seed model_configurations row for user-facing models only
        cur.execute(
            "SELECT id FROM model_configurations WHERE name = %s AND is_active = true",
            (display_name,),
        )
        if cur.fetchone():
            print(f"{LOG_PREFIX} Model configuration '{display_name}' already exists, skipping", flush=True)
            continue

        config_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO model_configurations
                (id, name, llm_provider_id, model_name,
                 is_active, created_by, functionalities, created_at, updated_at)
            VALUES (%s, %s, %s, %s, true, %s, %s, now(), now())
            """,
            (config_id, display_name, provider_id, model_id, "system", json.dumps(DEFAULT_FUNCTIONALITIES)),
        )
        print(f"{LOG_PREFIX} Created model configuration '{display_name}' (id={config_id})", flush=True)


def _seed_designated_model(
    cur,
    model_id: str,
    provider_ids: dict[str, str],
    config_name: str,
    description: str,
    settings_key: str,
    extra_functionalities: dict[str, bool],
) -> None:
    """Create a dedicated model configuration and register it in system_settings."""
    cur.execute("SELECT id FROM model_configurations WHERE name = %s AND is_active = true", (config_name,))
    existing = cur.fetchone()
    if existing:
        print(f"{LOG_PREFIX} '{config_name}' config already exists, skipping", flush=True)
        return

    provider_id = _provider_id_for_model(model_id, provider_ids)
    if not provider_id:
        print(f"{LOG_PREFIX} No provider for '{config_name}' model '{model_id}', skipping", flush=True)
        return

    _ensure_llm_model(cur, model_id, provider_id)

    config_id = str(uuid.uuid4())
    functionalities = {**DEFAULT_FUNCTIONALITIES, **extra_functionalities}
    cur.execute(
        """
        INSERT INTO model_configurations
            (id, name, description, llm_provider_id, model_name,
             is_active, created_by, functionalities, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, %s, %s, now(), now())
        """,
        (config_id, config_name, description, provider_id, model_id, "system", json.dumps(functionalities)),
    )
    print(f"{LOG_PREFIX} Created model configuration '{config_name}' (id={config_id})", flush=True)

    setting_value = json.dumps({"model_config_id": config_id, "updated_by": "system"})
    cur.execute(
        """
        INSERT INTO system_settings (key, value, created_at, updated_at)
        VALUES (%s, %s, now(), now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (settings_key, setting_value),
    )
    print(f"{LOG_PREFIX} Registered '{config_name}' in system_settings", flush=True)


def run(url: str) -> bool:
    """Seed OpenRouter providers, models, side caller, and profiling model. Idempotent."""
    api_key = os.getenv("SHU_OPENROUTER_API_KEY")
    encryption_key = os.getenv("SHU_LLM_ENCRYPTION_KEY")
    models_json = os.getenv("SHU_SEED_MODELS", "")
    side_caller_model = os.getenv("SHU_SEED_SIDE_CALLER", "")
    profiling_model = os.getenv("SHU_SEED_PROFILING_MODEL", "")

    if not api_key:
        print(f"{LOG_PREFIX} SHU_OPENROUTER_API_KEY not set, skipping", flush=True)
        return True

    if not encryption_key:
        print(f"{LOG_PREFIX} SHU_LLM_ENCRYPTION_KEY is required", file=sys.stderr)
        return False

    fernet = Fernet(encryption_key.encode())
    api_key_encrypted = fernet.encrypt(api_key.encode()).decode()

    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            provider_ids: dict[str, str] = {}
            for spec in SEED_PROVIDERS:
                pid = _seed_single_provider(cur, spec["name"], spec["provider_type"], api_key_encrypted)
                if pid is None:
                    conn.rollback()
                    return False
                provider_ids[spec["provider_type"]] = pid

            if models_json:
                _seed_models(cur, models_json, provider_ids)

            if side_caller_model:
                _seed_designated_model(
                    cur,
                    side_caller_model,
                    provider_ids,
                    config_name="Side Caller",
                    description="This model will periodically assist the user with performing background tasks.",
                    settings_key="side_call_model_config_id",
                    extra_functionalities={"side_call": True},
                )

            if profiling_model:
                _seed_designated_model(
                    cur,
                    profiling_model,
                    provider_ids,
                    config_name="Profiling Model",
                    description="This model will be used for ingest time intelligence profiling of KB documents.",
                    settings_key="profiling_model_config_id",
                    extra_functionalities={"profiling": True},
                )

        conn.commit()
        print(f"{LOG_PREFIX} Hosting deployment seed complete", flush=True)
        return True
    except Exception as e:
        conn.rollback()
        print(f"{LOG_PREFIX} Error: {e}", file=sys.stderr)
        return False
    finally:
        conn.close()


def cmd_seed(url: str) -> bool:
    """Seed providers, models, side caller, and profiling model from environment variables."""
    return run(url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shu Hosted Deployment Provisioning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  seed                   Seed OpenRouter providers, models, side caller, and profiling model

Environment variables (for seed):
  SHU_OPENROUTER_API_KEY     OpenRouter API key (skips if absent)
  SHU_LLM_ENCRYPTION_KEY     Fernet encryption key (required when API key is set)
  SHU_SEED_MODELS            JSON array: [{"id": "model-id", "name": "Display Name"}, ...]
  SHU_SEED_SIDE_CALLER       Model ID for the side caller
  SHU_SEED_PROFILING_MODEL   Model ID for document profiling
  SHU_DATABASE_URL           PostgreSQL connection URL

Examples:
  python scripts/hosting_deployment.py seed
  python scripts/hosting_deployment.py seed --database-url [CONNECTION_STRING]
        """,
    )

    parser.add_argument(
        "command",
        choices=["seed"],
        metavar="COMMAND",
        help="Command to run",
    )
    parser.add_argument(
        "--database-url",
        metavar="URL",
        help="PostgreSQL connection URL (overrides SHU_DATABASE_URL)",
    )

    args = parser.parse_args()
    url = _get_database_url(args.database_url)

    if args.command == "seed":
        success = cmd_seed(url)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
