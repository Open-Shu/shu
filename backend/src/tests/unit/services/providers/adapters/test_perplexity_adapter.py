import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.perplexity_adapter import PerplexityAdapter


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-perplexity",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def perplexity_adapter(mock_db_session, mock_provider):
    return PerplexityAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(perplexity_adapter):
    info = perplexity_adapter.get_provider_information()
    assert info.key == "perplexity"
    assert info.display_name == "Perplexity"

    capabilities = perplexity_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is False
    assert capabilities.vision is False

    assert perplexity_adapter.get_api_base_url() == "https://api.perplexity.ai"
    assert perplexity_adapter.get_chat_endpoint() == "/chat/completions"
    assert perplexity_adapter.get_models_endpoint() == "/models"
    assert perplexity_adapter.get_model_information_path() == "data[*].{id: id, name: id}"

    authorization_header = perplexity_adapter.get_authorization_header()
    assert authorization_header.get("headers", {}).get("Authorization") == f"Bearer {None}"

    parameter_mapping = perplexity_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)
