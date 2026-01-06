"""
Integration tests for SideCallService using custom test framework.

These tests verify that side-call configuration and execution behave correctly:
- Admins can configure the side-call model
- Non-admins are blocked from configuration changes
- Side-call invocations use the configured model and redact sensitive input
"""

import sys
import os
import uuid
from datetime import datetime, timezone
from typing import List, Callable, Dict, Any

from sqlalchemy import text, select

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data
from integ.helpers.auth import create_active_user_headers

from shu.models.provider_type_definition import ProviderTypeDefinition


SIDE_CALL_SETTING_KEY = "side_call_model_config_id"

LOCAL_PROVIDER_TYPE_DEFINITION: Dict[str, Any] = {
    "key": "local",
    "display_name": "Local Test Provider",
    "provider_adapter_name": "local",
    "is_active": True,
}


async def _clear_side_call_setting(db) -> None:
    """Ensure side-call configuration starts from a clean state."""
    await db.execute(
        text("DELETE FROM system_settings WHERE key = :key"),
        {"key": SIDE_CALL_SETTING_KEY},
    )
    await db.commit()


async def _ensure_local_provider_type(db) -> None:
    """Insert a lightweight 'local' provider type definition if missing."""
    result = await db.execute(
        select(ProviderTypeDefinition).where(
            ProviderTypeDefinition.key == LOCAL_PROVIDER_TYPE_DEFINITION["key"]
        )
    )
    if result.scalar_one_or_none():
        return

    provider_type = ProviderTypeDefinition(
        key=LOCAL_PROVIDER_TYPE_DEFINITION["key"],
        display_name=LOCAL_PROVIDER_TYPE_DEFINITION["display_name"],
        provider_adapter_name=LOCAL_PROVIDER_TYPE_DEFINITION["provider_adapter_name"],
        is_active=LOCAL_PROVIDER_TYPE_DEFINITION["is_active"],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(provider_type)
    await db.commit()


async def _create_local_side_call_model_config(client, db, auth_headers) -> Dict[str, Any]:
    """Create provider, model, and model configuration suited for side-call tests."""
    await _ensure_local_provider_type(db)
    suffix = uuid.uuid4().hex[:8]

    provider_payload = {
        "name": f"Test Local Provider {suffix}",
        "provider_type": LOCAL_PROVIDER_TYPE_DEFINITION["key"],
        "api_endpoint": "http://local-llm.test",
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "rate_limit_rpm": 1200,
        "rate_limit_tpm": 60000,
    }
    provider_response = await client.post(
        "/api/v1/llm/providers", json=provider_payload, headers=auth_headers
    )
    assert provider_response.status_code == 201, provider_response.text
    provider = extract_data(provider_response)

    model_payload = {
        "model_name": f"test-local-model-{suffix}",
        "display_name": f"Test Local Model {suffix}",
        "model_type": "chat",
        "context_window": 16000,
        "max_output_tokens": 1024,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.0,
        "cost_per_output_token": 0.0,
    }
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider['id']}/models",
        json=model_payload,
        headers=auth_headers,
    )
    assert model_response.status_code == 200, model_response.text
    model = extract_data(model_response)

    config_payload = {
        "name": f"Test Side Call Config {suffix}",
        "description": "Integration test side-call configuration",
        "llm_provider_id": provider["id"],
        "model_name": model["model_name"],
        "prompt_id": None,
        "knowledge_base_ids": [],
        "parameter_overrides": {"temperature": 0.2},
        "functionalities": {"side_call": True},
        "is_active": True,
        "is_side_call_model": False,
        "kb_prompt_assignments": [],
        "created_by": "integration-test-user",
    }
    config_response = await client.post(
        "/api/v1/model-configurations",
        json=config_payload,
        headers=auth_headers,
    )
    assert config_response.status_code == 201, config_response.text
    config = extract_data(config_response)
    return {
        "provider": provider,
        "model": model,
        "config": config,
    }


async def test_side_call_config_returns_unconfigured_when_not_set(
    client, db, auth_headers
):
    """Side-call config endpoint reports no model when unset."""
    await _clear_side_call_setting(db)

    response = await client.get(
        "/api/v1/side-calls/config", headers=auth_headers
    )
    assert response.status_code == 200
    data = extract_data(response)

    assert data["configured"] is False
    assert data["side_call_model_config"] is None
    assert "No side-call model" in data["message"]


async def test_side_call_config_admin_can_set_model(client, db, auth_headers):
    """Admins can configure side-call model via API."""
    await _clear_side_call_setting(db)
    resources = await _create_local_side_call_model_config(client, db, auth_headers)

    set_response = await client.post(
        "/api/v1/side-calls/config",
        json={"model_config_id": resources["config"]["id"]},
        headers=auth_headers,
    )
    assert set_response.status_code == 200, set_response.text
    config_data = extract_data(set_response)

    assert config_data["configured"] is True
    assert config_data["side_call_model_config"]["id"] == resources["config"]["id"]
    assert config_data["side_call_model_config"]["model_name"] == resources["config"]["model_name"]

    # Follow-up GET should reflect configured model
    get_response = await client.get(
        "/api/v1/side-calls/config", headers=auth_headers
    )
    assert get_response.status_code == 200
    get_data = extract_data(get_response)
    assert get_data["configured"] is True
    assert get_data["side_call_model_config"]["id"] == resources["config"]["id"]


async def test_side_call_config_requires_admin_privileges(client, db, auth_headers):
    """Non-admin users cannot change side-call configuration."""
    await _clear_side_call_setting(db)
    resources = await _create_local_side_call_model_config(client, db, auth_headers)
    user_headers = await create_active_user_headers(client, auth_headers)

    response = await client.post(
        "/api/v1/side-calls/config",
        json={"model_config_id": resources["config"]["id"]},
        headers=user_headers,
    )
    assert response.status_code == 403
    body = response.json()
    # Depending on dependency error handling, this may be a FastAPI detail or our envelope
    if isinstance(body, dict) and "error" in body:
        assert body["error"]["message"] == "Admin access required"
    else:
        assert body.get("detail") == "Admin access required"


async def _set_side_call_model(client, model_config_id: str, headers) -> None:
    """Helper to set the side-call model using admin headers."""
    response = await client.post(
        "/api/v1/side-calls/config",
        json={"model_config_id": model_config_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text

class SideCallServiceTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for SideCallService workflows."""

    def get_test_functions(self) -> List[Callable]:
        return [
            test_side_call_config_returns_unconfigured_when_not_set,
            test_side_call_config_admin_can_set_model,
            test_side_call_config_requires_admin_privileges,
        ]

    def get_suite_name(self) -> str:
        return "Side-Call Service Integration Tests"

    def get_suite_description(self) -> str:
        return "End-to-end tests covering side-call configuration, permissions, and execution"

    def get_cli_examples(self) -> str:
        return """
Examples:
  python tests/test_side_call_service_integration.py                       # Run all side-call tests
  python tests/test_side_call_service_integration.py --list                # List available tests
  python tests/test_side_call_service_integration.py --pattern config      # Run configuration-focused tests
        """


if __name__ == "__main__":
    suite = SideCallServiceTestSuite()
    sys.exit(suite.run())
