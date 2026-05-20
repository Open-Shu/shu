"""Shared provisioning helpers for SHU-759 baseline runners.

Both the chat latency baseline and the pool pressure baseline need the same
local-provider model configuration and conversation. This module owns the
shared setup so the runners stay focused on their measurement logic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


async def ensure_local_provider_type(db) -> None:
    """Idempotently insert the ``local`` provider type definition.

    Dev DBs are seeded with production provider types (openai, anthropic,
    etc.) but not ``local``, even though [local_adapter.py](../../shu/services/providers/adapters/local_adapter.py)
    is registered in code. The baselines need ``local`` to bypass real LLM
    calls, so we bootstrap the row here.
    """
    existing = await db.execute(
        text("SELECT 1 FROM llm_provider_type_definitions WHERE key = :k"),
        {"k": "local"},
    )
    if existing.first():
        return

    now = datetime.now(UTC)
    await db.execute(
        text(
            "INSERT INTO llm_provider_type_definitions "
            "(id, key, display_name, provider_adapter_name, is_active, created_at, updated_at) "
            "VALUES (:id, :key, :display_name, :adapter, :is_active, :created_at, :updated_at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "key": "local",
            "display_name": "Local (test)",
            "adapter": "local",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await db.commit()
    logger.info("Bootstrapped llm_provider_type_definitions row for key='local'")


async def create_local_chat_setup(client, db, auth_headers) -> tuple[str, str]:
    """Provision a local-provider model configuration and conversation.

    Returns ``(conversation_id, model_configuration_id)``.
    """
    await ensure_local_provider_type(db)

    suffix = uuid.uuid4().hex[:8]

    provider_data = {
        "name": f"Test Baseline Local Provider {suffix}",
        "provider_type": "local",
        "api_endpoint": "http://localhost",
        "api_key": "test-baseline",
        "is_active": True,
    }
    provider_response = await client.post(
        "/api/v1/llm/providers", json=provider_data, headers=auth_headers
    )
    assert provider_response.status_code in (200, 201), provider_response.text
    provider_id = extract_data(provider_response)["id"]

    model_data = {
        "model_name": f"local-test-{suffix}",
        "display_name": "Local Test Model (baseline)",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "is_active": True,
    }
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models",
        json=model_data,
        headers=auth_headers,
    )
    assert model_response.status_code in (200, 201), model_response.text

    config_data = {
        "name": f"Test Baseline Chat Config {suffix}",
        "description": "Baseline measurement configuration",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-baseline",
    }
    config_response = await client.post(
        "/api/v1/model-configurations", json=config_data, headers=auth_headers
    )
    assert config_response.status_code in (200, 201), config_response.text
    model_config_id = extract_data(config_response)["id"]

    conversation_data = {
        "title": f"Test Baseline Conversation {suffix}",
        "model_configuration_id": model_config_id,
    }
    conv_response = await client.post(
        "/api/v1/chat/conversations", json=conversation_data, headers=auth_headers
    )
    assert conv_response.status_code in (200, 201), conv_response.text
    conversation_id = extract_data(conv_response)["id"]

    return conversation_id, model_config_id
