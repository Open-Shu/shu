#!/usr/bin/env python3
"""Shu Hosted Deployment Provisioning Script.

Seeds OpenRouter LLM providers, models, and side caller configuration
for hosted deployments. Reads all configuration from environment variables
and is fully idempotent.

Environment variables:
    SHU_OPENROUTER_API_KEY     OpenRouter API key (required — skips if absent)
    SHU_SEED_MODELS            JSON array of models, e.g.
                               '[{"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet"}]'
    SHU_SEED_SIDE_CALLER       Model ID to designate as the side caller
    SHU_SEED_PROFILING_MODEL   Model ID to designate for document profiling
    SHU_SEED_EMBEDDING_MODEL   Embedding model ID and dimension, e.g. "qwen/qwen3-embedding-8b:4096"
    SHU_SEED_OCR_MODEL         OCR model name (default: "mistral-ocr-latest")

Usage (from the backend/ directory):
    python scripts/hosting_deployment.py seed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import psycopg2
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

LOG_PREFIX = "[hosting]"

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


def _connect() -> psycopg2.extensions.connection:
    url = _normalize_url(os.environ["SHU_DATABASE_URL"])
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
             is_system_managed, rate_limit_rpm, rate_limit_tpm, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, true, true, 0, 0, now(), now())
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
    """Parse and validate SHU_SEED_MODELS JSON array.

    Expected format: [{"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet"}, ...]
    Exits non-zero on malformed input so bad config is never silently ignored.
    """
    try:
        models = json.loads(models_json)
    except json.JSONDecodeError as e:
        print(f"{LOG_PREFIX} Failed to parse SHU_SEED_MODELS as JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(models, list):
        print(f"{LOG_PREFIX} SHU_SEED_MODELS must be a JSON array, got {type(models).__name__}", file=sys.stderr)
        sys.exit(1)

    for i, entry in enumerate(models):
        if not isinstance(entry, dict):
            print(f"{LOG_PREFIX} SHU_SEED_MODELS[{i}] must be an object, got {type(entry).__name__}", file=sys.stderr)
            sys.exit(1)
        model_id = entry.get("id")
        name = entry.get("name")
        if not isinstance(model_id, str) or not model_id.strip():
            print(f"{LOG_PREFIX} SHU_SEED_MODELS[{i}] missing or empty 'id': {entry}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(name, str) or not name.strip():
            print(f"{LOG_PREFIX} SHU_SEED_MODELS[{i}] missing or empty 'name': {entry}", file=sys.stderr)
            sys.exit(1)

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
        model_id = entry["id"].strip()
        display_name = entry["name"].strip()

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


def _seed_embedding_model(
    cur,
    embedding_spec: str,
    provider_ids: dict[str, str],
    query_prefix: str = "",
    document_prefix: str = "",
) -> None:
    """Seed an embedding model row in llm_models.

    Uses the OpenAI provider since OpenRouter's embeddings endpoint
    follows the OpenAI format regardless of the underlying model vendor.

    Args:
        embedding_spec: "model_id:dimension" e.g. "qwen/qwen3-embedding-8b:4096"
        query_prefix: Prefix prepended to query texts before embedding.
        document_prefix: Prefix prepended to document texts before embedding.
    """
    if ":" not in embedding_spec:
        print(
            f"{LOG_PREFIX} SHU_SEED_EMBEDDING_MODEL must be 'model_id:dimension' "
            f"(e.g. 'qwen/qwen3-embedding-8b:4096'), got: {embedding_spec}",
            file=sys.stderr,
        )
        sys.exit(1)

    model_id, dim_str = embedding_spec.rsplit(":", 1)
    model_id = model_id.strip()
    if not model_id:
        print(f"{LOG_PREFIX} SHU_SEED_EMBEDDING_MODEL has empty model_id: {embedding_spec}", file=sys.stderr)
        sys.exit(1)

    try:
        dimension = int(dim_str.strip())
    except ValueError:
        print(f"{LOG_PREFIX} Invalid dimension '{dim_str}' in SHU_SEED_EMBEDDING_MODEL", file=sys.stderr)
        sys.exit(1)

    if dimension <= 0:
        print(f"{LOG_PREFIX} Dimension must be positive, got {dimension}", file=sys.stderr)
        sys.exit(1)

    provider_id = provider_ids.get("openai")
    if not provider_id:
        print(f"{LOG_PREFIX} OpenAI provider not found, cannot seed embedding model", flush=True)
        return

    cur.execute(
        "SELECT id FROM llm_models WHERE model_name = %s AND provider_id = %s",
        (model_id, provider_id),
    )
    if cur.fetchone():
        print(f"{LOG_PREFIX} Embedding model '{model_id}' already exists, skipping", flush=True)
        return

    display_name = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    model_row_id = str(uuid.uuid4())
    config = {"dimension": dimension}
    if query_prefix:
        config["query_prefix"] = query_prefix
    if document_prefix:
        config["document_prefix"] = document_prefix
    model_config = json.dumps(config)
    cur.execute(
        """
        INSERT INTO llm_models
            (id, provider_id, model_name, display_name, model_type,
             supports_streaming, supports_functions, supports_vision,
             is_active, config, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'embedding', false, false, false, true, %s, now(), now())
        """,
        (model_row_id, provider_id, model_id, display_name, model_config),
    )
    print(f"{LOG_PREFIX} Created embedding model '{model_id}' (dimension={dimension})", flush=True)


def _seed_ocr_model(cur, model_id: str, provider_ids: dict[str, str]) -> None:
    """Seed an OCR model row in llm_models.

    Uses the OpenAI provider since the Mistral OCR API key is routed
    through the same OpenRouter account.
    """
    provider_id = provider_ids.get("openai")
    if not provider_id:
        print(f"{LOG_PREFIX} OpenAI provider not found, cannot seed OCR model", flush=True)
        return

    cur.execute(
        "SELECT id FROM llm_models WHERE model_name = %s AND provider_id = %s",
        (model_id, provider_id),
    )
    if cur.fetchone():
        print(f"{LOG_PREFIX} OCR model '{model_id}' already exists, skipping", flush=True)
        return

    display_name = "Mistral OCR"
    model_row_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO llm_models
            (id, provider_id, model_name, display_name, model_type,
             supports_streaming, supports_functions, supports_vision,
             is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'ocr', false, false, false, true, now(), now())
        """,
        (model_row_id, provider_id, model_id, display_name),
    )
    print(f"{LOG_PREFIX} Created OCR model '{model_id}'", flush=True)


def run() -> bool:
    """Seed OpenRouter providers, models, side caller, and profiling model. Idempotent."""
    api_key = os.getenv("SHU_OPENROUTER_API_KEY")
    models_json = os.getenv("SHU_SEED_MODELS", "")
    side_caller_model = os.getenv("SHU_SEED_SIDE_CALLER", "")
    profiling_model = os.getenv("SHU_SEED_PROFILING_MODEL", "")
    embedding_model = os.getenv("SHU_SEED_EMBEDDING_MODEL", "")
    embedding_query_prefix = os.getenv("SHU_SEED_EMBEDDING_QUERY_PREFIX", "")
    embedding_document_prefix = os.getenv("SHU_SEED_EMBEDDING_DOCUMENT_PREFIX", "")
    ocr_model = os.getenv("SHU_SEED_OCR_MODEL", "mistral-ocr-latest")

    if not api_key:
        print(f"{LOG_PREFIX} SHU_OPENROUTER_API_KEY not set, skipping", flush=True)
        return True

    conn = None
    try:
        fernet = Fernet(os.environ["SHU_LLM_ENCRYPTION_KEY"].encode())
        api_key_encrypted = fernet.encrypt(api_key.encode()).decode()

        conn = _connect()
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

            if embedding_model:
                _seed_embedding_model(
                    cur,
                    embedding_model,
                    provider_ids,
                    query_prefix=embedding_query_prefix,
                    document_prefix=embedding_document_prefix,
                )

            if ocr_model:
                _seed_ocr_model(cur, ocr_model, provider_ids)

        conn.commit()
        print(f"{LOG_PREFIX} Hosting deployment seed complete", flush=True)
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"{LOG_PREFIX} Error: {e}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()


def cmd_seed() -> bool:
    """Seed providers, models, side caller, and profiling model from environment variables."""
    return run()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shu Hosted Deployment Provisioning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  seed                   Seed OpenRouter providers, models, side caller, and profiling model

Environment variables (for seed):
  SHU_OPENROUTER_API_KEY     OpenRouter API key (skips if absent)
  SHU_SEED_MODELS            JSON array: [{"id": "model-id", "name": "Display Name"}, ...]
  SHU_SEED_SIDE_CALLER       Model ID for the side caller
  SHU_SEED_PROFILING_MODEL   Model ID for document profiling
  SHU_SEED_EMBEDDING_MODEL   Embedding model as "model_id:dimension" (e.g. "qwen/qwen3-embedding-8b:4096")
  SHU_SEED_OCR_MODEL         OCR model name (default: "mistral-ocr-latest")

Examples (from the backend/ directory):
  python scripts/hosting_deployment.py seed
        """,
    )

    parser.add_argument(
        "command",
        choices=["seed"],
        metavar="COMMAND",
        help="Command to run",
    )

    args = parser.parse_args()

    if args.command == "seed":
        success = cmd_seed()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
