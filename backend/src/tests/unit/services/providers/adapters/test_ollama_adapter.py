import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.ollama_adapter import OllamaAdapter


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-ollama",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def ollama_adapter(mock_db_session, mock_provider):
    return OllamaAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(ollama_adapter):
    info = ollama_adapter.get_provider_information()
    assert info.key == "ollama"
    assert info.display_name == "Ollama"

    capabilities = ollama_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is False

    assert ollama_adapter.get_api_base_url() == "http://localhost:11434/v1"
    assert ollama_adapter.get_chat_endpoint() == "/chat/completions"
    assert ollama_adapter.get_models_endpoint() == "/models"
    assert ollama_adapter.get_model_information_path() == "data[*].{id: id, name: id}"

    authorization_header = ollama_adapter.get_authorization_header()
    assert authorization_header.get("scheme") is None
    assert authorization_header.get("headers") == {}

    parameter_mapping = ollama_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)
