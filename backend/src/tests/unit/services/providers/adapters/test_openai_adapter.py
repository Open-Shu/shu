import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.openai_adapter import OpenAIAdapter


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-openai",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def openai_adapter(mock_db_session, mock_provider):
    return OpenAIAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(openai_adapter):
    info = openai_adapter.get_provider_information()
    assert info.key == "openai"
    assert info.display_name == "OpenAI"

    capabilities = openai_adapter.get_capabilities()
    assert capabilities.streaming == True
    assert capabilities.tools == True
    assert capabilities.vision == True

    assert openai_adapter.get_api_base_url() == "https://api.openai.com/v1"
    assert openai_adapter.get_chat_endpoint() == "/responses"
    assert openai_adapter.get_models_endpoint() == "/models"
    assert openai_adapter.get_model_information_path() == "data[*].{id: id, name: id}"

    authorization_header = openai_adapter.get_authorization_header()
    assert (
        authorization_header.get("headers", {}).get("Authorization") == f"Bearer {None}"
    )  # We're not testing decryption

    # TODO: We'll want to evaluate the mapping in the future to ensure it is valid.
    parameter_mapping = openai_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)
