import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.digitalocean_responses_adapter import (
    DigitalOceanResponsesAdapter,
)


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-digitalocean",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def digitalocean_adapter(mock_db_session, mock_provider):
    return DigitalOceanResponsesAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(digitalocean_adapter):
    info = digitalocean_adapter.get_provider_information()
    assert info.key == "digitalocean"
    assert info.display_name == "DigitalOcean"

    capabilities = digitalocean_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is True

    assert digitalocean_adapter.supports_native_documents() is True
    assert digitalocean_adapter.get_api_base_url() == "https://inference.do-ai.run/v1"
    assert digitalocean_adapter.get_chat_endpoint() == "/responses"
    assert digitalocean_adapter.get_models_endpoint() == "/models"

    # Set a stand-in api_key directly — the fixture builds the adapter
    # without an encrypted provider key, so the constructor leaves
    # self.api_key=None. We want to test the auth-header format under
    # realistic conditions (a real key reaches the wire as "Bearer <key>"),
    # not the degenerate "Bearer None" shape.
    digitalocean_adapter.api_key = "fake-key"

    authorization_header = digitalocean_adapter.get_authorization_header()
    assert authorization_header.get("scheme") == "bearer"
    assert authorization_header.get("headers", {}).get("Authorization") == "Bearer fake-key"

    parameter_mapping = digitalocean_adapter.get_parameter_mapping()
    assert set(parameter_mapping.keys()) == {
        "temperature",
        "top_p",
        "max_output_tokens",
        "reasoning",
        "parallel_tool_calls",
        "tool_choice",
        "int:web_search",
    }
    reasoning_props = parameter_mapping["reasoning"].properties
    assert {option.value for option in reasoning_props["effort"].options} == {"low", "medium", "high"}
    assert {option.value for option in reasoning_props["summary"].options} == {"concise", "detailed"}
    assert {option.value for option in parameter_mapping["tool_choice"].options} == {"auto", "none"}
